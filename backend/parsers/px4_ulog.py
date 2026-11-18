"""PX4 ULog parser supporting binary (.ulg), structured JSON (.ulog.json), and CSV (.ulog.csv)."""

from __future__ import annotations

import io
import json
import math
import re
import struct
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
    quaternion_to_euler_deg,
)


ULOG_MAGIC = b"ULog\x01\x12\x35"

ULOG_PRIMITIVE_FORMATS: dict[str, tuple[str, int]] = {
    "int8_t": ("b", 1),
    "uint8_t": ("B", 1),
    "int16_t": ("h", 2),
    "uint16_t": ("H", 2),
    "int32_t": ("i", 4),
    "uint32_t": ("I", 4),
    "int64_t": ("q", 8),
    "uint64_t": ("Q", 8),
    "float": ("f", 4),
    "double": ("d", 8),
    "bool": ("?", 1),
    "char": ("c", 1),
}

PX4_NAV_STATES: dict[int, str] = {
    0: "MANUAL",
    1: "ALTCTL",
    2: "POSCTL",
    3: "AUTO_MISSION",
    4: "AUTO_LOITER",
    5: "AUTO_RTL",
    14: "OFFBOARD",
    15: "STAB",
    17: "AUTO_TAKEOFF",
    18: "AUTO_LAND",
}


class PX4ULogParser(BaseFlightParser):
    """Parser for PX4 ULog binary (.ulg), structured ULog JSON (.ulog.json), and ULog CSV."""

    format_id = "px4_ulog"
    display_name = "PX4 ULog (.ulg / .ulog.json / .ulog.csv)"
    supported_extensions = (".ulg", ".ulog", ".ulog.json", ".ulog.csv")

    def can_parse(self, filename: str, content: bytes) -> bool:
        lower_name = filename.lower()
        if content.startswith(ULOG_MAGIC):
            return True
        if lower_name.endswith((".ulg", ".ulog", ".ulog.json", ".ulog.csv")):
            return True
        if lower_name.endswith(".json"):
            snippet = content[:4096].decode("utf-8", errors="ignore")
            if '"px4_ulog"' in snippet or '"vehicle_attitude"' in snippet or '"topics"' in snippet:
                return True
        if lower_name.endswith(".csv"):
            head = content[:2048].decode("utf-8", errors="ignore")
            if "vehicle_attitude" in head or ("q[0]" in head and "timestamp" in head):
                return True
        return False

    def parse(self, content: bytes, filename: str = "") -> ParsedFlight:
        if not content:
            raise ParserError("Empty file provided to PX4 ULog parser.")

        if content.startswith(ULOG_MAGIC):
            return self._parse_binary_ulg(content, filename)

        lower_name = filename.lower()
        if lower_name.endswith(".csv"):
            return self._parse_ulog_csv(content.decode("utf-8-sig", errors="replace"), filename)

        text = content.decode("utf-8-sig", errors="replace").strip()
        if text.startswith("{") or text.startswith("["):
            return self._parse_ulog_json(text, filename)

        if lower_name.endswith((".ulg", ".ulog")):
            raise ParserError("Invalid PX4 binary ULog header magic bytes.")

        return self._parse_ulog_csv(text, filename)

    def _parse_ulog_json(self, text: str, filename: str) -> ParsedFlight:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParserError(f"Invalid ULog JSON payload: {exc}") from exc

        if isinstance(payload, list):
            payload = {"topics": {}, "records": payload, "metadata": {}}

        if not isinstance(payload, dict):
            raise ParserError("ULog JSON root must be an object or array.")

        meta_raw = payload.get("metadata", {}) or {}
        is_synth = bool(meta_raw.get("is_synthetic", False)) or "synthetic" in filename.lower()
        metadata = FlightMetadata(
            format_id=self.format_id,
            autopilot=str(meta_raw.get("autopilot", "PX4 Autopilot")),
            vehicle_type=str(meta_raw.get("vehicle_type", "Multirotor")),
            firmware_version=str(meta_raw.get("firmware_version", "PX4 v1.14")),
            source_filename=filename,
            is_synthetic=is_synth,
            synthetic_label=str(
                meta_raw.get("synthetic_label", "Synthetic Simulation" if is_synth else "")
            ),
            cell_count_hint=meta_raw.get("cell_count_hint"),
            notes=str(meta_raw.get("notes", "")),
            extra=dict(meta_raw),
        )

        raw_events: list[ParsedEvent] = []
        for ev in payload.get("events", []) or []:
            if isinstance(ev, dict):
                raw_events.append(
                    ParsedEvent(
                        timestamp_s=float(ev.get("timestamp_s", 0.0)),
                        event_type=str(ev.get("event_type", "autopilot_msg")),
                        title=str(ev.get("title", "PX4 Event")),
                        description=str(ev.get("description", "")),
                        severity=str(ev.get("severity", "info")),
                    )
                )

        if "records" in payload and isinstance(payload["records"], list) and payload["records"]:
            df = pd.DataFrame(payload["records"])
            return self._finalize_json_records(df, metadata, raw_events)

        topics = payload.get("topics")
        if not isinstance(topics, dict) or not topics:
            raise ParserError("PX4 ULog JSON contains neither 'topics' nor 'records'.")

        topic_frames = self._build_frames_from_topic_dicts(topics, raw_events)
        merged = merge_topic_frames(topic_frames)
        final_df = self.finalize_dataframe(merged)
        return ParsedFlight(metadata=metadata, dataframe=final_df, raw_events=raw_events)

    def _finalize_json_records(
        self, df: pd.DataFrame, metadata: FlightMetadata, raw_events: list[ParsedEvent]
    ) -> ParsedFlight:
        if "timestamp_s" not in df.columns:
            for alt in ("timestamp", "time_us", "time_s"):
                if alt in df.columns:
                    df["timestamp_s"] = normalize_time_column(df[alt])
                    break
        else:
            df["timestamp_s"] = normalize_time_column(df["timestamp_s"])
        final_df = self.finalize_dataframe(df)
        return ParsedFlight(metadata=metadata, dataframe=final_df, raw_events=raw_events)

    def _build_frames_from_topic_dicts(
        self, topics: dict[str, list[dict[str, Any]]], raw_events: list[ParsedEvent]
    ) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}

        def _time_series(tdf: pd.DataFrame) -> pd.Series:
            for cand in ("timestamp_s", "timestamp", "time_us", "time_s"):
                if cand in tdf.columns:
                    return normalize_time_column(tdf[cand])
            raise ParserError("PX4 topic missing timestamp field.")

        def _num_col(tdf: pd.DataFrame, *names: str) -> pd.Series:
            for name in names:
                if name in tdf.columns:
                    return pd.to_numeric(tdf[name], errors="coerce")
            return pd.Series(np.nan, index=tdf.index, dtype=float)

        att_rows = topics.get("vehicle_attitude")
        if att_rows:
            adf = pd.DataFrame(att_rows)
            ts = _time_series(adf)
            if {"roll_deg", "pitch_deg", "yaw_deg"}.issubset(adf.columns):
                r, p, y = adf["roll_deg"], adf["pitch_deg"], adf["yaw_deg"]
            else:
                rolls, pitches, yaws = [], [], []
                for _, row in adf.iterrows():
                    q = row.get("q")
                    if isinstance(q, (list, tuple)) and len(q) == 4:
                        rr, pp, yy = quaternion_to_euler_deg(float(q[0]), float(q[1]), float(q[2]), float(q[3]))
                    else:
                        rr, pp, yy = quaternion_to_euler_deg(
                            float(row.get("q[0]", row.get("q0", 1.0))),
                            float(row.get("q[1]", row.get("q1", 0.0))),
                            float(row.get("q[2]", row.get("q2", 0.0))),
                            float(row.get("q[3]", row.get("q3", 0.0))),
                        )
                    rolls.append(rr)
                    pitches.append(pp)
                    yaws.append(yy)
                r, p, y = rolls, pitches, yaws
            frames["vehicle_attitude"] = pd.DataFrame(
                {"timestamp_s": ts, "roll_deg": r, "pitch_deg": p, "yaw_deg": y}
            )

        att_sp_rows = topics.get("vehicle_attitude_setpoint")
        if att_sp_rows:
            sdf = pd.DataFrame(att_sp_rows)
            ts = _time_series(sdf)
            if {"roll_des_deg", "pitch_des_deg", "yaw_des_deg"}.issubset(sdf.columns):
                rd, pd_, yd = sdf["roll_des_deg"], sdf["pitch_des_deg"], sdf["yaw_des_deg"]
            elif {"roll_body", "pitch_body", "yaw_body"}.issubset(sdf.columns):
                rd = np.degrees(pd.to_numeric(sdf["roll_body"], errors="coerce"))
                pd_ = np.degrees(pd.to_numeric(sdf["pitch_body"], errors="coerce"))
                yd = np.degrees(pd.to_numeric(sdf["yaw_body"], errors="coerce"))
            else:
                rd, pd_, yd = [], [], []
                for _, row in sdf.iterrows():
                    qd = row.get("q_d")
                    if isinstance(qd, (list, tuple)) and len(qd) == 4:
                        rr, pp, yy = quaternion_to_euler_deg(
                            float(qd[0]), float(qd[1]), float(qd[2]), float(qd[3])
                        )
                    else:
                        rr, pp, yy = quaternion_to_euler_deg(
                            float(row.get("q_d[0]", 1.0)),
                            float(row.get("q_d[1]", 0.0)),
                            float(row.get("q_d[2]", 0.0)),
                            float(row.get("q_d[3]", 0.0)),
                        )
                    rd.append(rr)
                    pd_.append(pp)
                    yd.append(yy)

            thr = pd.Series(np.nan, index=sdf.index)
            for tc in ("thrust_body[2]", "thrust", "throttle_pct"):
                if tc in sdf.columns:
                    raw_t = pd.to_numeric(sdf[tc], errors="coerce")
                    if tc == "thrust_body[2]":
                        thr = raw_t.abs() * 100.0
                    elif raw_t.notna().any() and float(raw_t.max()) <= 1.25:
                        thr = raw_t * 100.0
                    else:
                        thr = raw_t
                    break

            frames["vehicle_attitude_setpoint"] = pd.DataFrame(
                {
                    "timestamp_s": ts,
                    "roll_des_deg": rd,
                    "pitch_des_deg": pd_,
                    "yaw_des_deg": yd,
                    "throttle_pct": thr,
                }
            )

        bat_rows = topics.get("battery_status")
        if bat_rows:
            bdf = pd.DataFrame(bat_rows)
            ts = _time_series(bdf)
            v = _num_col(bdf, "voltage_v", "voltage_filtered_v")
            c = _num_col(bdf, "current_a", "current_filtered_a")
            rem = _num_col(bdf, "remaining", "battery_remaining_pct")
            if rem.notna().any() and float(rem.max()) <= 1.05:
                rem = rem * 100.0
            frames["battery_status"] = pd.DataFrame(
                {
                    "timestamp_s": ts,
                    "voltage_v": v,
                    "current_a": c,
                    "battery_remaining_pct": rem,
                }
            )

        vibe_rows = topics.get("vibe_metrics") or topics.get("estimator_sensor_bias")
        if vibe_rows:
            vdf = pd.DataFrame(vibe_rows)
            ts = _time_series(vdf)
            frames["vibe_metrics"] = pd.DataFrame(
                {
                    "timestamp_s": ts,
                    "vibe_x": pd.to_numeric(vdf.get("vibe_x", vdf.get("accel_vib_x")), errors="coerce"),
                    "vibe_y": pd.to_numeric(vdf.get("vibe_y", vdf.get("accel_vib_y")), errors="coerce"),
                    "vibe_z": pd.to_numeric(vdf.get("vibe_z", vdf.get("accel_vib_z")), errors="coerce"),
                    "clip_count": pd.to_numeric(vdf.get("clip_count", 0), errors="coerce"),
                }
            )
        elif topics.get("sensor_accel") or topics.get("vehicle_acceleration"):
            acc_rows = topics.get("vehicle_acceleration") or topics.get("sensor_accel")
            adf = pd.DataFrame(acc_rows)
            ts = _time_series(adf)
            if "xyz" in adf.columns and isinstance(adf["xyz"].iloc[0], (list, tuple)):
                arr = np.array(adf["xyz"].tolist(), dtype=float)
                ax, ay, az = arr[:, 0], arr[:, 1], arr[:, 2]
            else:
                ax = pd.to_numeric(adf.get("x", adf.get("xyz[0]")), errors="coerce").to_numpy(dtype=float)
                ay = pd.to_numeric(adf.get("y", adf.get("xyz[1]")), errors="coerce").to_numpy(dtype=float)
                az = pd.to_numeric(adf.get("z", adf.get("xyz[2]")), errors="coerce").to_numpy(dtype=float)
            # Remove quasi-static gravity/maneuver baseline via centered rolling mean to isolate vibration
            vx = pd.Series(ax) - pd.Series(ax).rolling(21, center=True, min_periods=1).mean()
            vy = pd.Series(ay) - pd.Series(ay).rolling(21, center=True, min_periods=1).mean()
            vz = pd.Series(az) - pd.Series(az).rolling(21, center=True, min_periods=1).mean()
            clip = pd.to_numeric(adf.get("clip_counter", 0), errors="coerce").fillna(0.0)
            frames["sensor_accel"] = pd.DataFrame(
                {
                    "timestamp_s": ts,
                    "vibe_x": vx,
                    "vibe_y": vy,
                    "vibe_z": vz,
                    "clip_count": clip,
                }
            )

        gps_rows = topics.get("vehicle_gps_position")
        if gps_rows:
            gdf = pd.DataFrame(gps_rows)
            ts = _time_series(gdf)
            lat = _num_col(gdf, "lat")
            lon = _num_col(gdf, "lon")
            if lat.notna().any() and float(lat.abs().max()) > 180.0:
                lat = lat * 1e-7
            if lon.notna().any() and float(lon.abs().max()) > 180.0:
                lon = lon * 1e-7
            alt = _num_col(gdf, "alt")
            if alt.notna().any() and float(alt.abs().max()) > 20000.0:
                alt = alt * 1e-3  # mm -> m
            hdop = _num_col(gdf, "hdop", "eph")
            sats = _num_col(gdf, "satellites_used", "nsats")
            spd = _num_col(gdf, "vel_m_s", "vel_d_m_s")
            frames["vehicle_gps_position"] = pd.DataFrame(
                {
                    "timestamp_s": ts,
                    "gps_lat": lat,
                    "gps_lon": lon,
                    "gps_alt_m": alt,
                    "gps_hdop": hdop,
                    "gps_nsats": sats,
                    "gps_speed_ms": spd,
                }
            )

        lpos_rows = topics.get("vehicle_local_position")
        if lpos_rows:
            ldf = pd.DataFrame(lpos_rows)
            ts = _time_series(ldf)
            alt_col = ldf["baro_alt_m"] if "baro_alt_m" in ldf.columns else -pd.to_numeric(ldf.get("z"), errors="coerce")
            frames["vehicle_local_position"] = pd.DataFrame(
                {
                    "timestamp_s": ts,
                    "baro_alt_m": pd.to_numeric(alt_col, errors="coerce"),
                }
            )

        st_rows = topics.get("vehicle_status")
        if st_rows:
            st_df = pd.DataFrame(st_rows)
            ts = _time_series(st_df)
            modes: list[str] = []
            prev_mode: str | None = None
            for i, row in st_df.iterrows():
                nav = row.get("nav_state")
                if "flight_mode" in row and pd.notna(row["flight_mode"]):
                    m_str = str(row["flight_mode"])
                elif pd.notna(nav):
                    try:
                        m_str = PX4_NAV_STATES.get(int(nav), f"NAV_{int(nav)}")
                    except (TypeError, ValueError):
                        m_str = str(nav)
                else:
                    m_str = "UNKNOWN"
                modes.append(m_str)
                if m_str != "UNKNOWN" and m_str != prev_mode:
                    t_val = float(ts.iloc[i])
                    raw_events.append(
                        ParsedEvent(
                            timestamp_s=t_val,
                            event_type="mode_change",
                            title=f"PX4 Mode -> {m_str}",
                            description=f"PX4 nav_state transitioned to {m_str} at t={t_val:.1f}s.",
                            severity="info",
                        )
                    )
                    prev_mode = m_str
            frames["vehicle_status"] = pd.DataFrame({"timestamp_s": ts, "flight_mode": modes})

        return frames

    def _parse_ulog_csv(self, text: str, filename: str) -> ParsedFlight:
        try:
            raw = pd.read_csv(io.StringIO(text))
        except Exception as exc:
            raise ParserError(f"Failed to parse ULog CSV: {exc}") from exc

        if raw.empty:
            raise ParserError("PX4 ULog CSV file is empty.")

        out = pd.DataFrame()
        if "timestamp" in raw.columns:
            out["timestamp_s"] = normalize_time_column(raw["timestamp"])
        elif "timestamp_s" in raw.columns:
            out["timestamp_s"] = normalize_time_column(raw["timestamp_s"])
        else:
            raise ParserError("PX4 ULog CSV missing 'timestamp' column.")

        if {"q[0]", "q[1]", "q[2]", "q[3]"}.issubset(raw.columns):
            eulers = [
                quaternion_to_euler_deg(float(r["q[0]"]), float(r["q[1]"]), float(r["q[2]"]), float(r["q[3]"]))
                for _, r in raw.iterrows()
            ]
            out["roll_deg"] = [e[0] for e in eulers]
            out["pitch_deg"] = [e[1] for e in eulers]
            out["yaw_deg"] = [e[2] for e in eulers]

        mapping = {
            "roll_deg": ["roll_deg", "roll"],
            "pitch_deg": ["pitch_deg", "pitch"],
            "yaw_deg": ["yaw_deg", "yaw"],
            "roll_des_deg": ["roll_des_deg", "roll_body"],
            "pitch_des_deg": ["pitch_des_deg", "pitch_body"],
            "voltage_v": ["voltage_v", "voltage_filtered_v"],
            "current_a": ["current_a", "current_filtered_a"],
            "vibe_x": ["vibe_x", "x"],
            "vibe_y": ["vibe_y", "y"],
            "vibe_z": ["vibe_z", "z"],
            "gps_lat": ["gps_lat", "lat"],
            "gps_lon": ["gps_lon", "lon"],
            "gps_alt_m": ["gps_alt_m", "alt"],
            "gps_hdop": ["gps_hdop", "hdop", "eph"],
            "gps_nsats": ["gps_nsats", "satellites_used"],
            "baro_alt_m": ["baro_alt_m"],
            "throttle_pct": ["throttle_pct"],
        }
        for target, cands in mapping.items():
            if target in out.columns:
                continue
            for c in cands:
                if c in raw.columns:
                    val = pd.to_numeric(raw[c], errors="coerce")
                    if c in ("roll_body", "pitch_body"):
                        val = np.degrees(val)
                    out[target] = val
                    break

        is_synth = "synthetic" in filename.lower()
        meta = FlightMetadata(
            format_id=self.format_id,
            autopilot="PX4 Autopilot",
            vehicle_type="Multirotor",
            firmware_version="PX4 ULog CSV",
            source_filename=filename,
            is_synthetic=is_synth,
            synthetic_label="Synthetic Simulation" if is_synth else "",
        )
        final_df = self.finalize_dataframe(out)
        return ParsedFlight(metadata=meta, dataframe=final_df, raw_events=[])

    # ---- Binary PX4 .ulg Decoder ----
    def _parse_binary_ulg(self, content: bytes, filename: str) -> ParsedFlight:
        if len(content) < 16 or not content.startswith(ULOG_MAGIC):
            raise ParserError("Invalid binary PX4 ULog file header.")

        _header_ts_us = struct.unpack_from("<Q", content, 8)[0]
        offset = 16

        formats_raw: dict[str, list[tuple[str, str, int]]] = {}
        compiled_formats: dict[str, tuple[str, list[str]]] = {}
        msg_id_map: dict[int, tuple[str, int]] = {}
        info: dict[str, Any] = {}
        topics: dict[str, list[dict[str, Any]]] = {}
        raw_events: list[ParsedEvent] = []

        def _compile_format(name: str) -> tuple[str, list[str]] | None:
            if name in compiled_formats:
                return compiled_formats[name]
            fields = formats_raw.get(name)
            if not fields:
                return None
            fmt_chars = ["<"]
            flat_names: list[str] = []
            for ftype, fname, arr_len in fields:
                if fname.startswith("_padding"):
                    fmt_chars.append(f"{arr_len}x")
                    continue
                if ftype in ULOG_PRIMITIVE_FORMATS:
                    ch, _ = ULOG_PRIMITIVE_FORMATS[ftype]
                    if arr_len > 1:
                        fmt_chars.append(f"{arr_len}{ch}")
                        for i in range(arr_len):
                            flat_names.append(f"{fname}[{i}]")
                    else:
                        fmt_chars.append(ch)
                        flat_names.append(fname)
                else:
                    nested = _compile_format(ftype)
                    if nested is None:
                        return None
                    n_fmt, n_names = nested
                    n_body = n_fmt[1:]
                    for i in range(arr_len):
                        fmt_chars.append(n_body)
                        prefix = f"{fname}[{i}]" if arr_len > 1 else fname
                        for nn in n_names:
                            flat_names.append(f"{prefix}.{nn}")
            res = ("".join(fmt_chars), flat_names)
            compiled_formats[name] = res
            return res

        total_len = len(content)
        while offset + 3 <= total_len:
            msg_size, msg_type_int = struct.unpack_from("<HB", content, offset)
            offset += 3
            if offset + msg_size > total_len:
                break
            payload = content[offset : offset + msg_size]
            offset += msg_size
            mtype = chr(msg_type_int)

            if mtype == "F":
                text = payload.decode("utf-8", errors="ignore")
                if ":" in text:
                    name, fdef = text.split(":", 1)
                    parsed_fields: list[tuple[str, str, int]] = []
                    for item in fdef.split(";"):
                        item = item.strip()
                        if not item:
                            continue
                        parts = item.split(" ", 1)
                        if len(parts) != 2:
                            continue
                        ftype_raw, fname = parts[0].strip(), parts[1].strip()
                        m = re.match(r"^([a-zA-Z0-9_]+)\[(\d+)\]$", ftype_raw)
                        if m:
                            parsed_fields.append((m.group(1), fname, int(m.group(2))))
                        else:
                            parsed_fields.append((ftype_raw, fname, 1))
                    formats_raw[name] = parsed_fields

            elif mtype in ("I", "M"):
                idx_offset = 1 if mtype == "M" else 0
                if len(payload) > idx_offset:
                    key_len = payload[idx_offset]
                    kstart = idx_offset + 1
                    kend = kstart + key_len
                    if kend <= len(payload):
                        key_type_name = payload[kstart:kend].decode("utf-8", errors="ignore")
                        val_bytes = payload[kend:]
                        if " " in key_type_name:
                            vtype, vname = key_type_name.split(" ", 1)
                            if vtype.startswith("char["):
                                info[vname] = val_bytes.decode("utf-8", errors="ignore").rstrip("\x00")
                            elif vtype in ULOG_PRIMITIVE_FORMATS:
                                ch, sz = ULOG_PRIMITIVE_FORMATS[vtype]
                                if len(val_bytes) >= sz:
                                    info[vname] = struct.unpack_from("<" + ch, val_bytes, 0)[0]

            elif mtype == "A" and len(payload) >= 3:
                multi_id = payload[0]
                msg_id = struct.unpack_from("<H", payload, 1)[0]
                topic_name = payload[3:].decode("utf-8", errors="ignore").rstrip("\x00")
                msg_id_map[msg_id] = (topic_name, multi_id)

            elif mtype == "D" and len(payload) >= 2:
                msg_id = struct.unpack_from("<H", payload, 0)[0]
                if msg_id not in msg_id_map:
                    continue
                topic_name, _multi_id = msg_id_map[msg_id]
                if topic_name not in (
                    "vehicle_attitude",
                    "vehicle_attitude_setpoint",
                    "battery_status",
                    "vehicle_gps_position",
                    "vehicle_local_position",
                    "sensor_accel",
                    "vehicle_acceleration",
                    "vehicle_status",
                ):
                    continue
                compiled = _compile_format(topic_name)
                if not compiled:
                    continue
                s_fmt, field_names = compiled
                expected_size = struct.calcsize(s_fmt)
                data_bytes = payload[2:]
                if len(data_bytes) < expected_size:
                    continue
                vals = struct.unpack_from(s_fmt, data_bytes, 0)
                row = dict(zip(field_names, vals))
                topics.setdefault(topic_name, []).append(row)

            elif mtype == "L" and len(payload) >= 9:
                level = payload[0]
                ts_us = struct.unpack_from("<Q", payload, 1)[0]
                msg_txt = payload[9:].decode("utf-8", errors="ignore").rstrip("\x00")
                sev = "critical" if level <= 3 else ("warning" if level <= 4 else "info")
                raw_events.append(
                    ParsedEvent(
                        timestamp_s=max(0.0, (ts_us - _header_ts_us) * 1e-6),
                        event_type="autopilot_msg",
                        title="PX4 Logged Message",
                        description=msg_txt,
                        severity=sev,
                    )
                )

        if not topics:
            raise ParserError("Binary PX4 ULog contained no supported telemetry topics.")

        topic_frames = self._build_frames_from_topic_dicts(topics, raw_events)
        merged = merge_topic_frames(topic_frames)
        final_df = self.finalize_dataframe(merged)

        is_synth = str(info.get("aerolens_synthetic", "")).lower() in ("1", "true") or (
            "synthetic" in filename.lower()
        )
        metadata = FlightMetadata(
            format_id=self.format_id,
            autopilot="PX4 Autopilot",
            vehicle_type=str(info.get("sys_name", "Multirotor")),
            firmware_version=str(info.get("ver_sw", "PX4 ULog Binary")),
            source_filename=filename,
            is_synthetic=is_synth,
            synthetic_label="Synthetic Simulation" if is_synth else "",
            extra=info,
        )
        return ParsedFlight(metadata=metadata, dataframe=final_df, raw_events=raw_events)
