"""Physics and statistical anomaly detection engine for UAV flight telemetry."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import fft as scipy_fft
from scipy import signal as scipy_signal

from backend.parsers.base import ParsedFlight


EARTH_RADIUS_M = 6_371_000.0


@dataclass
class DetectedAnomaly:
    """Single detected physical or statistical anomaly interval."""

    anomaly_id: str
    category: str  # power, vibration, attitude, navigation
    code: str
    severity: str  # info, warning, critical
    title: str
    description: str
    start_time_s: float
    end_time_s: float
    peak_time_s: float
    peak_value: float
    threshold_value: float
    unit: str
    recommendation: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TimelineEvent:
    """Unified flight timeline event (flight phase, mode transition, or anomaly)."""

    timestamp_s: float
    end_time_s: float | None
    event_type: str  # takeoff, landing, mode_change, anomaly, autopilot_msg
    category: str
    severity: str
    title: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FlightHealthSummary:
    """Quantitative telemetry summary and subsystem health scorecard."""

    health_score: float
    health_grade: str
    flight_status: str
    power_score: float
    vibration_score: float
    attitude_score: float
    navigation_score: float

    duration_s: float
    sample_count: int
    sample_rate_hz: float
    max_altitude_m: float
    max_groundspeed_ms: float
    total_distance_m: float

    # Power / Battery
    estimated_cell_count: int
    voltage_min_v: float | None
    voltage_max_v: float | None
    voltage_mean_v: float | None
    current_max_a: float | None
    current_mean_a: float | None
    internal_resistance_ohm: float | None
    internal_resistance_mohm: float | None
    r_int_per_cell_mohm: float | None
    r_int_r2: float | None
    max_voltage_sag_v: float | None

    # Vibration & FFT
    vibe_rms_x: float | None
    vibe_rms_y: float | None
    vibe_rms_z: float | None
    vibe_rms_total: float | None
    vibe_peak_total: float | None
    total_clip_count: int
    dominant_vibe_freq_hz: float | None
    dominant_vibe_amp: float | None

    # Attitude / PID
    roll_rms_error_deg: float | None
    pitch_rms_error_deg: float | None
    roll_max_error_deg: float | None
    pitch_max_error_deg: float | None
    roll_lag_ms: float | None
    pitch_lag_ms: float | None
    oscillation_freq_hz: float | None

    # Navigation / GPS
    gps_hdop_mean: float | None
    gps_hdop_max: float | None
    gps_nsats_min: int | None
    gps_nsats_mean: float | None
    gps_jump_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnomalyDetectionResult:
    """Complete analysis output including health summary, spectra, anomalies, and timeline."""

    summary: FlightHealthSummary
    anomalies: list[DetectedAnomaly]
    events: list[TimelineEvent]
    fft_spectrum: dict[str, list[float]]  # {"frequencies_hz": [...], "amplitudes": [...]}
    derived_series: dict[str, list[float | None]]  # e.g., v_oc_est, roll_err, pitch_err, vibe_mag

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "anomalies": [a.to_dict() for a in self.anomalies],
            "events": [e.to_dict() for e in self.events],
            "fft_spectrum": self.fft_spectrum,
            "derived_series": self.derived_series,
        }


def _haversine_m(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2.0) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * np.arcsin(np.minimum(1.0, np.sqrt(a)))


def _find_contiguous_windows(
    times: np.ndarray, mask: np.ndarray, values: np.ndarray, min_duration_s: float = 0.0
) -> list[tuple[int, int]]:
    """Return inclusive (start_idx, end_idx) intervals where boolean mask is True."""
    windows: list[tuple[int, int]] = []
    n = len(mask)
    i = 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and mask[j + 1]:
            j += 1
        if times[j] - times[i] >= min_duration_s:
            windows.append((i, j))
        i = j + 1
    return windows


def _infer_lipo_cells(voltages: np.ndarray, hint: int | None = None) -> int:
    if hint and hint > 0:
        return int(hint)
    valid = voltages[np.isfinite(voltages) & (voltages > 2.5)]
    if len(valid) == 0:
        return 4
    v_top = float(np.percentile(valid, 98))
    candidates = [1, 2, 3, 4, 6, 8, 12, 14]
    best_s = 4
    best_err = float("inf")
    for s in candidates:
        per_cell = v_top / s
        if 3.45 <= per_cell <= 4.35:
            err = abs(per_cell - 4.16)
            if err < best_err:
                best_err = err
                best_s = s
    if best_err == float("inf"):
        best_s = max(1, round(v_top / 4.1))
    return int(best_s)


class FlightAnalyzer:
    """Runs physics and statistical diagnostics on a canonical ParsedFlight."""

    def analyze(self, flight: ParsedFlight) -> AnomalyDetectionResult:
        df = flight.dataframe
        t = df["timestamp_s"].to_numpy(dtype=float)
        duration_s = float(t[-1] - t[0]) if len(t) > 1 else 0.0
        sample_rate_hz = flight.sample_rate_hz

        anomalies: list[DetectedAnomaly] = []
        counter = 0

        def next_id(prefix: str) -> str:
            nonlocal counter
            counter += 1
            return f"{prefix}-{counter:03d}"

        # 1. Power / Battery Analysis
        power_metrics, power_anomalies, v_oc_est = self._analyze_power(
            df, t, flight.metadata.cell_count_hint, next_id
        )
        anomalies.extend(power_anomalies)

        # 2. Vibration & SciPy FFT Analysis
        vibe_metrics, vibe_anomalies, fft_spectrum, vibe_mag = self._analyze_vibration(
            df, t, sample_rate_hz, next_id
        )
        anomalies.extend(vibe_anomalies)

        # 3. Attitude PID Tracking, Cross-Correlation Lag & Oscillation Analysis
        att_metrics, att_anomalies, roll_err, pitch_err = self._analyze_attitude(
            df, t, sample_rate_hz, next_id
        )
        anomalies.extend(att_anomalies)

        # 4. Navigation / GPS Integrity Analysis
        nav_metrics, nav_anomalies, total_distance_m = self._analyze_navigation(df, t, next_id)
        anomalies.extend(nav_anomalies)

        # Kinematics summary
        alt_series = df["baro_alt_m"] if df["baro_alt_m"].notna().any() else df["gps_alt_m"]
        max_alt_m = float(alt_series.max()) if alt_series.notna().any() else 0.0
        if alt_series.notna().any() and float(alt_series.min()) < 0 and max_alt_m > 0:
            max_alt_m = float(alt_series.max() - alt_series.min())
        max_spd_ms = (
            float(df["gps_speed_ms"].max()) if df["gps_speed_ms"].notna().any() else 0.0
        )

        # 5. Composite Flight Health Score (0-100)
        p_score = self._score_power(power_metrics, power_anomalies)
        v_score = self._score_vibration(vibe_metrics, vibe_anomalies)
        a_score = self._score_attitude(att_metrics, att_anomalies)
        n_score = self._score_navigation(nav_metrics, nav_anomalies)

        composite = round(0.25 * p_score + 0.25 * v_score + 0.25 * a_score + 0.25 * n_score, 1)
        composite = float(max(0.0, min(100.0, composite)))

        if composite >= 90.0:
            grade = "A"
        elif composite >= 78.0:
            grade = "B"
        elif composite >= 62.0:
            grade = "C"
        elif composite >= 45.0:
            grade = "D"
        else:
            grade = "F"

        has_critical = any(a.severity == "critical" for a in anomalies)
        if has_critical or composite < 62.0:
            status = "CRITICAL"
        elif anomalies or composite < 85.0:
            status = "CAUTION"
        else:
            status = "NOMINAL"

        summary = FlightHealthSummary(
            health_score=composite,
            health_grade=grade,
            flight_status=status,
            power_score=round(p_score, 1),
            vibration_score=round(v_score, 1),
            attitude_score=round(a_score, 1),
            navigation_score=round(n_score, 1),
            duration_s=round(duration_s, 3),
            sample_count=int(len(df)),
            sample_rate_hz=round(sample_rate_hz, 2),
            max_altitude_m=round(max_alt_m, 2),
            max_groundspeed_ms=round(max_spd_ms, 2),
            total_distance_m=round(total_distance_m, 2),
            **power_metrics,
            **vibe_metrics,
            **att_metrics,
            **nav_metrics,
        )

        events = self._build_timeline_events(flight, df, t, anomalies)

        derived_series = {
            "v_oc_est": [None if not np.isfinite(x) else round(float(x), 3) for x in v_oc_est],
            "vibe_mag": [None if not np.isfinite(x) else round(float(x), 3) for x in vibe_mag],
            "roll_err_deg": [None if not np.isfinite(x) else round(float(x), 3) for x in roll_err],
            "pitch_err_deg": [None if not np.isfinite(x) else round(float(x), 3) for x in pitch_err],
        }

        anomalies.sort(key=lambda a: a.start_time_s)
        return AnomalyDetectionResult(
            summary=summary,
            anomalies=anomalies,
            events=events,
            fft_spectrum=fft_spectrum,
            derived_series=derived_series,
        )

    def _analyze_power(
        self,
        df: pd.DataFrame,
        t: np.ndarray,
        cell_hint: int | None,
        next_id: Any,
    ) -> tuple[dict[str, Any], list[DetectedAnomaly], np.ndarray]:
        v = df["voltage_v"].to_numpy(dtype=float)
        c = df["current_a"].to_numpy(dtype=float)
        n = len(t)
        v_oc_est = np.full(n, np.nan, dtype=float)
        anomalies: list[DetectedAnomaly] = []

        valid_v = np.isfinite(v)
        if not np.any(valid_v):
            return (
                {
                    "estimated_cell_count": cell_hint or 4,
                    "voltage_min_v": None,
                    "voltage_max_v": None,
                    "voltage_mean_v": None,
                    "current_max_a": None,
                    "current_mean_a": None,
                    "internal_resistance_ohm": None,
                    "internal_resistance_mohm": None,
                    "r_int_per_cell_mohm": None,
                    "r_int_r2": None,
                    "max_voltage_sag_v": None,
                },
                anomalies,
                v_oc_est,
            )

        cells = _infer_lipo_cells(v, cell_hint)
        v_min = float(np.nanmin(v))
        v_max = float(np.nanmax(v))
        v_mean = float(np.nanmean(v))

        valid_vc = np.isfinite(v) & np.isfinite(c)
        c_max = float(np.nanmax(c)) if np.any(np.isfinite(c)) else None
        c_mean = float(np.nanmean(c)) if np.any(np.isfinite(c)) else None

        r_int_ohm: float | None = None
        r_int_r2: float | None = None
        max_sag_v: float | None = None

        if np.sum(valid_vc) >= 10 and float(np.nanstd(c[valid_vc])) > 0.5:
            t_vc = t[valid_vc]
            v_vc = v[valid_vc]
            c_vc = c[valid_vc]

            # Electro-chemical model: V(t) = V_0 - k_soc * Q(t) - R_int * I(t)
            dt = np.diff(t_vc, prepend=t_vc[0])
            dt = np.clip(dt, 0.0, 5.0)
            q_ah = np.cumsum(c_vc * dt) / 3600.0
            q_span = float(np.max(q_ah) - np.min(q_ah))

            if q_span > 1e-5:
                X = np.column_stack([np.ones_like(c_vc), -q_ah, -c_vc])
                beta, _, _, _ = np.linalg.lstsq(X, v_vc, rcond=None)
                v0_fit, k_soc, r_fit = float(beta[0]), float(beta[1]), float(beta[2])
                if r_fit < 0.002 or r_fit > 2.5 or k_soc < -0.5:
                    # Fallback to direct detrended V vs I OLS if Q and I are collinear
                    X2 = np.column_stack([np.ones_like(c_vc), -c_vc])
                    b2, _, _, _ = np.linalg.lstsq(X2, v_vc, rcond=None)
                    r_fit = max(0.0, float(b2[1]))
                    v_pred = X2 @ b2
                else:
                    v_pred = X @ beta
            else:
                X2 = np.column_stack([np.ones_like(c_vc), -c_vc])
                b2, _, _, _ = np.linalg.lstsq(X2, v_vc, rcond=None)
                r_fit = max(0.0, float(b2[1]))
                v_pred = X2 @ b2

            ss_res = float(np.sum((v_vc - v_pred) ** 2))
            ss_tot = float(np.sum((v_vc - np.mean(v_vc)) ** 2))
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-9 else 0.0
            r_int_ohm = max(0.001, r_fit)
            r_int_r2 = round(max(0.0, min(1.0, r2)), 4)

            v_oc_est[valid_vc] = v_vc + c_vc * r_int_ohm
            sag_curve = np.where(valid_vc, c * r_int_ohm, np.nan)
            max_sag_v = float(np.nanmax(sag_curve))
        else:
            v_oc_est = v.copy()

        r_int_mohm = round(r_int_ohm * 1000.0, 2) if r_int_ohm is not None else None
        r_per_cell_mohm = round(r_int_mohm / cells, 2) if r_int_mohm is not None else None

        # Anomaly 1: Elevated Internal Resistance
        if r_per_cell_mohm is not None and r_per_cell_mohm > 22.0:
            sev = "critical" if r_per_cell_mohm > 32.0 else "warning"
            thresh = 22.0 * cells
            anomalies.append(
                DetectedAnomaly(
                    anomaly_id=next_id("PWR"),
                    category="power",
                    code="ELEVATED_RINT",
                    severity=sev,
                    title=f"Elevated Battery Internal Resistance ({r_int_mohm:.1f} mΩ)",
                    description=(
                        f"Estimated pack R_int is {r_int_mohm:.1f} mΩ ({r_per_cell_mohm:.1f} mΩ/cell "
                        f"across {cells}S), exceeding nominal <= 22 mΩ/cell threshold (R²={r_int_r2:.2f})."
                    ),
                    start_time_s=float(t[0]),
                    end_time_s=float(t[-1]),
                    peak_time_s=float(t[int(np.nanargmin(v))]),
                    peak_value=float(r_int_mohm),
                    threshold_value=float(thresh),
                    unit="mΩ",
                    recommendation="Retire or bench-test LiPo pack; inspect XT60/XT90 solder joints and ESC leads.",
                    metrics={"r_int_mohm": r_int_mohm, "r_per_cell_mohm": r_per_cell_mohm, "r2": r_int_r2},
                )
            )

        # Anomaly 2: Under-load Voltage Sag Windows
        v_cell = v / cells
        sag_warn_v = 3.45 * cells
        sag_crit_v = 3.30 * cells
        low_mask = np.isfinite(v_cell) & (v_cell < 3.45)
        for s_idx, e_idx in _find_contiguous_windows(t, low_mask, v, min_duration_s=0.4):
            seg_v = v[s_idx : e_idx + 1]
            pk_rel = int(np.argmin(seg_v))
            pk_idx = s_idx + pk_rel
            min_v_win = float(seg_v[pk_rel])
            min_v_cell = min_v_win / cells
            sev = "critical" if min_v_cell < 3.30 else "warning"
            thresh = sag_crit_v if sev == "critical" else sag_warn_v
            anomalies.append(
                DetectedAnomaly(
                    anomaly_id=next_id("PWR"),
                    category="power",
                    code="VOLTAGE_SAG",
                    severity=sev,
                    title=f"Under-Load Voltage Sag ({min_v_win:.2f} V / {min_v_cell:.2f} V/cell)",
                    description=(
                        f"Pack voltage sagged to {min_v_win:.2f} V ({min_v_cell:.2f} V/cell) between "
                        f"t={t[s_idx]:.1f}s and t={t[e_idx]:.1f}s."
                    ),
                    start_time_s=float(t[s_idx]),
                    end_time_s=float(t[e_idx]),
                    peak_time_s=float(t[pk_idx]),
                    peak_value=round(min_v_win, 3),
                    threshold_value=round(thresh, 3),
                    unit="V",
                    recommendation="Reduce climb throttle limit, verify battery health, and configure RTL failsafe voltage.",
                    metrics={"min_cell_v": round(min_v_cell, 3), "cells": cells},
                )
            )

        return (
            {
                "estimated_cell_count": cells,
                "voltage_min_v": round(v_min, 3),
                "voltage_max_v": round(v_max, 3),
                "voltage_mean_v": round(v_mean, 3),
                "current_max_a": round(c_max, 2) if c_max is not None else None,
                "current_mean_a": round(c_mean, 2) if c_mean is not None else None,
                "internal_resistance_ohm": round(r_int_ohm, 5) if r_int_ohm is not None else None,
                "internal_resistance_mohm": r_int_mohm,
                "r_int_per_cell_mohm": r_per_cell_mohm,
                "r_int_r2": r_int_r2,
                "max_voltage_sag_v": round(max_sag_v, 3) if max_sag_v is not None else None,
            },
            anomalies,
            v_oc_est,
        )

    def _analyze_vibration(
        self,
        df: pd.DataFrame,
        t: np.ndarray,
        sample_rate_hz: float,
        next_id: Any,
    ) -> tuple[dict[str, Any], list[DetectedAnomaly], dict[str, list[float]], np.ndarray]:
        vx = df["vibe_x"].to_numpy(dtype=float)
        vy = df["vibe_y"].to_numpy(dtype=float)
        vz = df["vibe_z"].to_numpy(dtype=float)
        clip = df["clip_count"].to_numpy(dtype=float)

        n = len(t)
        vibe_mag = np.full(n, np.nan, dtype=float)
        anomalies: list[DetectedAnomaly] = []
        fft_out: dict[str, list[float]] = {"frequencies_hz": [], "amplitudes": []}

        valid_mask = np.isfinite(vx) & np.isfinite(vy) & np.isfinite(vz)
        if not np.any(valid_mask):
            return (
                {
                    "vibe_rms_x": None,
                    "vibe_rms_y": None,
                    "vibe_rms_z": None,
                    "vibe_rms_total": None,
                    "vibe_peak_total": None,
                    "total_clip_count": 0,
                    "dominant_vibe_freq_hz": None,
                    "dominant_vibe_amp": None,
                },
                anomalies,
                fft_out,
                vibe_mag,
            )

        vibe_mag = np.sqrt(np.square(vx) + np.square(vy) + np.square(vz))
        rms_x = float(np.sqrt(np.nanmean(np.square(vx))))
        rms_y = float(np.sqrt(np.nanmean(np.square(vy))))
        rms_z = float(np.sqrt(np.nanmean(np.square(vz))))
        rms_total = float(np.sqrt(rms_x**2 + rms_y**2 + rms_z**2))
        peak_total = float(np.nanmax(vibe_mag))

        valid_clip = clip[np.isfinite(clip)]
        if len(valid_clip) >= 2:
            total_clip = int(max(0.0, float(valid_clip[-1] - valid_clip[0])))
            if total_clip == 0 and float(np.max(valid_clip)) > 0 and float(valid_clip[0]) > 0:
                total_clip = int(np.max(valid_clip))
        elif len(valid_clip) == 1:
            total_clip = int(max(0.0, float(valid_clip[0])))
        else:
            total_clip = 0

        # Spectral Peak via SciPy FFT
        dom_freq_hz: float | None = None
        dom_amp: float | None = None
        if np.sum(valid_mask) >= 16 and sample_rate_hz > 2.0:
            tv = t[valid_mask]
            # Pick axis with highest variance or combined vibe_mag for spectral signature
            # Use highest-energy centered axis for clean harmonic isolation
            axis_vars = [float(np.var(vx[valid_mask])), float(np.var(vy[valid_mask])), float(np.var(vz[valid_mask]))]
            dominant_axis_raw = [vx, vy, vz][int(np.argmax(axis_vars))][valid_mask]
            sig = dominant_axis_raw
            if np.std(sig) < 1e-6:
                sig = vibe_mag[valid_mask]

            n_uniform = min(8192, max(64, int(round((tv[-1] - tv[0]) * sample_rate_hz))))
            t_uni = np.linspace(float(tv[0]), float(tv[-1]), n_uniform)
            dt_uni = float(t_uni[1] - t_uni[0]) if n_uniform > 1 else 1.0 / sample_rate_hz
            y_uni = np.interp(t_uni, tv, sig)
            y_centered = y_uni - np.mean(y_uni)

            window = scipy_signal.windows.hann(len(y_centered))
            norm_factor = float(np.sum(window)) / 2.0
            yf = scipy_fft.rfft(y_centered * window)
            xf = scipy_fft.rfftfreq(len(y_centered), d=dt_uni)
            amps = np.abs(yf) / max(1e-9, norm_factor)

            # Ignore DC bin (< 1.5 Hz)
            freq_mask = xf >= 1.5
            if np.any(freq_mask):
                xf_f = xf[freq_mask]
                amps_f = amps[freq_mask]
                pk_idx = int(np.argmax(amps_f))
                dom_freq_hz = round(float(xf_f[pk_idx]), 2)
                dom_amp = round(float(amps_f[pk_idx]), 3)

                # Downsample spectrum to <= 200 bins for responsive plotting
                if len(xf_f) > 200:
                    step = max(1, len(xf_f) // 200)
                    xf_plot = xf_f[::step]
                    amps_plot = [float(np.max(amps_f[i : i + step])) for i in range(0, len(amps_f), step)]
                else:
                    xf_plot = xf_f
                    amps_plot = amps_f.tolist()

                fft_out = {
                    "frequencies_hz": [round(float(f), 2) for f in xf_plot],
                    "amplitudes": [round(float(a), 4) for a in amps_plot],
                }

        # Vibration Anomaly Windows using rolling 0.5s RMS envelope (handles AC oscillations cleanly)
        win_n = max(3, int(round(max(sample_rate_hz, 5.0) * 0.5)))
        rms_mag_env = (
            pd.Series(np.square(vibe_mag))
            .rolling(win_n, center=True, min_periods=1)
            .mean()
            .pow(0.5)
            .to_numpy(dtype=float)
        )
        max_axis_raw = np.nanmax(np.vstack([np.abs(vx), np.abs(vy), np.abs(vz)]), axis=0)
        max_axis_env = (
            pd.Series(np.square(max_axis_raw))
            .rolling(win_n, center=True, min_periods=1)
            .mean()
            .pow(0.5)
            .to_numpy(dtype=float)
        )
        high_vibe_mask = (max_axis_env > 25.0) | (rms_mag_env > 32.0)
        for s_idx, e_idx in _find_contiguous_windows(t, high_vibe_mask, rms_mag_env, min_duration_s=0.4):
            seg_mag = rms_mag_env[s_idx : e_idx + 1]
            pk_rel = int(np.nanargmax(seg_mag))
            pk_val = float(seg_mag[pk_rel])
            pk_axis = float(np.nanmax(max_axis_env[s_idx : e_idx + 1]))
            sev = "critical" if (pk_val > 45.0 or pk_axis > 38.0) else "warning"
            freq_note = (
                f" Dominant FFT harmonic at {dom_freq_hz:.1f} Hz." if dom_freq_hz is not None else ""
            )
            anomalies.append(
                DetectedAnomaly(
                    anomaly_id=next_id("VIB"),
                    category="vibration",
                    code="HIGH_VIBRATION",
                    severity=sev,
                    title=f"Excessive Airframe Vibration ({pk_val:.1f} m/s² RMS)",
                    description=(
                        f"Rolling 0.5s vibration RMS peaked at {pk_val:.1f} m/s² (max axis {pk_axis:.1f} m/s²) "
                        f"between t={t[s_idx]:.1f}s and t={t[e_idx]:.1f}s.{freq_note}"
                    ),
                    start_time_s=float(t[s_idx]),
                    end_time_s=float(t[e_idx]),
                    peak_time_s=float(t[s_idx + pk_rel]),
                    peak_value=round(pk_val, 2),
                    threshold_value=32.0 if sev == "warning" else 45.0,
                    unit="m/s²",
                    recommendation="Balance propellers, inspect motor bearings/bell runout, and tune IMU notch filter.",
                    metrics={"dominant_freq_hz": dom_freq_hz, "max_axis_rms_ms2": round(pk_axis, 2)},
                )
            )

        # Accelerometer Clipping Detection
        if total_clip > 0:
            clip_diff = np.diff(clip, prepend=clip[0])
            clip_idx = np.where(clip_diff > 0)[0]
            s_t = float(t[clip_idx[0]]) if len(clip_idx) > 0 else float(t[0])
            e_t = float(t[clip_idx[-1]]) if len(clip_idx) > 0 else float(t[-1])
            sev = "critical" if total_clip >= 20 else "warning"
            anomalies.append(
                DetectedAnomaly(
                    anomaly_id=next_id("VIB"),
                    category="vibration",
                    code="IMU_CLIPPING",
                    severity=sev,
                    title=f"IMU Accelerometer Clipping ({total_clip} events)",
                    description=(
                        f"Detected {total_clip} accelerometer saturation/clipping events; "
                        f"EKF altitude and attitude estimation may experience drift."
                    ),
                    start_time_s=s_t,
                    end_time_s=e_t,
                    peak_time_s=e_t,
                    peak_value=float(total_clip),
                    threshold_value=1.0,
                    unit="clips",
                    recommendation="Upgrade flight controller vibration damping foam/bobbins and rebalance rotors.",
                    metrics={"total_clips": total_clip},
                )
            )

        return (
            {
                "vibe_rms_x": round(rms_x, 3),
                "vibe_rms_y": round(rms_y, 3),
                "vibe_rms_z": round(rms_z, 3),
                "vibe_rms_total": round(rms_total, 3),
                "vibe_peak_total": round(peak_total, 3),
                "total_clip_count": total_clip,
                "dominant_vibe_freq_hz": dom_freq_hz,
                "dominant_vibe_amp": dom_amp,
            },
            anomalies,
            fft_out,
            vibe_mag,
        )

    def _analyze_attitude(
        self,
        df: pd.DataFrame,
        t: np.ndarray,
        sample_rate_hz: float,
        next_id: Any,
    ) -> tuple[dict[str, Any], list[DetectedAnomaly], np.ndarray, np.ndarray]:
        roll = df["roll_deg"].to_numpy(dtype=float)
        roll_des = df["roll_des_deg"].to_numpy(dtype=float)
        pitch = df["pitch_deg"].to_numpy(dtype=float)
        pitch_des = df["pitch_des_deg"].to_numpy(dtype=float)

        n = len(t)
        roll_err = np.where(np.isfinite(roll) & np.isfinite(roll_des), roll_des - roll, np.nan)
        pitch_err = np.where(np.isfinite(pitch) & np.isfinite(pitch_des), pitch_des - pitch, np.nan)
        anomalies: list[DetectedAnomaly] = []

        roll_rms = float(np.sqrt(np.nanmean(np.square(roll_err)))) if np.any(np.isfinite(roll_err)) else None
        pitch_rms = float(np.sqrt(np.nanmean(np.square(pitch_err)))) if np.any(np.isfinite(pitch_err)) else None
        roll_max = float(np.nanmax(np.abs(roll_err))) if np.any(np.isfinite(roll_err)) else None
        pitch_max = float(np.nanmax(np.abs(pitch_err))) if np.any(np.isfinite(pitch_err)) else None

        def _estimate_lag_ms(act: np.ndarray, des: np.ndarray) -> float | None:
            valid = np.isfinite(act) & np.isfinite(des)
            if np.sum(valid) < 20 or sample_rate_hz <= 1.0:
                return None
            if float(np.std(des[valid])) < 0.4:
                return 0.0
            tv = t[valid]
            n_uni = min(4096, max(40, int(round((tv[-1] - tv[0]) * sample_rate_hz))))
            t_uni = np.linspace(float(tv[0]), float(tv[-1]), n_uni)
            dt_uni = float(t_uni[1] - t_uni[0])
            a_u = np.interp(t_uni, tv, act[valid])
            d_u = np.interp(t_uni, tv, des[valid])
            a_c = a_u - np.mean(a_u)
            d_c = d_u - np.mean(d_u)
            denom = float(np.linalg.norm(a_c) * np.linalg.norm(d_c))
            if denom < 1e-9:
                return 0.0
            corr = scipy_signal.correlate(a_c, d_c, mode="full") / denom
            lags = scipy_signal.correlation_lags(len(a_c), len(d_c), mode="full")
            overlap = np.maximum(1, len(a_c) - np.abs(lags))
            corr_unbiased = corr * (len(a_c) / overlap)
            lag_s = lags * dt_uni
            win = (lag_s >= -0.25) & (lag_s <= 0.8)
            if not np.any(win):
                return 0.0
            best_i = int(np.argmax(corr_unbiased[win]))
            return round(max(0.0, float(lag_s[win][best_i]) * 1000.0), 1)

        roll_lag_ms = _estimate_lag_ms(roll, roll_des)
        pitch_lag_ms = _estimate_lag_ms(pitch, pitch_des)

        # Oscillation & tracking anomaly windows via rolling 0.8s RMS envelope
        osc_freq_hz: float | None = None
        win_att = max(5, int(round(max(sample_rate_hz, 5.0) * 0.8)))
        for axis_name, err in (("Roll", roll_err), ("Pitch", pitch_err)):
            valid = np.isfinite(err)
            if np.sum(valid) < 16:
                continue
            err_filled = np.where(valid, err, 0.0)
            rms_env = (
                pd.Series(np.square(err_filled))
                .rolling(win_att, center=True, min_periods=1)
                .mean()
                .pow(0.5)
                .to_numpy(dtype=float)
            )
            # Detect sustained tracking error / oscillation RMS > 2.8 deg
            high_err_mask = rms_env > 2.8
            for s_idx, e_idx in _find_contiguous_windows(t, high_err_mask, rms_env, min_duration_s=0.6):
                seg_abs = np.abs(err_filled[s_idx : e_idx + 1])
                pk_rel = int(np.argmax(seg_abs))
                pk_val = float(seg_abs[pk_rel])
                rms_pk = float(np.max(rms_env[s_idx : e_idx + 1]))
                sev = "critical" if (pk_val > 8.5 or rms_pk > 5.0) else "warning"

                # Check spectral oscillation frequency inside/around window
                pad_s = max(0, s_idx - 10)
                pad_e = min(n, e_idx + 11)
                seg_e = err[pad_s:pad_e]
                seg_t = t[pad_s:pad_e]
                seg_v = np.isfinite(seg_e)
                local_freq = None
                if np.sum(seg_v) >= 16 and seg_t[seg_v][-1] > seg_t[seg_v][0]:
                    tu = np.linspace(float(seg_t[seg_v][0]), float(seg_t[seg_v][-1]), max(32, int(np.sum(seg_v) * 2)))
                    eu = np.interp(tu, seg_t[seg_v], seg_e[seg_v])
                    eu = eu - np.mean(eu)
                    dt_u = float(tu[1] - tu[0])
                    yf = np.abs(scipy_fft.rfft(eu * scipy_signal.windows.hann(len(eu))))
                    xf = scipy_fft.rfftfreq(len(eu), d=dt_u)
                    band = (xf >= 1.2) & (xf <= 18.0)
                    if np.any(band):
                        local_freq = round(float(xf[band][int(np.argmax(yf[band]))]), 2)
                        if osc_freq_hz is None:
                            osc_freq_hz = local_freq

                code = "PID_OSCILLATION" if (local_freq and local_freq >= 1.8) else "ATTITUDE_TRACKING_LAG"
                freq_str = f" (~{local_freq:.1f} Hz oscillation)" if local_freq else ""
                anomalies.append(
                    DetectedAnomaly(
                        anomaly_id=next_id("ATT"),
                        category="attitude",
                        code=code,
                        severity=sev,
                        title=f"{axis_name} PID Instability / Tracking Error ({pk_val:.1f}° peak){freq_str}",
                        description=(
                            f"{axis_name} tracking error reached {rms_pk:.1f}° RMS ({pk_val:.1f}° peak) between "
                            f"t={t[s_idx]:.1f}s and t={t[e_idx]:.1f}s{freq_str}."
                        ),
                        start_time_s=float(t[s_idx]),
                        end_time_s=float(t[e_idx]),
                        peak_time_s=float(t[s_idx + pk_rel]),
                        peak_value=round(pk_val, 2),
                        threshold_value=2.8,
                        unit="deg",
                        recommendation="Reduce Roll/Pitch P or D gain if oscillating, or increase I/FF gain for tracking lag.",
                        metrics={"axis": axis_name, "rms_error_deg": round(rms_pk, 2), "oscillation_freq_hz": local_freq},
                    )
                )

        # Also check excessive phase lag (> 160 ms)
        max_lag = max(x for x in (roll_lag_ms, pitch_lag_ms) if x is not None) if any(
            x is not None for x in (roll_lag_ms, pitch_lag_ms)
        ) else None
        if max_lag is not None and max_lag > 160.0:
            sev = "critical" if max_lag > 280.0 else "warning"
            anomalies.append(
                DetectedAnomaly(
                    anomaly_id=next_id("ATT"),
                    category="attitude",
                    code="PID_PHASE_LAG",
                    severity=sev,
                    title=f"Attitude Control Phase Lag ({max_lag:.0f} ms)",
                    description=(
                        f"Cross-correlation indicates {max_lag:.0f} ms response lag between desired "
                        f"attitude setpoint and airframe response."
                    ),
                    start_time_s=float(t[0]),
                    end_time_s=float(t[-1]),
                    peak_time_s=float(t[len(t) // 2]),
                    peak_value=float(max_lag),
                    threshold_value=160.0,
                    unit="ms",
                    recommendation="Increase rate feed-forward / P-gain and inspect ESC response latency.",
                    metrics={"roll_lag_ms": roll_lag_ms, "pitch_lag_ms": pitch_lag_ms},
                )
            )

        return (
            {
                "roll_rms_error_deg": round(roll_rms, 3) if roll_rms is not None else None,
                "pitch_rms_error_deg": round(pitch_rms, 3) if pitch_rms is not None else None,
                "roll_max_error_deg": round(roll_max, 3) if roll_max is not None else None,
                "pitch_max_error_deg": round(pitch_max, 3) if pitch_max is not None else None,
                "roll_lag_ms": roll_lag_ms,
                "pitch_lag_ms": pitch_lag_ms,
                "oscillation_freq_hz": osc_freq_hz,
            },
            anomalies,
            roll_err,
            pitch_err,
        )

    def _analyze_navigation(
        self, df: pd.DataFrame, t: np.ndarray, next_id: Any
    ) -> tuple[dict[str, Any], list[DetectedAnomaly], float]:
        lat = df["gps_lat"].to_numpy(dtype=float)
        lon = df["gps_lon"].to_numpy(dtype=float)
        hdop = df["gps_hdop"].to_numpy(dtype=float)
        nsats = df["gps_nsats"].to_numpy(dtype=float)

        anomalies: list[DetectedAnomaly] = []
        total_distance_m = 0.0
        jump_count = 0

        valid_hdop = np.isfinite(hdop)
        hdop_mean = float(np.nanmean(hdop)) if np.any(valid_hdop) else None
        hdop_max = float(np.nanmax(hdop)) if np.any(valid_hdop) else None

        valid_sats = np.isfinite(nsats)
        nsats_min = int(np.nanmin(nsats)) if np.any(valid_sats) else None
        nsats_mean = float(np.nanmean(nsats)) if np.any(valid_sats) else None

        # HDOP spikes (> 2.0)
        if np.any(valid_hdop):
            hdop_mask = valid_hdop & (hdop > 2.0)
            for s_idx, e_idx in _find_contiguous_windows(t, hdop_mask, hdop, min_duration_s=0.3):
                seg = hdop[s_idx : e_idx + 1]
                pk_rel = int(np.argmax(seg))
                pk_val = float(seg[pk_rel])
                sev = "critical" if pk_val > 3.5 else "warning"
                anomalies.append(
                    DetectedAnomaly(
                        anomaly_id=next_id("NAV"),
                        category="navigation",
                        code="GPS_HDOP_SPIKE",
                        severity=sev,
                        title=f"GPS HDOP Degradation (Peak {pk_val:.2f})",
                        description=(
                            f"Horizontal Dilution of Precision spiked to {pk_val:.2f} between "
                            f"t={t[s_idx]:.1f}s and t={t[e_idx]:.1f}s, degrading GNSS fix accuracy."
                        ),
                        start_time_s=float(t[s_idx]),
                        end_time_s=float(t[e_idx]),
                        peak_time_s=float(t[s_idx + pk_rel]),
                        peak_value=round(pk_val, 2),
                        threshold_value=2.0 if sev == "warning" else 3.5,
                        unit="HDOP",
                        recommendation="Inspect GPS mast RF shielding from VTX/companion telemetry and avoid multipath canyons.",
                    )
                )

        # Satellite drop (< 8 sats)
        if np.any(valid_sats):
            sat_mask = valid_sats & (nsats < 8.0)
            for s_idx, e_idx in _find_contiguous_windows(t, sat_mask, nsats, min_duration_s=0.3):
                seg = nsats[s_idx : e_idx + 1]
                pk_rel = int(np.argmin(seg))
                min_s = int(seg[pk_rel])
                sev = "critical" if min_s < 6 else "warning"
                anomalies.append(
                    DetectedAnomaly(
                        anomaly_id=next_id("NAV"),
                        category="navigation",
                        code="GPS_SAT_DROP",
                        severity=sev,
                        title=f"GNSS Satellite Count Drop ({min_s} sats)",
                        description=(
                            f"Locked satellites fell to {min_s} between t={t[s_idx]:.1f}s and t={t[e_idx]:.1f}s."
                        ),
                        start_time_s=float(t[s_idx]),
                        end_time_s=float(t[e_idx]),
                        peak_time_s=float(t[s_idx + pk_rel]),
                        peak_value=float(min_s),
                        threshold_value=8.0,
                        unit="sats",
                        recommendation="Verify GNSS antenna sky view and check LNA/RF interference.",
                    )
                )

        # Kinematic GPS Position Jumps via Haversine distance
        valid_pos = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) > 1e-4) & (np.abs(lon) > 1e-4)
        if np.sum(valid_pos) >= 3:
            idx_pos = np.where(valid_pos)[0]
            lat_v = lat[idx_pos]
            lon_v = lon[idx_pos]
            t_v = t[idx_pos]
            dists = _haversine_m(lat_v[:-1], lon_v[:-1], lat_v[1:], lon_v[1:])
            dts = np.maximum(1e-3, np.diff(t_v))
            implied_v = dists / dts

            # Exclude impossible jumps (require meaningful spatial displacement > 8 m to avoid dt quantization jitter)
            jump_mask = (dists > 8.0) & ((implied_v > 45.0) | ((dists > 12.0) & (dts <= 1.2)))
            total_distance_m = float(np.sum(dists[~jump_mask]))

            jump_indices = np.where(jump_mask)[0]
            # Group jumps occurring within 2.5 seconds into contiguous glitch events
            if len(jump_indices) > 0:
                groups: list[list[int]] = [[int(jump_indices[0])]]
                for ji in jump_indices[1:]:
                    if t_v[int(ji) + 1] - t_v[groups[-1][-1] + 1] <= 2.5:
                        groups[-1].append(int(ji))
                    else:
                        groups.append([int(ji)])

                for grp in groups:
                    jump_count += len(grp)
                    s_t = float(t_v[grp[0]])
                    e_t = float(t_v[grp[-1] + 1])
                    max_d = float(np.max(dists[grp]))
                    max_v = float(np.max(implied_v[grp]))
                    anomalies.append(
                        DetectedAnomaly(
                            anomaly_id=next_id("NAV"),
                            category="navigation",
                            code="GPS_POSITION_JUMP",
                            severity="critical",
                            title=f"GPS Kinematic Glitch / Position Jump ({max_d:.1f} m)",
                            description=(
                                f"Detected unphysical GNSS position jump of {max_d:.1f} m "
                                f"(implied velocity {max_v:.1f} m/s) between t={s_t:.1f}s and t={e_t:.1f}s."
                            ),
                            start_time_s=s_t,
                            end_time_s=e_t,
                            peak_time_s=e_t,
                            peak_value=round(max_d, 2),
                            threshold_value=12.0,
                            unit="m",
                            recommendation="Enable EKF GNSS innovation gating / optical flow fallback and inspect GNSS receiver lock.",
                            metrics={"implied_speed_ms": round(max_v, 1), "jumps_in_window": len(grp)},
                        )
                    )

        return (
            {
                "gps_hdop_mean": round(hdop_mean, 3) if hdop_mean is not None else None,
                "gps_hdop_max": round(hdop_max, 3) if hdop_max is not None else None,
                "gps_nsats_min": nsats_min,
                "gps_nsats_mean": round(nsats_mean, 2) if nsats_mean is not None else None,
                "gps_jump_count": jump_count,
            },
            anomalies,
            total_distance_m,
        )

    @staticmethod
    def _score_power(metrics: dict[str, Any], anomalies: list[DetectedAnomaly]) -> float:
        score = 100.0
        r_cell = metrics.get("r_int_per_cell_mohm")
        if r_cell is not None and r_cell > 18.0:
            score -= min(35.0, (r_cell - 18.0) * 1.4)
        for a in anomalies:
            if a.code == "VOLTAGE_SAG":
                score -= 22.0 if a.severity == "critical" else 11.0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _score_vibration(metrics: dict[str, Any], anomalies: list[DetectedAnomaly]) -> float:
        score = 100.0
        rms = metrics.get("vibe_rms_total")
        if rms is not None and rms > 22.0:
            score -= min(40.0, (rms - 22.0) * 1.1)
        clips = metrics.get("total_clip_count", 0)
        if clips > 0:
            score -= min(30.0, 8.0 + clips * 0.6)
        for a in anomalies:
            if a.code == "HIGH_VIBRATION":
                score -= 14.0 if a.severity == "critical" else 7.0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _score_attitude(metrics: dict[str, Any], anomalies: list[DetectedAnomaly]) -> float:
        score = 100.0
        r_err = metrics.get("roll_rms_error_deg") or 0.0
        p_err = metrics.get("pitch_rms_error_deg") or 0.0
        avg_err = 0.5 * (r_err + p_err)
        if avg_err > 1.8:
            score -= min(35.0, (avg_err - 1.8) * 6.5)
        for a in anomalies:
            if a.code in ("PID_OSCILLATION", "ATTITUDE_TRACKING_LAG"):
                score -= 16.0 if a.severity == "critical" else 8.0
            elif a.code == "PID_PHASE_LAG":
                score -= 12.0 if a.severity == "critical" else 6.0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _score_navigation(metrics: dict[str, Any], anomalies: list[DetectedAnomaly]) -> float:
        score = 100.0
        hdop_max = metrics.get("gps_hdop_max")
        if hdop_max is not None and hdop_max > 1.6:
            score -= min(28.0, (hdop_max - 1.6) * 8.0)
        sats_min = metrics.get("gps_nsats_min")
        if sats_min is not None and sats_min < 9:
            score -= min(25.0, (9 - sats_min) * 5.0)
        jumps = metrics.get("gps_jump_count", 0)
        if jumps > 0:
            score -= min(40.0, 18.0 + jumps * 6.0)
        return max(0.0, min(100.0, score))

    @staticmethod
    def _build_timeline_events(
        flight: ParsedFlight,
        df: pd.DataFrame,
        t: np.ndarray,
        anomalies: list[DetectedAnomaly],
    ) -> list[TimelineEvent]:
        events: list[TimelineEvent] = []

        # Raw parsed events (mode changes, autopilot logs)
        for ev in flight.raw_events:
            events.append(
                TimelineEvent(
                    timestamp_s=round(float(ev.timestamp_s), 2),
                    end_time_s=None,
                    event_type=ev.event_type,
                    category="flight_ops",
                    severity=ev.severity,
                    title=ev.title,
                    description=ev.description,
                )
            )

        # Infer Takeoff and Landing from altitude
        alt = df["baro_alt_m"] if df["baro_alt_m"].notna().any() else df["gps_alt_m"]
        if alt.notna().any() and len(t) >= 5:
            alt_arr = alt.to_numpy(dtype=float)
            base_alt = float(np.nanmin(alt_arr[: max(3, len(alt_arr) // 10)]))
            rel_alt = alt_arr - base_alt
            airborne = np.where(np.isfinite(rel_alt) & (rel_alt > 1.5))[0]
            if len(airborne) > 0:
                to_t = float(t[airborne[0]])
                ld_t = float(t[airborne[-1]])
                events.append(
                    TimelineEvent(
                        timestamp_s=round(to_t, 2),
                        end_time_s=None,
                        event_type="takeoff",
                        category="flight_ops",
                        severity="info",
                        title="Takeoff Detected",
                        description=f"Vehicle climbed above 1.5 m AGL at t={to_t:.1f}s.",
                    )
                )
                if ld_t > to_t + 2.0:
                    events.append(
                        TimelineEvent(
                            timestamp_s=round(ld_t, 2),
                            end_time_s=None,
                            event_type="landing",
                            category="flight_ops",
                            severity="info",
                            title="Landing / Descent Complete",
                            description=f"Vehicle descended below 1.5 m AGL at t={ld_t:.1f}s.",
                        )
                    )

        for anom in anomalies:
            events.append(
                TimelineEvent(
                    timestamp_s=round(anom.start_time_s, 2),
                    end_time_s=round(anom.end_time_s, 2),
                    event_type="anomaly",
                    category=anom.category,
                    severity=anom.severity,
                    title=anom.title,
                    description=anom.description,
                )
            )

        events.sort(key=lambda e: e.timestamp_s)
        return events
