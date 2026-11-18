"""MAVLink telemetry parser supporting JSON streams (.tlog.json) and binary .tlog packets."""

from __future__ import annotations

import json
import math
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


# Exact MAVLink wire-order struct definitions (fields sorted descending by size)
MAVLINK_WIRE_SPECS: dict[int, tuple[str, str, tuple[str, ...]]] = {
    0: (
        "HEARTBEAT",
        "<IBBBBB",
        ("custom_mode", "type", "autopilot", "base_mode", "system_status", "mavlink_version"),
    ),
    1: (
        "SYS_STATUS",
        "<IIIHHhHHHHHHb",
        (
            "onboard_control_sensors_present",
            "onboard_control_sensors_enabled",
            "onboard_control_sensors_health",
            "load",
            "voltage_battery",
            "current_battery",
            "drop_rate_comm",
            "errors_comm",
            "errors_count1",
            "errors_count2",
            "errors_count3",
            "errors_count4",
            "battery_remaining",
        ),
    ),
    24: (
        "GPS_RAW_INT",
        "<QiiiHHHHBB",
        (
            "time_usec",
            "lat",
            "lon",
            "alt",
            "eph",
            "epv",
            "vel",
            "cog",
            "fix_type",
            "satellites_visible",
        ),
    ),
    30: (
        "ATTITUDE",
        "<Iffffff",
        ("time_boot_ms", "roll", "pitch", "yaw", "rollspeed", "pitchspeed", "yawspeed"),
    ),
    33: (
        "GLOBAL_POSITION_INT",
        "<IiiiihhhH",
        ("time_boot_ms", "lat", "lon", "alt", "relative_alt", "vx", "vy", "vz", "hdg"),
    ),
    74: (
        "VFR_HUD",
        "<ffffhH",
        ("airspeed", "groundspeed", "alt", "climb", "heading", "throttle"),
    ),
    83: (
        "ATTITUDE_TARGET",
        "<IffffffffB",
        (
            "time_boot_ms",
            "q0",
            "q1",
            "q2",
            "q3",
            "body_roll_rate",
            "body_pitch_rate",
            "body_yaw_rate",
            "thrust",
            "type_mask",
        ),
    ),
    147: (
        "BATTERY_STATUS",
        "<iih10HhBBBb",
        (
            "current_consumed",
            "energy_consumed",
            "temperature",
            "v0",
            "v1",
            "v2",
            "v3",
            "v4",
            "v5",
            "v6",
            "v7",
            "v8",
            "v9",
            "current_battery",
            "id",
            "battery_function",
            "type",
            "battery_remaining",
        ),
    ),
    241: (
        "VIBRATION",
        "<QfffIII",
        (
            "time_usec",
            "vibration_x",
            "vibration_y",
            "vibration_z",
            "clipping_0",
            "clipping_1",
            "clipping_2",
        ),
    ),
    253: ("STATUSTEXT", "<B50s", ("severity", "text")),
}


class MAVLinkParser(BaseFlightParser):
    """Parser for MAVLink telemetry streams (.tlog.json and binary .tlog)."""

    format_id = "mavlink_tlog"
    display_name = "MAVLink Telemetry (.tlog / .tlog.json)"
    supported_extensions = (".tlog", ".tlog.json")

    def can_parse(self, filename: str, content: bytes) -> bool:
        lower = filename.lower()
        if lower.endswith((".tlog", ".tlog.json")):
            return True
        if lower.endswith(".json"):
            snippet = content[:4096].decode("utf-8", errors="ignore")
            if '"mavpackettype"' in snippet or '"mavlink_tlog"' in snippet or '"mavlink_messages"' in snippet:
                return True
        # Check binary tlog header: 8-byte timestamp + 0xFE/0xFD or immediate 0xFE/0xFD
        if len(content) >= 12 and (content[8] in (0xFE, 0xFD) or content[0] in (0xFE, 0xFD)):
            return True
        return False

    def parse(self, content: bytes, filename: str = "") -> ParsedFlight:
        if not content:
            raise ParserError("Empty file provided to MAVLink parser.")

        stripped = content.lstrip()
        if stripped.startswith((b"{", b"[")):
            return self._parse_json_tlog(content.decode("utf-8-sig", errors="replace"), filename)
        return self._parse_binary_tlog(content, filename)

    def _parse_json_tlog(self, text: str, filename: str) -> ParsedFlight:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParserError(f"Invalid MAVLink JSON payload: {exc}") from exc

        if isinstance(payload, list):
            messages = payload
            meta_raw: dict[str, Any] = {}
        elif isinstance(payload, dict):
            meta_raw = payload.get("metadata", {}) or {}
            messages = (
                payload.get("messages")
                or payload.get("mavlink_messages")
                or payload.get("records")
                or []
            )
        else:
            raise ParserError("MAVLink JSON root must be a dict or list.")

        if not messages:
            raise ParserError("MAVLink JSON contains zero telemetry messages.")

        is_synth = bool(meta_raw.get("is_synthetic", False)) or "synthetic" in filename.lower()
        metadata = FlightMetadata(
            format_id=self.format_id,
            autopilot=str(meta_raw.get("autopilot", "MAVLink Autopilot")),
            vehicle_type=str(meta_raw.get("vehicle_type", "Multirotor")),
            firmware_version=str(meta_raw.get("firmware_version", "MAVLink v2")),
            source_filename=filename,
            is_synthetic=is_synth,
            synthetic_label=str(
                meta_raw.get("synthetic_label", "Synthetic Simulation" if is_synth else "")
            ),
            cell_count_hint=meta_raw.get("cell_count_hint"),
            notes=str(meta_raw.get("notes", "")),
            extra=dict(meta_raw),
        )
        return self._messages_to_flight(messages, metadata)

    def _parse_binary_tlog(self, content: bytes, filename: str) -> ParsedFlight:
        messages: list[dict[str, Any]] = []
        offset = 0
        n = len(content)
        first_ts_us: int | None = None

        while offset < n:
            pkt_ts_s: float | None = None
            # Detect QGC 8-byte big-endian uint64 timestamp prefix before 0xFE/0xFD
            if offset + 10 <= n and content[offset] not in (0xFE, 0xFD) and content[offset + 8] in (0xFE, 0xFD):
                ts_us = struct.unpack_from(">Q", content, offset)[0]
                if 1_000_000_000_000_000 <= ts_us <= 3_000_000_000_000_000:
                    if first_ts_us is None:
                        first_ts_us = ts_us
                    pkt_ts_s = (ts_us - first_ts_us) * 1e-6
                    offset += 8

            if offset >= n:
                break

            magic = content[offset]
            if magic == 0xFE:
                if offset + 8 > n:
                    break
                plen = content[offset + 1]
                msgid = content[offset + 5]
                frame_len = 6 + plen + 2
                if offset + frame_len > n:
                    break
                payload = content[offset + 6 : offset + 6 + plen]
                offset += frame_len
            elif magic == 0xFD:
                if offset + 12 > n:
                    break
                plen = content[offset + 1]
                incompat_flags = content[offset + 2]
                msgid = content[offset + 7] | (content[offset + 8] << 8) | (content[offset + 9] << 16)
                sig_len = 13 if (incompat_flags & 0x01) else 0
                frame_len = 10 + plen + 2 + sig_len
                if offset + frame_len > n:
                    break
                payload = content[offset + 10 : offset + 10 + plen]
                offset += frame_len
            else:
                offset += 1
                continue

            if msgid not in MAVLINK_WIRE_SPECS:
                continue

            msg_name, s_fmt, fields = MAVLINK_WIRE_SPECS[msgid]
            expected_len = struct.calcsize(s_fmt)
            # MAVLink v2 trims trailing zero bytes; pad back before unpacking
            padded = payload + b"\x00" * max(0, expected_len - len(payload))
            vals = struct.unpack_from(s_fmt, padded, 0)

            msg_dict: dict[str, Any] = {"mavpackettype": msg_name}
            if msg_name == "BATTERY_STATUS":
                # Pack voltages array into list
                msg_dict["current_consumed"] = vals[0]
                msg_dict["energy_consumed"] = vals[1]
                msg_dict["temperature"] = vals[2]
                msg_dict["voltages"] = list(vals[3:13])
                msg_dict["current_battery"] = vals[13]
                msg_dict["battery_remaining"] = vals[17]
            elif msg_name == "ATTITUDE_TARGET":
                msg_dict["time_boot_ms"] = vals[0]
                msg_dict["q"] = list(vals[1:5])
                msg_dict["thrust"] = vals[8]
            elif msg_name == "STATUSTEXT":
                msg_dict["severity"] = vals[0]
                msg_dict["text"] = vals[1].decode("utf-8", errors="ignore").rstrip("\x00")
            else:
                for k, v in zip(fields, vals):
                    msg_dict[k] = v

            if pkt_ts_s is not None:
                msg_dict["timestamp_s"] = pkt_ts_s
            messages.append(msg_dict)

        if not messages:
            raise ParserError("No recognizable MAVLink packets found in binary .tlog file.")

        is_synth = "synthetic" in filename.lower()
        metadata = FlightMetadata(
            format_id=self.format_id,
            autopilot="MAVLink Autopilot",
            vehicle_type="Multirotor",
            firmware_version="MAVLink Binary .tlog",
            source_filename=filename,
            is_synthetic=is_synth,
            synthetic_label="Synthetic Simulation" if is_synth else "",
        )
        return self._messages_to_flight(messages, metadata)

    def _messages_to_flight(self, messages: list[dict[str, Any]], metadata: FlightMetadata) -> ParsedFlight:
        grouped: dict[str, list[dict[str, Any]]] = {}
        raw_events: list[ParsedEvent] = []

        # Establish global time zero across messages
        raw_times: list[float] = []
        for idx, msg in enumerate(messages):
            t = self._extract_msg_time_s(msg)
            if t is not None:
                raw_times.append(t)

        t0 = min(raw_times) if raw_times else 0.0
        last_seen_t = 0.0

        for idx, msg in enumerate(messages):
            mtype = str(msg.get("mavpackettype", msg.get("type", ""))).upper()
            t_abs = self._extract_msg_time_s(msg)
            if t_abs is None:
                t_rel = last_seen_t
            else:
                t_rel = max(0.0, t_abs - t0)
                last_seen_t = t_rel

            entry = dict(msg)
            entry["_t_s"] = t_rel
            if mtype:
                grouped.setdefault(mtype, []).append(entry)
            else:
                grouped.setdefault("FLAT", []).append(entry)

            if mtype == "STATUSTEXT":
                sev_num = int(msg.get("severity", 6))
                sev = "critical" if sev_num <= 3 else ("warning" if sev_num <= 4 else "info")
                raw_events.append(
                    ParsedEvent(
                        timestamp_s=t_rel,
                        event_type="autopilot_msg",
                        title="MAVLink STATUSTEXT",
                        description=str(msg.get("text", "")),
                        severity=sev,
                    )
                )

        if "FLAT" in grouped and len(grouped) == 1:
            fdf = pd.DataFrame(grouped["FLAT"])
            fdf["timestamp_s"] = fdf["_t_s"]
            final_df = self.finalize_dataframe(fdf)
            return ParsedFlight(metadata=metadata, dataframe=final_df, raw_events=raw_events)

        topic_frames: dict[str, pd.DataFrame] = {}

        if "ATTITUDE" in grouped:
            adf = pd.DataFrame(grouped["ATTITUDE"])
            r = pd.to_numeric(adf.get("roll"), errors="coerce")
            p = pd.to_numeric(adf.get("pitch"), errors="coerce")
            y = pd.to_numeric(adf.get("yaw"), errors="coerce")
            # MAVLink ATTITUDE is in radians unless explicit *_deg fields exist
            if "roll_deg" in adf.columns:
                r = pd.to_numeric(adf["roll_deg"], errors="coerce")
                p = pd.to_numeric(adf["pitch_deg"], errors="coerce")
                y = pd.to_numeric(adf["yaw_deg"], errors="coerce")
            else:
                r = np.degrees(r)
                p = np.degrees(p)
                y = np.degrees(y)
            topic_frames["ATTITUDE"] = pd.DataFrame(
                {"timestamp_s": adf["_t_s"], "roll_deg": r, "pitch_deg": p, "yaw_deg": y}
            )

        if "ATTITUDE_TARGET" in grouped:
            tdf = pd.DataFrame(grouped["ATTITUDE_TARGET"])
            if {"roll_des_deg", "pitch_des_deg", "yaw_des_deg"}.issubset(tdf.columns):
                rd = pd.to_numeric(tdf["roll_des_deg"], errors="coerce")
                pd_ = pd.to_numeric(tdf["pitch_des_deg"], errors="coerce")
                yd = pd.to_numeric(tdf["yaw_des_deg"], errors="coerce")
            else:
                rd, pd_, yd = [], [], []
                for _, row in tdf.iterrows():
                    q = row.get("q")
                    if isinstance(q, (list, tuple)) and len(q) == 4:
                        rr, pp, yy = quaternion_to_euler_deg(float(q[0]), float(q[1]), float(q[2]), float(q[3]))
                    else:
                        rr, pp, yy = quaternion_to_euler_deg(
                            float(row.get("q0", 1.0)),
                            float(row.get("q1", 0.0)),
                            float(row.get("q2", 0.0)),
                            float(row.get("q3", 0.0)),
                        )
                    rd.append(rr)
                    pd_.append(pp)
                    yd.append(yy)
            thr = pd.to_numeric(tdf.get("thrust"), errors="coerce")
            if thr.notna().any() and float(thr.max()) <= 1.25:
                thr = thr * 100.0
            topic_frames["ATTITUDE_TARGET"] = pd.DataFrame(
                {
                    "timestamp_s": tdf["_t_s"],
                    "roll_des_deg": rd,
                    "pitch_des_deg": pd_,
                    "yaw_des_deg": yd,
                    "throttle_pct": thr,
                }
            )

        if "SYS_STATUS" in grouped:
            sdf = pd.DataFrame(grouped["SYS_STATUS"])
            mv = pd.to_numeric(sdf.get("voltage_battery"), errors="coerce")
            volt = mv / 1000.0 if (mv.notna().any() and float(mv.max()) > 100.0) else mv
            ca = pd.to_numeric(sdf.get("current_battery"), errors="coerce")
            curr = ca / 100.0  # MAVLink cA -> A
            rem = pd.to_numeric(sdf.get("battery_remaining"), errors="coerce")
            topic_frames["SYS_STATUS"] = pd.DataFrame(
                {
                    "timestamp_s": sdf["_t_s"],
                    "voltage_v": volt,
                    "current_a": curr,
                    "battery_remaining_pct": rem,
                }
            )

        if "BATTERY_STATUS" in grouped:
            bdf = pd.DataFrame(grouped["BATTERY_STATUS"])
            volts: list[float] = []
            for _, row in bdf.iterrows():
                arr = row.get("voltages")
                if isinstance(arr, (list, tuple)):
                    valid_cells = [float(x) for x in arr if x is not None and 0 < int(x) < 65535]
                    volts.append(sum(valid_cells) / 1000.0 if valid_cells else np.nan)
                else:
                    volts.append(float(row.get("voltage_v", np.nan)))
            ca = pd.to_numeric(bdf.get("current_battery"), errors="coerce")
            curr = ca / 100.0 if (ca.notna().any() and float(ca.abs().max()) > 200.0) else ca
            rem = pd.to_numeric(bdf.get("battery_remaining"), errors="coerce")
            topic_frames["BATTERY_STATUS"] = pd.DataFrame(
                {
                    "timestamp_s": bdf["_t_s"],
                    "voltage_v": volts,
                    "current_a": curr,
                    "battery_remaining_pct": rem,
                }
            )

        if "VIBRATION" in grouped:
            vdf = pd.DataFrame(grouped["VIBRATION"])
            c0 = pd.to_numeric(vdf.get("clipping_0", 0), errors="coerce").fillna(0.0)
            c1 = pd.to_numeric(vdf.get("clipping_1", 0), errors="coerce").fillna(0.0)
            c2 = pd.to_numeric(vdf.get("clipping_2", 0), errors="coerce").fillna(0.0)
            topic_frames["VIBRATION"] = pd.DataFrame(
                {
                    "timestamp_s": vdf["_t_s"],
                    "vibe_x": pd.to_numeric(vdf.get("vibration_x"), errors="coerce"),
                    "vibe_y": pd.to_numeric(vdf.get("vibration_y"), errors="coerce"),
                    "vibe_z": pd.to_numeric(vdf.get("vibration_z"), errors="coerce"),
                    "clip_count": c0 + c1 + c2,
                }
            )

        if "GPS_RAW_INT" in grouped:
            gdf = pd.DataFrame(grouped["GPS_RAW_INT"])
            lat = pd.to_numeric(gdf.get("lat"), errors="coerce")
            lon = pd.to_numeric(gdf.get("lon"), errors="coerce")
            if lat.notna().any() and float(lat.abs().max()) > 180.0:
                lat = lat * 1e-7
            if lon.notna().any() and float(lon.abs().max()) > 180.0:
                lon = lon * 1e-7
            alt = pd.to_numeric(gdf.get("alt"), errors="coerce")
            if alt.notna().any() and float(alt.abs().max()) > 20000.0:
                alt = alt * 1e-3
            eph = pd.to_numeric(gdf.get("eph"), errors="coerce")
            # MAVLink eph is in cm (e.g. 120 = HDOP 1.2)
            hdop = eph / 100.0 if (eph.notna().any() and float(eph.max()) > 25.0) else eph
            vel = pd.to_numeric(gdf.get("vel"), errors="coerce")
            spd = vel / 100.0 if (vel.notna().any() and float(vel.max()) > 100.0) else vel
            topic_frames["GPS_RAW_INT"] = pd.DataFrame(
                {
                    "timestamp_s": gdf["_t_s"],
                    "gps_lat": lat,
                    "gps_lon": lon,
                    "gps_alt_m": alt,
                    "gps_hdop": hdop,
                    "gps_nsats": pd.to_numeric(gdf.get("satellites_visible"), errors="coerce"),
                    "gps_speed_ms": spd,
                }
            )

        if "GLOBAL_POSITION_INT" in grouped:
            gpdf = pd.DataFrame(grouped["GLOBAL_POSITION_INT"])
            rel_alt = pd.to_numeric(gpdf.get("relative_alt"), errors="coerce")
            if rel_alt.notna().any() and float(rel_alt.abs().max()) > 5000.0:
                rel_alt = rel_alt * 1e-3
            topic_frames["GLOBAL_POSITION_INT"] = pd.DataFrame(
                {"timestamp_s": gpdf["_t_s"], "baro_alt_m": rel_alt}
            )

        if "VFR_HUD" in grouped:
            hud = pd.DataFrame(grouped["VFR_HUD"])
            topic_frames["VFR_HUD"] = pd.DataFrame(
                {
                    "timestamp_s": hud["_t_s"],
                    "gps_speed_ms": pd.to_numeric(hud.get("groundspeed"), errors="coerce"),
                    "baro_alt_m": pd.to_numeric(hud.get("alt"), errors="coerce"),
                    "throttle_pct": pd.to_numeric(hud.get("throttle"), errors="coerce"),
                }
            )

        if "HEARTBEAT" in grouped:
            hb = pd.DataFrame(grouped["HEARTBEAT"])
            if "mode" in hb.columns or "flight_mode" in hb.columns:
                modes = hb.get("flight_mode", hb.get("mode")).astype(str).tolist()
                topic_frames["HEARTBEAT"] = pd.DataFrame({"timestamp_s": hb["_t_s"], "flight_mode": modes})

        merged = merge_topic_frames(topic_frames)
        final_df = self.finalize_dataframe(merged)
        return ParsedFlight(metadata=metadata, dataframe=final_df, raw_events=raw_events)

    @staticmethod
    def _extract_msg_time_s(msg: dict[str, Any]) -> float | None:
        if "timestamp_s" in msg and msg["timestamp_s"] is not None:
            return float(msg["timestamp_s"])
        if "time_boot_ms" in msg and msg["time_boot_ms"] is not None:
            return float(msg["time_boot_ms"]) * 1e-3
        if "time_usec" in msg and msg["time_usec"] is not None:
            return float(msg["time_usec"]) * 1e-6
        if "timestamp" in msg and msg["timestamp"] is not None:
            v = float(msg["timestamp"])
            return v * 1e-6 if v > 1e11 else v
        return None
