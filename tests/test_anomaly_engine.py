"""Physics and statistical verification tests for FlightAnalyzer and Comparator."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.analysis.anomaly_engine import FlightAnalyzer
from backend.analysis.comparator import compare_flights
from backend.parsers.base import BaseFlightParser, FlightMetadata, ParsedFlight
from backend.parsers.registry import default_registry


def _make_synthetic_df(
    fs: float = 100.0,
    duration_s: float = 20.0,
    r_int_ohm: float = 0.05,
    vibe_freq_hz: float = 35.0,
    vibe_amp: float = 4.0,
    roll_lag_samples: int = 0,
    inject_gps_jump: bool = False,
) -> ParsedFlight:
    t = np.arange(0.0, duration_s, 1.0 / fs)
    n = len(t)
    current = 12.0 + 14.0 * (np.sin(0.6 * t) > 0).astype(float)
    q_ah = np.cumsum(current / fs) / 3600.0
    voltage = 16.6 - 0.9 * q_ah - current * r_int_ohm

    vibe_x = 4.0 + vibe_amp * np.sin(2.0 * math.pi * vibe_freq_hz * t)
    vibe_y = 4.0 + 0.5 * vibe_amp * np.cos(2.0 * math.pi * vibe_freq_hz * t)
    vibe_z = np.full(n, 6.0)

    roll_des = 10.0 * np.sin(1.2 * t)
    roll = np.roll(roll_des, roll_lag_samples)
    if roll_lag_samples > 0:
        roll[:roll_lag_samples] = roll_des[:roll_lag_samples]

    lat = np.full(n, 12.9716) + 0.00001 * np.arange(n) / fs
    lon = np.full(n, 77.5946) + 0.00001 * np.arange(n) / fs
    if inject_gps_jump:
        lat[n // 2 : n // 2 + 5] += 0.00045  # ~50 meter jump

    raw = pd.DataFrame(
        {
            "timestamp_s": t,
            "voltage_v": voltage,
            "current_a": current,
            "roll_deg": roll,
            "roll_des_deg": roll_des,
            "pitch_deg": np.zeros(n),
            "pitch_des_deg": np.zeros(n),
            "yaw_deg": np.zeros(n),
            "yaw_des_deg": np.zeros(n),
            "vibe_x": vibe_x,
            "vibe_y": vibe_y,
            "vibe_z": vibe_z,
            "clip_count": np.zeros(n),
            "gps_lat": lat,
            "gps_lon": lon,
            "gps_alt_m": np.full(n, 930.0),
            "gps_hdop": np.full(n, 0.9),
            "gps_nsats": np.full(n, 14),
            "gps_speed_ms": np.full(n, 3.0),
            "baro_alt_m": np.full(n, 20.0),
            "throttle_pct": np.full(n, 50.0),
            "flight_mode": ["AUTO"] * n,
        }
    )
    final_df = BaseFlightParser.finalize_dataframe(raw)
    meta = FlightMetadata(format_id="test", autopilot="TestPilot", is_synthetic=True)
    return ParsedFlight(metadata=meta, dataframe=final_df)


def test_r_int_and_fft_physics_recovery() -> None:
    # Known R_int = 0.140 Ohm (140 mOhm) and 38 Hz vibration harmonic (amp=40 -> ~28.6 m/s2 axis RMS)
    flight = _make_synthetic_df(fs=100.0, duration_s=20.0, r_int_ohm=0.140, vibe_freq_hz=38.0, vibe_amp=40.0)
    res = FlightAnalyzer().analyze(flight)
    s = res.summary

    assert s.estimated_cell_count == 4
    assert s.internal_resistance_mohm == pytest.approx(140.0, abs=3.0)
    assert s.r_int_r2 is not None and s.r_int_r2 > 0.98
    assert s.dominant_vibe_freq_hz == pytest.approx(38.0, abs=1.0)
    assert any(a.code == "ELEVATED_RINT" for a in res.anomalies)
    assert any(a.code == "HIGH_VIBRATION" for a in res.anomalies)


def test_pid_phase_lag_and_gps_jump_detection() -> None:
    # 20 samples lag at 100 Hz = 200 ms phase lag + 50m GPS jump
    flight = _make_synthetic_df(fs=100.0, duration_s=20.0, roll_lag_samples=20, inject_gps_jump=True)
    res = FlightAnalyzer().analyze(flight)

    assert res.summary.roll_lag_ms == pytest.approx(200.0, abs=20.0)
    assert res.summary.gps_jump_count >= 1
    codes = {a.code for a in res.anomalies}
    assert "PID_PHASE_LAG" in codes
    assert "GPS_POSITION_JUMP" in codes


def test_sample_logs_health_score_separation(sample_data_dir: Path) -> None:
    analyzer = FlightAnalyzer()
    nom = analyzer.analyze(
        default_registry.parse_bytes(
            (sample_data_dir / "synthetic_nominal_survey_flight.csv").read_bytes(),
            filename="synthetic_nominal_survey_flight.csv",
        )
    )
    sag = analyzer.analyze(
        default_registry.parse_bytes(
            (sample_data_dir / "synthetic_voltage_sag_vibration_anomaly.csv").read_bytes(),
            filename="synthetic_voltage_sag_vibration_anomaly.csv",
        )
    )
    gps_att = analyzer.analyze(
        default_registry.parse_bytes(
            (sample_data_dir / "synthetic_gps_glitch_attitude_instability.ulog.json").read_bytes(),
            filename="synthetic_gps_glitch_attitude_instability.ulog.json",
        )
    )

    assert nom.summary.flight_status == "NOMINAL"
    assert nom.summary.health_score >= 92.0
    assert len(nom.anomalies) == 0

    assert sag.summary.flight_status == "CRITICAL"
    assert sag.summary.health_score < nom.summary.health_score - 20.0
    assert sag.summary.dominant_vibe_freq_hz == pytest.approx(78.0, abs=1.0)
    assert sag.summary.total_clip_count > 0

    assert gps_att.summary.flight_status == "CRITICAL"
    assert gps_att.summary.oscillation_freq_hz == pytest.approx(4.2, abs=0.4)
    assert gps_att.summary.gps_hdop_max is not None and gps_att.summary.gps_hdop_max > 4.0


def test_multi_flight_comparator(sample_data_dir: Path) -> None:
    analyzer = FlightAnalyzer()
    records = []
    for idx, fname in enumerate(
        ["synthetic_nominal_survey_flight.csv", "synthetic_voltage_sag_vibration_anomaly.csv"], start=1
    ):
        p = sample_data_dir / fname
        parsed = default_registry.parse_bytes(p.read_bytes(), filename=fname)
        res = analyzer.analyze(parsed)
        records.append(
            {
                "id": idx,
                "name": fname,
                "autopilot": parsed.metadata.autopilot,
                "is_synthetic": True,
                "summary": res.summary.to_dict(),
                "anomalies": [a.to_dict() for a in res.anomalies],
                "telemetry": parsed.dataframe.to_dict(orient="records"),
            }
        )

    comp = compare_flights(records, n_points=50)
    assert comp.baseline_flight_id == 1
    assert len(comp.flights) == 2
    assert len(comp.flights[0].aligned_progress_pct) == 50
    health_delta = next(d for d in comp.metric_deltas if d.metric == "health_score")
    assert health_delta.deltas_from_baseline[2] is not None
    assert health_delta.deltas_from_baseline[2] < -15.0
    assert len(comp.insights) >= 2
