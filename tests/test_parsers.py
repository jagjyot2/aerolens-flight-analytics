"""Unit tests for modular flight log parsers and registry auto-detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.parsers.ardupilot_csv import ArduPilotParser
from backend.parsers.base import CANONICAL_NUMERIC_COLUMNS, ParserError, quaternion_to_euler_deg
from backend.parsers.mavlink_tlog import MAVLinkParser
from backend.parsers.px4_ulog import PX4ULogParser
from backend.parsers.registry import default_registry
from tests.conftest import build_binary_mavlink_tlog_bytes, build_binary_ulg_bytes


def test_quaternion_to_euler_identity_and_roll() -> None:
    r, p, y = quaternion_to_euler_deg(1.0, 0.0, 0.0, 0.0)
    assert pytest.approx(r, abs=1e-6) == 0.0
    assert pytest.approx(p, abs=1e-6) == 0.0
    assert pytest.approx(y, abs=1e-6) == 0.0

    # 90 deg roll -> q = (cos 45, sin 45, 0, 0)
    import math
    r2, p2, y2 = quaternion_to_euler_deg(math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0)
    assert pytest.approx(r2, abs=1e-3) == 90.0
    assert pytest.approx(p2, abs=1e-3) == 0.0
    assert pytest.approx(y2, abs=1e-3) == 0.0


def test_ardupilot_csv_sample_parsing(sample_data_dir: Path) -> None:
    path = sample_data_dir / "synthetic_nominal_survey_flight.csv"
    parsed = default_registry.parse_bytes(path.read_bytes(), filename=path.name)

    assert parsed.metadata.format_id == "ardupilot_csv"
    assert parsed.metadata.is_synthetic is True
    assert parsed.metadata.cell_count_hint == 4
    assert parsed.sample_count == 2400
    assert pytest.approx(parsed.duration_s, abs=0.2) == 119.95
    for col in CANONICAL_NUMERIC_COLUMNS:
        assert col in parsed.dataframe.columns
    assert len(parsed.raw_events) >= 3  # mode transitions


def test_ardupilot_dataflash_text_log_parsing() -> None:
    log_text = "\n".join([
        "FMT, 128, 89, FMT, BBnNZ, Type,Length,Name,Format,Columns",
        "FMT, 130, 45, ATT, QccccCCC, TimeUS,DesRoll,Roll,DesPitch,Pitch,DesYaw,Yaw",
        "FMT, 131, 35, BAT, Qff, TimeUS,Volt,Curr",
        "FMT, 132, 35, VIBE, QfffIII, TimeUS,VibeX,VibeY,VibeZ,Clip0,Clip1,Clip2",
        "FMT, 133, 45, GPS, QBBIHHHiiif, TimeUS,Status,GMS,GWk,NSats,HDop,Lat,Lng,Alt,Spd",
        "FMT, 134, 25, MODE, QMB, TimeUS,Mode,ModeNum",
        "MSG, 1000000, ArduCopter V4.5.1 (123456)",
        "MODE, 1000000, 3, 3",
        "ATT, 1000000, 1.5, 1.4, -2.0, -1.8, 90.0, 89.5",
        "BAT, 1000000, 16.4, 15.2",
        "VIBE, 1000000, 5.1, 4.9, 7.2, 0, 0, 0",
        "GPS, 1000000, 3, 1000, 2300, 14, 0.9, 129716000, 775946000, 920.0, 5.2",
        "ATT, 1100000, 2.5, 2.3, -1.5, -1.4, 91.0, 90.8",
        "BAT, 1100000, 16.2, 19.5",
        "VIBE, 1100000, 5.4, 5.0, 7.5, 0, 0, 0",
        "GPS, 1100000, 3, 1100, 2300, 14, 0.9, 129716100, 775946100, 921.0, 5.4",
    ])
    parsed = ArduPilotParser().parse(log_text.encode("utf-8"), filename="sample_text.log")
    assert parsed.metadata.autopilot == "ArduPilot"
    assert "ArduCopter" in parsed.metadata.firmware_version
    assert parsed.sample_count >= 2
    assert pytest.approx(parsed.dataframe["gps_lat"].iloc[0], abs=1e-4) == 12.9716
    assert parsed.dataframe["flight_mode"].iloc[0] == "AUTO"


def test_px4_ulog_json_sample_parsing(sample_data_dir: Path) -> None:
    path = sample_data_dir / "synthetic_gps_glitch_attitude_instability.ulog.json"
    parsed = default_registry.parse_bytes(path.read_bytes(), filename=path.name)

    assert parsed.metadata.format_id == "px4_ulog"
    assert parsed.metadata.is_synthetic is True
    assert parsed.sample_count == 2000
    assert pytest.approx(parsed.sample_rate_hz, abs=0.5) == 25.0
    assert parsed.dataframe["roll_deg"].notna().all()
    assert parsed.dataframe["gps_hdop"].max() > 4.0


def test_px4_binary_ulg_parsing() -> None:
    raw = build_binary_ulg_bytes(n_samples=60)
    parsed = default_registry.parse_bytes(raw, filename="flight_01.ulg")

    assert parsed.metadata.format_id == "px4_ulog"
    assert parsed.metadata.vehicle_type == "QuadTest"
    assert parsed.sample_count == 60
    assert parsed.dataframe["voltage_v"].iloc[0] == pytest.approx(16.2, abs=0.05)
    assert any("Armed" in ev.description for ev in parsed.raw_events)


def test_mavlink_tlog_json_sample_parsing(sample_data_dir: Path) -> None:
    path = sample_data_dir / "synthetic_mavlink_telemetry_sample.tlog.json"
    parsed = default_registry.parse_bytes(path.read_bytes(), filename=path.name)

    assert parsed.metadata.format_id == "mavlink_tlog"
    assert parsed.metadata.is_synthetic is True
    assert parsed.sample_count >= 700
    assert parsed.dataframe["voltage_v"].notna().all()
    assert any("STATUSTEXT" in ev.title for ev in parsed.raw_events)


def test_mavlink_binary_tlog_parsing() -> None:
    raw = build_binary_mavlink_tlog_bytes(n_samples=50)
    parsed = default_registry.parse_bytes(raw, filename="mission.tlog")

    assert parsed.metadata.format_id == "mavlink_tlog"
    assert parsed.sample_count == 50
    assert parsed.dataframe["voltage_v"].iloc[0] == pytest.approx(15.8, abs=0.05)
    assert parsed.dataframe["gps_nsats"].iloc[0] == 14


def test_registry_errors_and_invalid_inputs() -> None:
    assert len(default_registry.list_formats()) == 3

    with pytest.raises(ParserError):
        default_registry.get_parser("unknown_format")

    with pytest.raises(ParserError):
        default_registry.parse_bytes(b"", filename="empty.csv")

    with pytest.raises(ParserError):
        PX4ULogParser().parse(b"NOT_ULOG_MAGIC_BYTES", filename="bad.ulg")

    with pytest.raises(ParserError):
        MAVLinkParser().parse(b"{bad json", filename="bad.tlog.json")

    with pytest.raises(ParserError):
        ArduPilotParser().parse(b"colA\n1\n2\n", filename="single_col.csv")


def test_px4_ulog_csv_parsing() -> None:
    csv_text = "\n".join([
        "timestamp,q[0],q[1],q[2],q[3],roll_body,pitch_body,voltage_filtered_v,current_filtered_a,eph,satellites_used",
        "1000000,1.0,0.0,0.0,0.0,0.05,0.02,16.1,12.0,0.9,14",
        "1100000,0.999,0.02,0.0,0.0,0.06,0.02,16.0,14.0,0.9,14",
        "1200000,0.998,0.03,0.0,0.0,0.07,0.03,15.9,16.0,1.0,13",
    ])
    parsed = default_registry.parse_bytes(csv_text.encode("utf-8"), filename="vehicle_attitude.ulog.csv")
    assert parsed.metadata.format_id == "px4_ulog"
    assert parsed.sample_count == 3
    assert parsed.dataframe["voltage_v"].iloc[0] == pytest.approx(16.1, abs=0.01)

