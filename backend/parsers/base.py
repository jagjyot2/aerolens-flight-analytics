"""Canonical telemetry data structures and abstract parser base class."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


CANONICAL_NUMERIC_COLUMNS: tuple[str, ...] = (
    "timestamp_s",
    "voltage_v",
    "current_a",
    "battery_remaining_pct",
    "roll_deg",
    "pitch_deg",
    "yaw_deg",
    "roll_des_deg",
    "pitch_des_deg",
    "yaw_des_deg",
    "vibe_x",
    "vibe_y",
    "vibe_z",
    "clip_count",
    "gps_lat",
    "gps_lon",
    "gps_alt_m",
    "gps_hdop",
    "gps_nsats",
    "gps_speed_ms",
    "baro_alt_m",
    "throttle_pct",
)


class ParserError(ValueError):
    """Raised when a flight log cannot be parsed or lacks valid telemetry."""


@dataclass
class FlightMetadata:
    """Normalized metadata extracted from a flight log."""

    format_id: str
    autopilot: str
    vehicle_type: str = "Multirotor"
    firmware_version: str = "Unknown"
    source_filename: str = ""
    is_synthetic: bool = False
    synthetic_label: str = ""
    cell_count_hint: int | None = None
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedEvent:
    """Discrete flight event extracted directly from log messages."""

    timestamp_s: float
    event_type: str  # mode_change, arm, disarm, autopilot_msg, warning
    title: str
    description: str = ""
    severity: str = "info"  # info, warning, critical


@dataclass
class ParsedFlight:
    """Canonical flight log dataset produced by every parser implementation."""

    metadata: FlightMetadata
    dataframe: pd.DataFrame
    raw_events: list[ParsedEvent] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        if self.dataframe.empty:
            return 0.0
        return float(self.dataframe["timestamp_s"].iloc[-1] - self.dataframe["timestamp_s"].iloc[0])

    @property
    def sample_count(self) -> int:
        return int(len(self.dataframe))

    @property
    def sample_rate_hz(self) -> float:
        if len(self.dataframe) < 2:
            return 0.0
        dt = np.diff(self.dataframe["timestamp_s"].to_numpy(dtype=float))
        pos = dt[dt > 1e-6]
        if len(pos) == 0:
            return 0.0
        return float(1.0 / np.median(pos))


def quaternion_to_euler_deg(w: float, x: float, y: float, z: float) -> tuple[float, float, float]:
    """Convert Hamilton quaternion (w, x, y, z) to aerospace Euler angles (roll, pitch, yaw) in deg."""
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm < 1e-9:
        return 0.0, 0.0, 0.0
    w, x, y, z = w / norm, x / norm, y / norm, z / norm

    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    sinp = 2.0 * (w * y - z * x)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.asin(sinp)

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def normalize_time_column(values: pd.Series) -> pd.Series:
    """Convert raw timestamps (s, ms, us, or ISO strings) into seconds from zero."""
    if values.empty:
        return pd.Series(dtype=float)

    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() >= max(1, int(0.8 * len(values))):
        arr = numeric.ffill().bfill().to_numpy(dtype=float)
        span = float(np.nanmax(arr) - np.nanmin(arr))
        max_abs = float(np.nanmax(np.abs(arr)))
        # Heuristic scale detection: microseconds vs milliseconds vs seconds
        if span > 1e6 or max_abs > 1e11:
            scale = 1e-6  # microseconds
        elif span > 5e4 and max_abs > 1e5:
            scale = 1e-3  # milliseconds
        else:
            scale = 1.0  # seconds
        rel = (arr - arr[0]) * scale
        return pd.Series(rel, index=values.index, dtype=float)

    parsed_dt = pd.to_datetime(values, errors="coerce", utc=True)
    if parsed_dt.notna().sum() >= max(1, int(0.8 * len(values))):
        t_s = parsed_dt.astype("int64") / 1e9
        t_s = t_s - t_s.iloc[0]
        return pd.Series(t_s.to_numpy(dtype=float), index=values.index, dtype=float)

    raise ParserError("Unable to interpret timestamp column values.")


def merge_topic_frames(topic_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Merge heterogeneous time-indexed telemetry streams onto a unified sorted timeline."""
    valid = []
    for name, df in topic_frames.items():
        if df is None or df.empty or "timestamp_s" not in df.columns:
            continue
        clean = df.dropna(subset=["timestamp_s"]).copy()
        clean = clean.sort_values("timestamp_s").drop_duplicates(subset=["timestamp_s"], keep="last")
        if not clean.empty:
            valid.append((name, clean))

    if not valid:
        raise ParserError("No timestamped telemetry streams found in flight log.")

    # Choose the densest high-rate stream (preferring attitude or battery) or union of timestamps
    all_times = np.unique(
        np.concatenate([df["timestamp_s"].to_numpy(dtype=float) for _, df in valid])
    )
    if len(all_times) > 12000:
        # Downsample unified grid to keep high-rate logs responsive while preserving dynamics
        all_times = np.linspace(float(all_times[0]), float(all_times[-1]), 6000)

    timeline = pd.DataFrame({"timestamp_s": all_times})
    for _, df in valid:
        cols_to_merge = [c for c in df.columns if c != "timestamp_s" and c not in timeline.columns]
        if not cols_to_merge:
            # Fill missing values if column already exists from another topic
            overlap = [c for c in df.columns if c != "timestamp_s" and c in timeline.columns]
            if overlap:
                merged_extra = pd.merge_asof(
                    timeline[["timestamp_s"]],
                    df[["timestamp_s", *overlap]].sort_values("timestamp_s"),
                    on="timestamp_s",
                    direction="nearest",
                )
                for c in overlap:
                    timeline[c] = timeline[c].fillna(merged_extra[c])
            continue
        merged = pd.merge_asof(
            timeline,
            df[["timestamp_s", *cols_to_merge]].sort_values("timestamp_s"),
            on="timestamp_s",
            direction="nearest",
        )
        timeline = merged

    timeline["timestamp_s"] = timeline["timestamp_s"] - float(timeline["timestamp_s"].iloc[0])
    return timeline


class BaseFlightParser(ABC):
    """Abstract base class for extensible AeroLens flight log parsers."""

    format_id: str = "base"
    display_name: str = "Base Parser"
    supported_extensions: tuple[str, ...] = ()

    @abstractmethod
    def can_parse(self, filename: str, content: bytes) -> bool:
        """Return True if this parser recognizes the filename or raw byte signature."""

    @abstractmethod
    def parse(self, content: bytes, filename: str = "") -> ParsedFlight:
        """Parse raw bytes into a canonical ParsedFlight dataset."""

    @staticmethod
    def finalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        """Ensure every canonical column exists, types are valid, and rows are sorted by time."""
        if df.empty:
            raise ParserError("Parsed flight telemetry contains zero rows.")

        out = df.copy()
        if "timestamp_s" not in out.columns:
            raise ParserError("Missing canonical timestamp_s column.")

        out["timestamp_s"] = pd.to_numeric(out["timestamp_s"], errors="coerce")
        out = out.dropna(subset=["timestamp_s"]).sort_values("timestamp_s").reset_index(drop=True)
        if out.empty:
            raise ParserError("All telemetry timestamps were invalid or NaN.")

        t0 = float(out["timestamp_s"].iloc[0])
        out["timestamp_s"] = out["timestamp_s"] - t0

        for col in CANONICAL_NUMERIC_COLUMNS:
            if col not in out.columns:
                out[col] = np.nan
            else:
                out[col] = pd.to_numeric(out[col], errors="coerce")

        if "flight_mode" not in out.columns:
            out["flight_mode"] = "UNKNOWN"
        else:
            out["flight_mode"] = (
                out["flight_mode"]
                .astype(str)
                .replace({"nan": "UNKNOWN", "None": "UNKNOWN", "": "UNKNOWN"})
            )

        # Require at least some meaningful physical telemetry beyond timestamp
        telemetry_cols = [c for c in CANONICAL_NUMERIC_COLUMNS if c != "timestamp_s"]
        if out[telemetry_cols].notna().sum().sum() == 0:
            raise ParserError("File parsed without any recognizable telemetry channels.")

        ordered_cols = [*CANONICAL_NUMERIC_COLUMNS, "flight_mode"]
        return out[ordered_cols]
