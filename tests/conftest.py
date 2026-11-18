"""Shared pytest fixtures and synthetic binary flight log builders."""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from backend.parsers.px4_ulog import ULOG_MAGIC


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = PROJECT_ROOT / "sample_data"


def build_binary_ulg_bytes(n_samples: int = 120) -> bytes:
    """Build a valid binary PX4 .ulg file in memory with F, I, A, D, L messages."""
    buf = bytearray()
    buf.extend(ULOG_MAGIC)
    buf.append(1)  # version
    buf.extend(struct.pack("<Q", 1_000_000))  # header timestamp

    def add_msg(mtype: str, payload: bytes) -> None:
        buf.extend(struct.pack("<HB", len(payload), ord(mtype)))
        buf.extend(payload)

    # Info message: sys_name
    key = b"char[8] sys_name"
    add_msg("I", bytes([len(key)]) + key + b"QuadTest")

    # Formats
    add_msg(
        "F",
        b"vehicle_attitude:uint64_t timestamp;float[4] q;",
    )
    add_msg(
        "F",
        b"battery_status:uint64_t timestamp;float voltage_v;float current_a;",
    )
    add_msg(
        "F",
        b"vehicle_gps_position:uint64_t timestamp;int32_t lat;int32_t lon;int32_t alt;float hdop;uint8_t satellites_used;",
    )

    # Add logged message subscriptions
    add_msg("A", struct.pack("<BH", 0, 1) + b"vehicle_attitude")
    add_msg("A", struct.pack("<BH", 0, 2) + b"battery_status")
    add_msg("A", struct.pack("<BH", 0, 3) + b"vehicle_gps_position")

    # Logged string event
    add_msg("L", struct.pack("<BQ", 6, 1_500_000) + b"Armed for synthetic ULog binary test")

    for i in range(n_samples):
        ts_us = 1_000_000 + i * 50_000  # 20 Hz
        roll_rad = 0.08 * math.sin(0.2 * i)
        w = math.cos(roll_rad * 0.5)
        x = math.sin(roll_rad * 0.5)
        add_msg("D", struct.pack("<HQffff", 1, ts_us, w, x, 0.0, 0.0))
        add_msg("D", struct.pack("<HQff", 2, ts_us, 16.2 - 0.005 * i, 14.0 + (i % 5)))
        add_msg(
            "D",
            struct.pack(
                "<HQiiifB",
                3,
                ts_us,
                int(12.9716 * 1e7) + i * 10,
                int(77.5946 * 1e7) + i * 10,
                920_000 + i * 100,
                0.92,
                14,
            ),
        )

    return bytes(buf)


def build_binary_mavlink_tlog_bytes(n_samples: int = 80) -> bytes:
    """Build a valid binary QGC-style .tlog stream containing MAVLink v1 frames."""
    buf = bytearray()
    base_unix_us = 1_730_000_000_000_000
    seq = 0

    def add_mav_v1(ts_us: int, msgid: int, payload: bytes) -> None:
        nonlocal seq
        buf.extend(struct.pack(">Q", ts_us))
        buf.extend(struct.pack("<BBBBBB", 0xFE, len(payload), seq & 0xFF, 1, 1, msgid))
        buf.extend(payload)
        buf.extend(b"\xaa\xbb")  # dummy 2-byte CRC
        seq += 1

    for i in range(n_samples):
        ts_us = base_unix_us + i * 100_000
        ms = i * 100
        # #30 ATTITUDE
        add_mav_v1(ts_us, 30, struct.pack("<Iffffff", ms, 0.05 * math.sin(i * 0.2), -0.04, 0.1, 0.0, 0.0, 0.0))
        # #1 SYS_STATUS
        add_mav_v1(
            ts_us,
            1,
            struct.pack("<IIIHHhHHHHHHb", 0, 0, 0, 250, 15800 - i * 8, 1850, 0, 0, 0, 0, 0, 0, 88),
        )
        # #241 VIBRATION
        add_mav_v1(ts_us, 241, struct.pack("<QfffIII", i * 100_000, 6.2, 5.8, 8.9, 0, 0, 0))
        # #24 GPS_RAW_INT
        add_mav_v1(
            ts_us,
            24,
            struct.pack(
                "<QiiiHHHHBB",
                i * 100_000,
                int(47.3977 * 1e7) + i * 15,
                int(8.5456 * 1e7) + i * 15,
                450_000 + i * 100,
                95,
                140,
                450,
                9000,
                3,
                14,
            ),
        )

    return bytes(buf)


@pytest.fixture(scope="session")
def sample_data_dir() -> Path:
    return SAMPLE_DATA_DIR
