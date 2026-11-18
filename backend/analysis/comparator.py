"""Multi-flight comparison engine producing aligned normalized curves and KPI deltas."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class FlightComparisonItem:
    """Normalized flight entry inside a multi-flight comparison."""

    flight_id: int
    name: str
    autopilot: str
    is_synthetic: bool
    summary: dict[str, Any]
    anomaly_count: int
    critical_count: int
    warning_count: int
    aligned_progress_pct: list[float]
    aligned_voltage_v: list[float | None]
    aligned_vibe_mag: list[float | None]
    aligned_attitude_err_deg: list[float | None]
    aligned_altitude_m: list[float | None]


@dataclass
class MetricDelta:
    """Comparison delta against the baseline (first) flight."""

    metric: str
    label: str
    unit: str
    baseline_value: float | None
    compared_values: dict[int, float | None]
    deltas_from_baseline: dict[int, float | None]


@dataclass
class FlightComparisonResult:
    """Full comparison report across 2+ flights."""

    baseline_flight_id: int
    flights: list[FlightComparisonItem]
    metric_deltas: list[MetricDelta]
    insights: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_flight_id": self.baseline_flight_id,
            "flights": [asdict(f) for f in self.flights],
            "metric_deltas": [asdict(m) for m in self.metric_deltas],
            "insights": self.insights,
        }


COMPARED_METRIC_SPECS: tuple[tuple[str, str, str], ...] = (
    ("health_score", "Flight Health Score", "pts"),
    ("power_score", "Power Subsystem Score", "pts"),
    ("vibration_score", "Vibration Subsystem Score", "pts"),
    ("attitude_score", "Attitude Control Score", "pts"),
    ("navigation_score", "Navigation Integrity Score", "pts"),
    ("internal_resistance_mohm", "Battery R_int", "mΩ"),
    ("voltage_min_v", "Min Pack Voltage", "V"),
    ("vibe_rms_total", "Total Vibration RMS", "m/s²"),
    ("dominant_vibe_freq_hz", "Dominant Vibe FFT Peak", "Hz"),
    ("roll_rms_error_deg", "Roll RMS Tracking Error", "deg"),
    ("pitch_rms_error_deg", "Pitch RMS Tracking Error", "deg"),
    ("gps_hdop_max", "Peak GPS HDOP", "HDOP"),
    ("duration_s", "Flight Duration", "s"),
)


def _resample_series_to_progress(
    t: np.ndarray, values: np.ndarray, grid_pct: np.ndarray
) -> list[float | None]:
    valid = np.isfinite(values) & np.isfinite(t)
    if np.sum(valid) < 2:
        return [None] * len(grid_pct)
    tv = t[valid]
    span = float(tv[-1] - tv[0])
    if span <= 1e-6:
        val = round(float(values[valid][0]), 3)
        return [val] * len(grid_pct)
    pct_t = 100.0 * (tv - tv[0]) / span
    interp_vals = np.interp(grid_pct, pct_t, values[valid])
    return [round(float(x), 3) for x in interp_vals]


def compare_flights(
    flight_records: list[dict[str, Any]], n_points: int = 80
) -> FlightComparisonResult:
    """
    Compare 2 or more flights by aligning time-series onto 0-100% mission progress
    and computing metric deltas relative to the first flight (baseline).
    """
    if len(flight_records) < 2:
        raise ValueError("At least 2 flights are required for comparison.")

    grid_pct = np.linspace(0.0, 100.0, n_points)
    items: list[FlightComparisonItem] = []

    for rec in flight_records:
        summary = rec["summary"]
        anomalies = rec.get("anomalies", [])
        df = pd.DataFrame(rec["telemetry"])
        if df.empty or "timestamp_s" not in df.columns:
            t = np.array([0.0, 1.0])
            volt = np.array([np.nan, np.nan])
            vibe = np.array([np.nan, np.nan])
            att_err = np.array([np.nan, np.nan])
            alt = np.array([np.nan, np.nan])
        else:
            t = pd.to_numeric(df["timestamp_s"], errors="coerce").to_numpy(dtype=float)
            volt = pd.to_numeric(df.get("voltage_v"), errors="coerce").to_numpy(dtype=float)
            vx = pd.to_numeric(df.get("vibe_x"), errors="coerce").to_numpy(dtype=float)
            vy = pd.to_numeric(df.get("vibe_y"), errors="coerce").to_numpy(dtype=float)
            vz = pd.to_numeric(df.get("vibe_z"), errors="coerce").to_numpy(dtype=float)
            vibe = np.sqrt(np.square(vx) + np.square(vy) + np.square(vz))
            r_err = np.abs(
                pd.to_numeric(df.get("roll_des_deg"), errors="coerce").to_numpy(dtype=float)
                - pd.to_numeric(df.get("roll_deg"), errors="coerce").to_numpy(dtype=float)
            )
            p_err = np.abs(
                pd.to_numeric(df.get("pitch_des_deg"), errors="coerce").to_numpy(dtype=float)
                - pd.to_numeric(df.get("pitch_deg"), errors="coerce").to_numpy(dtype=float)
            )
            att_err = np.sqrt(0.5 * (np.square(r_err) + np.square(p_err)))
            alt_col = df["baro_alt_m"] if "baro_alt_m" in df.columns and df["baro_alt_m"].notna().any() else df.get("gps_alt_m")
            alt = pd.to_numeric(alt_col, errors="coerce").to_numpy(dtype=float)

        items.append(
            FlightComparisonItem(
                flight_id=int(rec["id"]),
                name=str(rec["name"]),
                autopilot=str(rec.get("autopilot", "Unknown")),
                is_synthetic=bool(rec.get("is_synthetic", False)),
                summary=summary,
                anomaly_count=len(anomalies),
                critical_count=sum(1 for a in anomalies if a.get("severity") == "critical"),
                warning_count=sum(1 for a in anomalies if a.get("severity") == "warning"),
                aligned_progress_pct=[round(float(p), 2) for p in grid_pct],
                aligned_voltage_v=_resample_series_to_progress(t, volt, grid_pct),
                aligned_vibe_mag=_resample_series_to_progress(t, vibe, grid_pct),
                aligned_attitude_err_deg=_resample_series_to_progress(t, att_err, grid_pct),
                aligned_altitude_m=_resample_series_to_progress(t, alt, grid_pct),
            )
        )

    baseline = items[0]
    metric_deltas: list[MetricDelta] = []
    for key, label, unit in COMPARED_METRIC_SPECS:
        base_raw = baseline.summary.get(key)
        base_val = float(base_raw) if base_raw is not None else None
        comp_vals: dict[int, float | None] = {}
        deltas: dict[int, float | None] = {}
        for it in items:
            raw = it.summary.get(key)
            val = float(raw) if raw is not None else None
            comp_vals[it.flight_id] = round(val, 3) if val is not None else None
            if base_val is not None and val is not None:
                deltas[it.flight_id] = round(val - base_val, 3)
            else:
                deltas[it.flight_id] = None
        metric_deltas.append(
            MetricDelta(
                metric=key,
                label=label,
                unit=unit,
                baseline_value=round(base_val, 3) if base_val is not None else None,
                compared_values=comp_vals,
                deltas_from_baseline=deltas,
            )
        )

    insights: list[str] = []
    best = max(items, key=lambda x: float(x.summary.get("health_score", 0.0)))
    worst = min(items, key=lambda x: float(x.summary.get("health_score", 0.0)))
    insights.append(
        f"Highest Flight Health Score: '{best.name}' ({best.summary.get('health_score', 0):.1f}/100, "
        f"Grade {best.summary.get('health_grade', '?')}); lowest: '{worst.name}' "
        f"({worst.summary.get('health_score', 0):.1f}/100)."
    )

    for it in items[1:]:
        dh = float(it.summary.get("health_score", 0.0)) - float(baseline.summary.get("health_score", 0.0))
        direction = "higher" if dh >= 0 else "lower"
        insights.append(
            f"'{it.name}' scored {abs(dh):.1f} pts {direction} than baseline '{baseline.name}' "
            f"({it.anomaly_count} vs {baseline.anomaly_count} anomalies)."
        )
        r_base = baseline.summary.get("internal_resistance_mohm")
        r_cur = it.summary.get("internal_resistance_mohm")
        if r_base is not None and r_cur is not None and abs(r_cur - r_base) >= 15.0:
            insights.append(
                f"Battery R_int delta on '{it.name}': {r_cur - r_base:+.1f} mΩ vs baseline "
                f"({r_cur:.1f} mΩ vs {r_base:.1f} mΩ)."
            )

    return FlightComparisonResult(
        baseline_flight_id=baseline.flight_id,
        flights=items,
        metric_deltas=metric_deltas,
        insights=insights,
    )
