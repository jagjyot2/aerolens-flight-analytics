"""ArduPilot DataFlash CSV and DataFlash text (.log) flight log parser."""

from __future__ import annotations

import io
import re
from typing import Any

import numpy as np
import pandas as pd

from backend.parsers.base import (
    BaseFlightParser,
    FlightMetadata,
    ParsedEvent,
    ParsedFlight,
    ParserError,
    merge_topic_frames,
    normalize_time_column,
)


ARDUPILOT_MODES: dict[int, str] = {
    0: "STABILIZE",
    1: "ACRO",
    2: "ALT_HOLD",
    3: "AUTO",
    4: "GUIDED",
    5: "LOITER",
    6: "RTL",
    7: "CIRCLE",
    9: "LAND",
    11: "DRIFT",
    13: "SPORT",
    16: "POSHOLD",
    17: "BRAKE",
    21: "SMART_RTL",
}


def _norm_key(col: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", col.strip().lower())


CSV_COLUMN_ALIASES: dict[str, str] = {
    # Timestamp
    "timestamps": "timestamp_s",
    "times": "timestamp_s",
    "timestamp": "timestamp_s",
    "timeus": "timestamp_s",
    "timebootms": "timestamp_s",
    "timems": "timestamp_s",
    "t": "timestamp_s",
    # Battery
    "voltagev": "voltage_v",
    "batvolt": "voltage_v",
    "currvolt": "voltage_v",
    "volt": "voltage_v",
    "voltage": "voltage_v",
    "vbat": "voltage_v",
    "currenta": "current_a",
    "batcurr": "current_a",
    "currcurr": "current_a",
    "curr": "current_a",
    "current": "current_a",
    "ibat": "current_a",
    "batteryremainingpct": "battery_remaining_pct",
    "batrempct": "battery_remaining_pct",
    "rempct": "battery_remaining_pct",
    "batterypct": "battery_remaining_pct",
    "soc": "battery_remaining_pct",
    # Attitude actual & desired
    "rolldeg": "roll_deg",
    "attroll": "roll_deg",
    "roll": "roll_deg",
    "pitchdeg": "pitch_deg",
    "attpitch": "pitch_deg",
    "pitch": "pitch_deg",
    "yawdeg": "yaw_deg",
    "attyaw": "yaw_deg",
    "yaw": "yaw_deg",
    "rolldesdeg": "roll_des_deg",
    "attdesroll": "roll_des_deg",
    "desroll": "roll_des_deg",
    "rolldes": "roll_des_deg",
    "rolltarget": "roll_des_deg",
    "pitchdesdeg": "pitch_des_deg",
    "attdespitch": "pitch_des_deg",
    "despitch": "pitch_des_deg",
    "pitchdes": "pitch_des_deg",
    "pitchtarget": "pitch_des_deg",
    "yawdesdeg": "yaw_des_deg",
    "attdesyaw": "yaw_des_deg",
    "desyaw": "yaw_des_deg",
    "yawdes": "yaw_des_deg",
    "yawtarget": "yaw_des_deg",
    # Vibration
    "vibex": "vibe_x",
    "vibevibex": "vibe_x",
    "vibey": "vibe_y",
    "vibevibey": "vibe_y",
    "vibez": "vibe_z",
    "vibevibez": "vibe_z",
    "clipcount": "clip_count",
    "vibeclip": "clip_count",
    "clip": "clip_count",
    "clip0": "clip_count",
    "vibeclip0": "clip_count",
    # GPS & Baro
    "gpslat": "gps_lat",
    "lat": "gps_lat",
    "latitude": "gps_lat",
    "gpslon": "gps_lon",
    "gpslng": "gps_lon",
    "lon": "gps_lon",
    "lng": "gps_lon",
    "longitude": "gps_lon",
    "gpsaltm": "gps_alt_m",
    "gpsalt": "gps_alt_m",
    "altmsl": "gps_alt_m",
    "gpshdop": "gps_hdop",
    "hdop": "gps_hdop",
    "gpsnsats": "gps_nsats",
    "nsats": "gps_nsats",
    "sats": "gps_nsats",
    "satellites": "gps_nsats",
    "gpsspeedms": "gps_speed_ms",
    "gpsspd": "gps_speed_ms",
    "spd": "gps_speed_ms",
    "groundspeed": "gps_speed_ms",
    "baroaltm": "baro_alt_m",
    "baroalt": "baro_alt_m",
    "ctunalt": "baro_alt_m",
    "alt": "baro_alt_m",
    "relalt": "baro_alt_m",
    "altitudem": "baro_alt_m",
    # Throttle & mode
    "throttlepct": "throttle_pct",
    "ctuntho": "throttle_pct",
    "tho": "throttle_pct",
    "throttle": "throttle_pct",
    "throttles": "throttle_pct",
    "flightmode": "flight_mode",
    "mode": "flight_mode",
    "modemode": "flight_mode",
}


class ArduPilotParser(BaseFlightParser):
    """Parser for ArduPilot DataFlash CSV exports and raw text DataFlash (.log) files."""

    format_id = "ardupilot_csv"
    display_name = "ArduPilot DataFlash CSV / .log"
    supported_extensions = (".csv", ".log", ".txt")

    def can_parse(self, filename: str, content: bytes) -> bool:
        ext = "." + filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        if ext == ".csv":
            # Exclude PX4 ulog2csv files if clearly tagged
            head = content[:2048].decode("utf-8", errors="ignore")
            if "vehicle_attitude" in head and "timestamp" in head and "q[0]" in head:
                return False
            return "," in head
        if ext in (".log", ".txt"):
            head = content[:4096].decode("utf-8", errors="ignore")
            return "FMT," in head or "ATT," in head or "BAT," in head or "VIBE," in head
        head = content[:2048].decode("utf-8", errors="ignore")
        return "FMT," in head and ("ATT," in head or "GPS," in head)

    def parse(self, content: bytes, filename: str = "") -> ParsedFlight:
        if not content or not content.strip():
            raise ParserError("Empty file provided to ArduPilot parser.")

        text = content.decode("utf-8-sig", errors="replace")
        head_lines = [ln.strip() for ln in text.splitlines()[:50] if ln.strip()]

        is_dataflash_log = any(
            ln.startswith(("FMT,", "PARM,", "ATT,", "BAT,", "VIBE,", "GPS,"))
            for ln in head_lines
        ) and not any(
            ln.lower().startswith(("timestamp", "time_s", "timeus", "att.roll"))
            for ln in head_lines
        )

        if is_dataflash_log:
            return self._parse_dataflash_text_log(text, filename)
        return self._parse_csv(text, filename)

    def _extract_comment_metadata(self, lines: list[str], filename: str) -> FlightMetadata:
        meta_kv: dict[str, str] = {}
        for ln in lines:
            stripped = ln.strip()
            if not stripped.startswith("#"):
                continue
            body = stripped.lstrip("#").strip()
            if ":" in body:
                k, v = body.split(":", 1)
                meta_kv[k.strip().upper()] = v.strip()

        is_synth = meta_kv.get("AEROLENS_SYNTHETIC", "").lower() in ("true", "1", "yes")
        if not is_synth and "synthetic" in filename.lower():
            is_synth = True

        cell_hint = None
        if "CELL_COUNT" in meta_kv:
            try:
                cell_hint = int(meta_kv["CELL_COUNT"])
            except ValueError:
                cell_hint = None

        return FlightMetadata(
            format_id=self.format_id,
            autopilot=meta_kv.get("AUTOPILOT", "ArduPilot"),
            vehicle_type=meta_kv.get("VEHICLE_TYPE", meta_kv.get("VEHICLE", "Multirotor")),
            firmware_version=meta_kv.get("FIRMWARE", "ArduCopter DataFlash"),
            source_filename=filename,
            is_synthetic=is_synth,
            synthetic_label=meta_kv.get("SYNTHETIC_LABEL", "Synthetic Simulation" if is_synth else ""),
            cell_count_hint=cell_hint,
            notes=meta_kv.get("NOTES", ""),
            extra=meta_kv,
        )

    def _parse_csv(self, text: str, filename: str) -> ParsedFlight:
        lines = text.splitlines()
        metadata = self._extract_comment_metadata(lines, filename)
        data_text = "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))
        if not data_text.strip():
            raise ParserError("CSV file contains only comments and no telemetry rows.")

        try:
            raw_df = pd.read_csv(io.StringIO(data_text))
        except Exception as exc:
            raise ParserError(f"Failed to read CSV telemetry: {exc}") from exc

        if raw_df.empty or len(raw_df.columns) < 2:
            raise ParserError("CSV does not contain valid multi-column telemetry data.")

        mapped_cols: dict[str, pd.Series] = {}
        clip_parts: list[pd.Series] = []
        for col in raw_df.columns:
            nk = _norm_key(str(col))
            if nk in ("vibeclip0", "vibeclip1", "vibeclip2", "clip0", "clip1", "clip2"):
                clip_parts.append(pd.to_numeric(raw_df[col], errors="coerce").fillna(0.0))
            canonical = CSV_COLUMN_ALIASES.get(nk)
            if canonical and canonical not in mapped_cols:
                mapped_cols[canonical] = raw_df[col]

        if clip_parts and "clip_count" not in mapped_cols:
            mapped_cols["clip_count"] = sum(clip_parts[1:], clip_parts[0])
        elif len(clip_parts) > 1:
            mapped_cols["clip_count"] = sum(clip_parts[1:], clip_parts[0])

        if "timestamp_s" not in mapped_cols:
            # Synthesize 10 Hz index only if explicit sample index exists? No, require timestamp or index
            raise ParserError("CSV telemetry is missing a recognizable timestamp column.")

        df = pd.DataFrame(mapped_cols)
        df["timestamp_s"] = normalize_time_column(df["timestamp_s"])

        # Normalize ArduPilot CTUN.ThO if 0..1 normalized instead of 0..100%
        if "throttle_pct" in df.columns:
            thr = pd.to_numeric(df["throttle_pct"], errors="coerce")
            if thr.notna().any() and float(thr.max()) <= 1.25 and float(thr.min()) >= 0.0:
                df["throttle_pct"] = thr * 100.0

        # Scale GPS lat/lon if integer 1e7 representation
        for coord in ("gps_lat", "gps_lon"):
            if coord in df.columns:
                vals = pd.to_numeric(df[coord], errors="coerce")
                if vals.notna().any() and float(vals.abs().max()) > 180.0:
                    df[coord] = vals * 1e-7

        final_df = self.finalize_dataframe(df)
        raw_events = self._extract_mode_events(final_df)
        return ParsedFlight(metadata=metadata, dataframe=final_df, raw_events=raw_events)

    def _parse_dataflash_text_log(self, text: str, filename: str) -> ParsedFlight:
        formats: dict[str, list[str]] = {}
        topic_rows: dict[str, list[dict[str, Any]]] = {
            "ATT": [],
            "BAT": [],
            "CURR": [],
            "VIBE": [],
            "GPS": [],
            "BARO": [],
            "CTUN": [],
            "MODE": [],
        }
        raw_events: list[ParsedEvent] = []
        firmware_info = "ArduPilot DataFlash"
        vehicle_type = "Multirotor"
        first_time_us: float | None = None

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            msg_type = parts[0]

            if msg_type == "FMT" and len(parts) >= 6:
                name = parts[3]
                cols = parts[5:]
                formats[name] = cols
                continue

            if msg_type == "MSG" and len(parts) >= 3:
                txt = ",".join(parts[2:])
                if "Ardu" in txt or "PX4" in txt:
                    firmware_info = txt
                if "Copter" in txt or "Quad" in txt:
                    vehicle_type = "Quadcopter"
                elif "Plane" in txt:
                    vehicle_type = "Fixed-Wing"
                try:
                    ts_us = float(parts[1])
                    if first_time_us is None:
                        first_time_us = ts_us
                    raw_events.append(
                        ParsedEvent(
                            timestamp_s=max(0.0, (ts_us - first_time_us) * 1e-6),
                            event_type="autopilot_msg",
                            title="Autopilot Message",
                            description=txt,
                            severity="info",
                        )
                    )
                except ValueError:
                    pass
                continue

            cols = formats.get(msg_type)
            if not cols:
                continue

            values = parts[1:]
            row: dict[str, Any] = {}
            for idx, col_name in enumerate(cols):
                if idx < len(values):
                    row[col_name] = values[idx]

            if "TimeUS" in row:
                try:
                    t_us = float(row["TimeUS"])
                    if first_time_us is None:
                        first_time_us = t_us
                    row["_t_s"] = (t_us - first_time_us) * 1e-6
                except ValueError:
                    continue
            else:
                continue

            if msg_type in topic_rows:
                topic_rows[msg_type].append(row)

        topic_frames: dict[str, pd.DataFrame] = {}

        if topic_rows["ATT"]:
            att_df = pd.DataFrame(topic_rows["ATT"])
            topic_frames["ATT"] = pd.DataFrame(
                {
                    "timestamp_s": pd.to_numeric(att_df["_t_s"], errors="coerce"),
                    "roll_deg": pd.to_numeric(att_df.get("Roll"), errors="coerce"),
                    "pitch_deg": pd.to_numeric(att_df.get("Pitch"), errors="coerce"),
                    "yaw_deg": pd.to_numeric(att_df.get("Yaw"), errors="coerce"),
                    "roll_des_deg": pd.to_numeric(att_df.get("DesRoll"), errors="coerce"),
                    "pitch_des_deg": pd.to_numeric(att_df.get("DesPitch"), errors="coerce"),
                    "yaw_des_deg": pd.to_numeric(att_df.get("DesYaw"), errors="coerce"),
                }
            )

        bat_list = topic_rows["BAT"] or topic_rows["CURR"]
        if bat_list:
            bat_df = pd.DataFrame(bat_list)
            topic_frames["BAT"] = pd.DataFrame(
                {
                    "timestamp_s": pd.to_numeric(bat_df["_t_s"], errors="coerce"),
                    "voltage_v": pd.to_numeric(bat_df.get("Volt"), errors="coerce"),
                    "current_a": pd.to_numeric(bat_df.get("Curr"), errors="coerce"),
                }
            )

        if topic_rows["VIBE"]:
            v_df = pd.DataFrame(topic_rows["VIBE"])
            c0 = pd.to_numeric(v_df.get("Clip0", 0), errors="coerce").fillna(0.0)
            c1 = pd.to_numeric(v_df.get("Clip1", 0), errors="coerce").fillna(0.0)
            c2 = pd.to_numeric(v_df.get("Clip2", 0), errors="coerce").fillna(0.0)
            topic_frames["VIBE"] = pd.DataFrame(
                {
                    "timestamp_s": pd.to_numeric(v_df["_t_s"], errors="coerce"),
                    "vibe_x": pd.to_numeric(v_df.get("VibeX"), errors="coerce"),
                    "vibe_y": pd.to_numeric(v_df.get("VibeY"), errors="coerce"),
                    "vibe_z": pd.to_numeric(v_df.get("VibeZ"), errors="coerce"),
                    "clip_count": c0 + c1 + c2,
                }
            )

        if topic_rows["GPS"]:
            g_df = pd.DataFrame(topic_rows["GPS"])
            lat = pd.to_numeric(g_df.get("Lat"), errors="coerce")
            lng = pd.to_numeric(g_df.get("Lng"), errors="coerce")
            if lat.notna().any() and float(lat.abs().max()) > 180.0:
                lat = lat * 1e-7
            if lng.notna().any() and float(lng.abs().max()) > 180.0:
                lng = lng * 1e-7
            topic_frames["GPS"] = pd.DataFrame(
                {
                    "timestamp_s": pd.to_numeric(g_df["_t_s"], errors="coerce"),
                    "gps_lat": lat,
                    "gps_lon": lng,
                    "gps_alt_m": pd.to_numeric(g_df.get("Alt"), errors="coerce"),
                    "gps_hdop": pd.to_numeric(g_df.get("HDop"), errors="coerce"),
                    "gps_nsats": pd.to_numeric(g_df.get("NSats"), errors="coerce"),
                    "gps_speed_ms": pd.to_numeric(g_df.get("Spd"), errors="coerce"),
                }
            )

        if topic_rows["BARO"]:
            b_df = pd.DataFrame(topic_rows["BARO"])
            topic_frames["BARO"] = pd.DataFrame(
                {
                    "timestamp_s": pd.to_numeric(b_df["_t_s"], errors="coerce"),
                    "baro_alt_m": pd.to_numeric(b_df.get("Alt"), errors="coerce"),
                }
            )

        if topic_rows["CTUN"]:
            c_df = pd.DataFrame(topic_rows["CTUN"])
            tho = pd.to_numeric(c_df.get("ThO"), errors="coerce")
            if tho.notna().any() and float(tho.max()) <= 1.25:
                tho = tho * 100.0
            topic_frames["CTUN"] = pd.DataFrame(
                {
                    "timestamp_s": pd.to_numeric(c_df["_t_s"], errors="coerce"),
                    "throttle_pct": tho,
                }
            )

        if topic_rows["MODE"]:
            m_df = pd.DataFrame(topic_rows["MODE"])
            modes: list[str] = []
            for _, mr in m_df.iterrows():
                m_val = mr.get("Mode")
                try:
                    m_str = ARDUPILOT_MODES.get(int(float(str(m_val))), str(m_val))
                except ValueError:
                    m_str = str(m_val)
                modes.append(m_str)
            topic_frames["MODE"] = pd.DataFrame(
                {
                    "timestamp_s": pd.to_numeric(m_df["_t_s"], errors="coerce"),
                    "flight_mode": modes,
                }
            )

        merged = merge_topic_frames(topic_frames)
        final_df = self.finalize_dataframe(merged)
        raw_events.extend(self._extract_mode_events(final_df))
        metadata = FlightMetadata(
            format_id=self.format_id,
            autopilot="ArduPilot",
            vehicle_type=vehicle_type,
            firmware_version=firmware_info,
            source_filename=filename,
            is_synthetic="synthetic" in filename.lower(),
            synthetic_label="Synthetic Simulation" if "synthetic" in filename.lower() else "",
        )
        return ParsedFlight(metadata=metadata, dataframe=final_df, raw_events=raw_events)

    @staticmethod
    def _extract_mode_events(df: pd.DataFrame) -> list[ParsedEvent]:
        events: list[ParsedEvent] = []
        if "flight_mode" not in df.columns or df.empty:
            return events
        prev_mode: str | None = None
        for idx in range(len(df)):
            mode = str(df["flight_mode"].iloc[idx])
            if mode and mode != "UNKNOWN" and mode != prev_mode:
                ts = float(df["timestamp_s"].iloc[idx])
                events.append(
                    ParsedEvent(
                        timestamp_s=ts,
                        event_type="mode_change",
                        title=f"Flight Mode -> {mode}",
                        description=f"Autopilot entered {mode} mode at t={ts:.1f}s.",
                        severity="info",
                    )
                )
                prev_mode = mode
        return events
