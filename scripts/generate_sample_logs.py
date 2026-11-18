"""
Deterministic physics-based synthetic UAV flight log generator for AeroLens.

Every generated file is explicitly labeled as SYNTHETIC SIMULATION DATA in
metadata headers and filenames. Fixed RNG seeds guarantee reproducibility.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np


ROOT_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = ROOT_DIR / "sample_data"


def euler_deg_to_quat(roll_deg: float, pitch_deg: float, yaw_deg: float) -> list[float]:
    r = math.radians(roll_deg) * 0.5
    p = math.radians(pitch_deg) * 0.5
    y = math.radians(yaw_deg) * 0.5
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    w = cr * cp * cy + sr * sp * sy
    x = sr * cp * cy - cr * sp * sy
    y_q = cr * sp * cy + sr * cp * sy
    z = cr * cp * sy - sr * sp * cy
    return [round(w, 6), round(x, 6), round(y_q, 6), round(z, 6)]


def generate_nominal_survey_csv(out_path: Path) -> None:
    """120s healthy 4S quadcopter survey flight at 20 Hz (ArduPilot DataFlash CSV)."""
    rng = np.random.default_rng(101)
    fs = 20.0
    duration_s = 120.0
    t = np.arange(0.0, duration_s, 1.0 / fs)
    n = len(t)

    # Smooth mission altitude profile (takeoff -> 45m survey -> descent & land)
    alt = np.zeros(n)
    throttle = np.full(n, 14.0)
    modes = []
    for i, ts in enumerate(t):
        if ts < 6.0:
            alt[i] = 0.2
            throttle[i] = 15.0 + 3.0 * (ts / 6.0)
            modes.append("STABILIZE")
        elif ts < 16.0:
            frac = (ts - 6.0) / 10.0
            alt[i] = 45.0 * (0.5 - 0.5 * math.cos(math.pi * frac))
            throttle[i] = 64.0
            modes.append("GUIDED")
        elif ts < 102.0:
            alt[i] = 45.0 + 0.35 * math.sin(0.25 * ts)
            throttle[i] = 52.0 + 3.5 * math.sin(0.2 * ts)
            modes.append("AUTO")
        elif ts < 114.0:
            frac = (ts - 102.0) / 12.0
            alt[i] = 45.0 * (1.0 - (0.5 - 0.5 * math.cos(math.pi * frac)))
            throttle[i] = 36.0
            modes.append("LAND")
        else:
            alt[i] = 0.0
            throttle[i] = 10.0
            modes.append("LAND")

    # Lawnmower survey ground track around Bangalore test range (lat ~12.9716, lon ~77.5946)
    lat0, lon0 = 12.9716, 77.5946
    north_m = np.zeros(n)
    east_m = np.zeros(n)
    yaw_des = np.zeros(n)
    for i, ts in enumerate(t):
        if 16.0 <= ts < 102.0:
            u = ts - 16.0
            leg = int(u // 21.5)
            phase = (u % 21.5) / 21.5
            if leg % 2 == 0:
                north_m[i] = 140.0 * phase
                yaw_des[i] = 0.0
            else:
                north_m[i] = 140.0 * (1.0 - phase)
                yaw_des[i] = 180.0
            east_m[i] = leg * 28.0 + min(28.0, 28.0 * max(0.0, (phase - 0.85) / 0.15)) if leg < 3 else leg * 28.0
        elif ts < 102.0:
            u = ts - 16.0
            leg = int(u // 21.5)
            phase = (u % 21.5) / 21.5
            if leg % 2 == 0:
                north_m[i] = 140.0 * phase
                yaw_des[i] = 0.0
            else:
                north_m[i] = 140.0 * (1.0 - phase)
                yaw_des[i] = 180.0
            east_m[i] = leg * 28.0 + min(28.0, 28.0 * max(0.0, (phase - 0.85) / 0.15)) if leg < 3 else leg * 28.0
        elif ts < 114.0:
            # Smooth return-to-home glide to origin (0, 0)
            frac = (ts - 102.0) / 12.0
            if i > 0 and abs(ts - 102.0) < 1.0 / fs:
                pass
            start_n = north_m[int(round(102.0 * fs)) - 1]
            start_e = east_m[int(round(102.0 * fs)) - 1]
            north_m[i] = start_n * (1.0 - frac)
            east_m[i] = start_e * (1.0 - frac)
        else:
            north_m[i] = 0.0
            east_m[i] = 0.0

    lat = lat0 + north_m / 111_132.0
    lon = lon0 + east_m / (111_320.0 * math.cos(math.radians(lat0)))
    speed = np.gradient(np.hypot(north_m, east_m), 1.0 / fs)
    speed = np.clip(np.abs(speed), 0.0, 14.0)

    # Healthy 4S LiPo electro-chemistry: R_int = 0.042 Ohm (42 mOhm)
    current = 4.0 + 0.42 * throttle + 3.2 * np.sin(0.18 * t) + rng.normal(0.0, 0.4, n)
    current = np.clip(current, 2.0, 42.0)
    dt = 1.0 / fs
    q_ah = np.cumsum(current * dt) / 3600.0
    v_oc = 16.72 - 1.15 * q_ah
    r_int_ohm = 0.042
    voltage = v_oc - current * r_int_ohm + rng.normal(0.0, 0.015, n)

    # Tight PID attitude tracking (< 0.8 deg RMS)
    roll_des = 7.5 * np.sin(0.28 * t) * (alt > 2.0)
    pitch_des = -6.0 * np.cos(0.19 * t) * (alt > 2.0)
    # 1st-order airframe lag ~45 ms (about 1 sample at 20Hz)
    roll = np.roll(roll_des, 1) * 0.96 + rng.normal(0.0, 0.28, n)
    pitch = np.roll(pitch_des, 1) * 0.96 + rng.normal(0.0, 0.28, n)
    yaw = yaw_des + rng.normal(0.0, 0.6, n)

    # Low nominal airframe vibration (~9 m/s^2 total RMS)
    vibe_x = 4.2 + 1.8 * np.sin(2.0 * math.pi * 6.5 * t) + rng.normal(0.0, 0.9, n)
    vibe_y = 4.5 + 1.6 * np.cos(2.0 * math.pi * 7.2 * t) + rng.normal(0.0, 0.9, n)
    vibe_z = 6.0 + 2.1 * np.sin(2.0 * math.pi * 8.1 * t) + rng.normal(0.0, 1.1, n)

    hdop = np.clip(0.85 + 0.08 * np.sin(0.11 * t) + rng.normal(0.0, 0.03, n), 0.7, 1.2)
    nsats = np.clip(np.round(15.2 + 0.7 * np.sin(0.07 * t) + rng.normal(0.0, 0.2, n)), 13, 17).astype(int)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        f.write("# AEROLENS_SYNTHETIC: true\n")
        f.write("# SYNTHETIC_LABEL: Deterministic Nominal 4S Survey Simulation (Seed 101)\n")
        f.write("# AUTOPILOT: ArduCopter 4.5.1\n")
        f.write("# VEHICLE_TYPE: Quadcopter-Survey\n")
        f.write("# CELL_COUNT: 4\n")
        writer = csv.writer(f)
        writer.writerow([
            "TimeUS", "BAT.Volt", "BAT.Curr", "ATT.Roll", "ATT.DesRoll",
            "ATT.Pitch", "ATT.DesPitch", "ATT.Yaw", "ATT.DesYaw",
            "VIBE.VibeX", "VIBE.VibeY", "VIBE.VibeZ", "VIBE.Clip0",
            "GPS.Lat", "GPS.Lng", "GPS.Alt", "GPS.HDop", "GPS.NSats", "GPS.Spd",
            "BARO.Alt", "CTUN.ThO", "MODE.Mode"
        ])
        for i in range(n):
            writer.writerow([
                int(round(t[i] * 1_000_000)),
                round(float(voltage[i]), 3),
                round(float(current[i]), 3),
                round(float(roll[i]), 3),
                round(float(roll_des[i]), 3),
                round(float(pitch[i]), 3),
                round(float(pitch_des[i]), 3),
                round(float(yaw[i]), 3),
                round(float(yaw_des[i]), 3),
                round(float(vibe_x[i]), 3),
                round(float(vibe_y[i]), 3),
                round(float(vibe_z[i]), 3),
                0,
                round(float(lat[i]), 7),
                round(float(lon[i]), 7),
                round(float(920.0 + alt[i]), 2),
                round(float(hdop[i]), 2),
                int(nsats[i]),
                round(float(speed[i]), 2),
                round(float(alt[i]), 2),
                round(float(throttle[i] / 100.0), 3),
                modes[i],
            ])


def generate_voltage_sag_vibration_csv(out_path: Path) -> None:
    """
    45s high-rate (200 Hz) 4S flight exhibiting degraded battery R_int (~165 mOhm),
    under-load voltage sag (< 13.0V), unbalanced rotor 78 Hz FFT vibration peak,
    and accelerometer clipping.
    """
    rng = np.random.default_rng(202)
    fs = 200.0
    duration_s = 45.0
    t = np.arange(0.0, duration_s, 1.0 / fs)
    n = len(t)

    alt = np.where(t < 5.0, 2.0 * t, np.where(t < 38.0, 10.0 + 18.0 * np.sin((t - 5.0) * 0.1), 10.0))
    throttle = np.full(n, 48.0)
    # High-current aggressive climb windows: 12-20s and 27-34s
    burst1 = (t >= 12.0) & (t <= 20.0)
    burst2 = (t >= 27.0) & (t <= 34.0)
    throttle[burst1] = 84.0
    throttle[burst2] = 89.0

    current = 12.0 + 0.35 * (throttle - 45.0) + rng.normal(0.0, 0.4, n)
    current[burst1] = 27.5 + rng.normal(0.0, 0.5, np.sum(burst1))
    current[burst2] = 31.5 + rng.normal(0.0, 0.5, np.sum(burst2))
    current = np.clip(current, 6.0, 42.0)

    # Aged 4S pack: high R_int = 0.165 Ohm (41.25 mOhm/cell!)
    q_ah = np.cumsum(current * (1.0 / fs)) / 3600.0
    v_oc = 16.70 - 1.30 * q_ah
    r_int_true = 0.165
    voltage = v_oc - current * r_int_true + rng.normal(0.0, 0.02, n)
    # Force sag below 13.0 V during second climb burst

    # Unbalanced prop inducing strong 78 Hz harmonic on X/Y/Z (well below 100 Hz Nyquist)
    vibe_x = 18.0 + 26.0 * np.sin(2.0 * math.pi * 78.0 * t) + rng.normal(0.0, 2.2, n)
    vibe_y = 16.0 + 21.0 * np.cos(2.0 * math.pi * 78.0 * t) + rng.normal(0.0, 2.0, n)
    vibe_z = 22.0 + 18.0 * np.sin(2.0 * math.pi * 78.0 * t + 0.5) + rng.normal(0.0, 2.5, n)
    # Intensify vibration + trigger accelerometer clipping during high throttle bursts
    vibe_x[burst1 | burst2] *= 1.45
    vibe_y[burst1 | burst2] *= 1.45
    vibe_z[burst1 | burst2] *= 1.45

    clip = np.zeros(n, dtype=int)
    clip_events = ((t >= 14.0) & (t <= 19.0)) | ((t >= 28.0) & (t <= 33.0))
    for i in range(1, n):
        inc = 1 if (clip_events[i] and (i % 150 == 0)) else 0
        clip[i] = clip[i - 1] + inc

    roll_des = 5.0 * np.sin(0.5 * t)
    pitch_des = -4.0 * np.cos(0.4 * t)
    roll = roll_des + rng.normal(0.0, 0.7, n)
    pitch = pitch_des + rng.normal(0.0, 0.7, n)
    yaw = 90.0 * np.ones(n) + rng.normal(0.0, 0.4, n)

    lat = 12.9716 + 0.0004 * np.sin(0.1 * t)
    lon = 77.5946 + 0.0004 * np.cos(0.1 * t)
    hdop = np.full(n, 1.05) + rng.normal(0.0, 0.04, n)
    nsats = np.full(n, 13)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        f.write("# AEROLENS_SYNTHETIC: true\n")
        f.write("# SYNTHETIC_LABEL: Degraded Battery R_int + 78 Hz Rotor Imbalance Simulation (Seed 202)\n")
        f.write("# AUTOPILOT: ArduCopter 4.5.1\n")
        f.write("# VEHICLE_TYPE: Hexacopter-Payload\n")
        f.write("# CELL_COUNT: 4\n")
        writer = csv.writer(f)
        writer.writerow([
            "TimeUS", "BAT.Volt", "BAT.Curr", "ATT.Roll", "ATT.DesRoll",
            "ATT.Pitch", "ATT.DesPitch", "ATT.Yaw", "ATT.DesYaw",
            "VIBE.VibeX", "VIBE.VibeY", "VIBE.VibeZ", "VIBE.Clip0",
            "GPS.Lat", "GPS.Lng", "GPS.Alt", "GPS.HDop", "GPS.NSats", "GPS.Spd",
            "BARO.Alt", "CTUN.ThO", "MODE.Mode"
        ])
        for i in range(n):
            mode = "ALT_HOLD" if t[i] < 10.0 else ("LOITER" if t[i] < 36.0 else "RTL")
            writer.writerow([
                int(round(t[i] * 1_000_000)),
                round(float(voltage[i]), 3),
                round(float(current[i]), 3),
                round(float(roll[i]), 3),
                round(float(roll_des[i]), 3),
                round(float(pitch[i]), 3),
                round(float(pitch_des[i]), 3),
                round(float(yaw[i]), 3),
                round(float(yaw[i]), 3),
                round(float(vibe_x[i]), 3),
                round(float(vibe_y[i]), 3),
                round(float(vibe_z[i]), 3),
                int(clip[i]),
                round(float(lat[i]), 7),
                round(float(lon[i]), 7),
                round(float(920.0 + alt[i]), 2),
                round(float(hdop[i]), 2),
                int(nsats[i]),
                round(float(6.2 if 5.0 < t[i] < 38.0 else 0.5), 2),
                round(float(alt[i]), 2),
                round(float(throttle[i] / 100.0), 3),
                mode,
            ])


def generate_gps_attitude_ulog_json(out_path: Path) -> None:
    """
    80s PX4 ULog JSON flight at 25 Hz showing GNSS multipath/spoof glitch
    (HDOP spike to 4.8, sat drop 14->5, 42m Haversine jump) and 4.2 Hz PID oscillation.
    """
    rng = np.random.default_rng(303)
    fs = 25.0
    duration_s = 80.0
    t = np.arange(0.0, duration_s, 1.0 / fs)
    n = len(t)

    roll_des = 6.0 * np.sin(0.35 * t)
    pitch_des = -4.5 * np.cos(0.30 * t)
    yaw_des = 15.0 * np.sin(0.1 * t)

    # Inject 4.2 Hz PID roll/pitch oscillation between t=42s and t=60s + 140 ms baseline lag
    lag_steps = 4  # 160 ms at 25 Hz
    roll = np.roll(roll_des, lag_steps) + rng.normal(0.0, 0.35, n)
    pitch = np.roll(pitch_des, lag_steps) + rng.normal(0.0, 0.35, n)
    osc_mask = (t >= 42.0) & (t <= 60.0)
    roll[osc_mask] += 7.8 * np.sin(2.0 * math.pi * 4.2 * (t[osc_mask] - 42.0))
    pitch[osc_mask] += 5.9 * np.cos(2.0 * math.pi * 4.2 * (t[osc_mask] - 42.0))
    yaw = yaw_des + rng.normal(0.0, 0.6, n)

    # Healthy 6S battery
    current = 24.0 + 4.0 * np.sin(0.2 * t) + rng.normal(0.0, 0.4, n)
    q_ah = np.cumsum(current * (1.0 / fs)) / 3600.0
    voltage = 25.0 - 0.8 * q_ah - 0.055 * current + rng.normal(0.0, 0.02, n)

    # GPS track with multipath glitch window 28s..38s
    lat0, lon0 = 37.4275, -122.1697
    north_m = 45.0 * np.sin(0.08 * t)
    east_m = 45.0 * np.cos(0.08 * t) - 45.0
    hdop = np.full(n, 0.95) + rng.normal(0.0, 0.03, n)
    sats = np.full(n, 14, dtype=int)

    glitch_win = (t >= 28.0) & (t <= 37.5)
    hdop[glitch_win] = 3.8 + 1.1 * np.sin((t[glitch_win] - 28.0) * 0.6)
    sats[glitch_win] = 5
    # Sudden 42 m position jump at t=31.0s .. 34.0s
    jump_win = (t >= 31.0) & (t <= 34.0)
    north_m[jump_win] += 42.0
    east_m[jump_win] -= 18.0

    lat = lat0 + north_m / 111_132.0
    lon = lon0 + east_m / (111_320.0 * math.cos(math.radians(lat0)))
    alt = 32.0 * np.sin(math.pi * np.clip(t / duration_s, 0.0, 1.0))

    topics: dict[str, list[dict]] = {
        "vehicle_attitude": [],
        "vehicle_attitude_setpoint": [],
        "battery_status": [],
        "vibe_metrics": [],
        "vehicle_gps_position": [],
        "vehicle_local_position": [],
        "vehicle_status": [],
    }

    for i in range(n):
        ts_us = int(round(t[i] * 1_000_000))
        topics["vehicle_attitude"].append({"timestamp": ts_us, "q": euler_deg_to_quat(float(roll[i]), float(pitch[i]), float(yaw[i]))})
        topics["vehicle_attitude_setpoint"].append({
            "timestamp": ts_us,
            "roll_body": round(math.radians(float(roll_des[i])), 5),
            "pitch_body": round(math.radians(float(pitch_des[i])), 5),
            "yaw_body": round(math.radians(float(yaw_des[i])), 5),
            "thrust": 0.56,
        })
        if i % 5 == 0:
            topics["battery_status"].append({
                "timestamp": ts_us,
                "voltage_v": round(float(voltage[i]), 3),
                "current_a": round(float(current[i]), 2),
                "remaining": round(max(0.2, 0.95 - 0.55 * (i / n)), 3),
            })
            topics["vibe_metrics"].append({
                "timestamp": ts_us,
                "vibe_x": round(float(6.0 + rng.normal(0, 0.8)), 3),
                "vibe_y": round(float(5.5 + rng.normal(0, 0.8)), 3),
                "vibe_z": round(float(8.2 + rng.normal(0, 1.0)), 3),
                "clip_count": 0,
            })
            topics["vehicle_gps_position"].append({
                "timestamp": ts_us,
                "lat": int(round(lat[i] * 1e7)),
                "lon": int(round(lon[i] * 1e7)),
                "alt": int(round((120.0 + alt[i]) * 1000)),
                "eph": round(float(hdop[i]), 2),
                "satellites_used": int(sats[i]),
                "vel_m_s": round(float(4.5 if 5 < t[i] < 75 else 0.2), 2),
            })
            topics["vehicle_local_position"].append({
                "timestamp": ts_us,
                "z": round(float(-alt[i]), 3),
            })

    topics["vehicle_status"] = [
        {"timestamp": 0, "nav_state": 17},
        {"timestamp": 8_000_000, "nav_state": 3},
        {"timestamp": 38_000_000, "nav_state": 2},
        {"timestamp": 68_000_000, "nav_state": 5},
        {"timestamp": 76_000_000, "nav_state": 18},
    ]

    payload = {
        "format": "px4_ulog",
        "metadata": {
            "autopilot": "PX4 Autopilot",
            "vehicle_type": "Quadcopter-X500",
            "firmware_version": "PX4 v1.14.3",
            "cell_count_hint": 6,
            "is_synthetic": True,
            "synthetic_label": "Synthetic GPS Multipath + 4.2 Hz PID Oscillation (Seed 303)",
            "notes": "Synthetic PX4 ULog JSON demonstrating EKF GNSS glitch and excessive P-gain oscillation.",
        },
        "events": [
            {
                "timestamp_s": 28.0,
                "event_type": "autopilot_msg",
                "title": "PX4 EKF2 GNSS Variance Warning",
                "description": "High GPS innovation variance detected; switching toward optical/baro fallback.",
                "severity": "warning",
            }
        ],
        "topics": topics,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def generate_mavlink_tlog_json(out_path: Path) -> None:
    """
    75s MAVLink telemetry JSON stream at 10 Hz with moderate mission turbulence
    and end-of-flight RTH battery voltage warning.
    """
    rng = np.random.default_rng(404)
    fs = 10.0
    duration_s = 75.0
    t = np.arange(0.0, duration_s, 1.0 / fs)
    n = len(t)

    lat0, lon0 = 47.3977, 8.5456  # ETH Zurich synthetic test coordinates
    messages: list[dict] = []

    for i, ts in enumerate(t):
        ms = int(round(ts * 1000.0))
        us = int(round(ts * 1_000_000.0))
        alt = 28.0 * math.sin(math.pi * min(1.0, ts / duration_s))
        mode = "AUTO" if ts < 55.0 else "RTL"
        if i % 10 == 0:
            messages.append({"mavpackettype": "HEARTBEAT", "time_boot_ms": ms, "flight_mode": mode})

        roll_des = 0.08 * math.sin(0.4 * ts)
        pitch_des = -0.06 * math.cos(0.35 * ts)
        # Brief wind gust disturbance at t=40..44s
        gust = 0.09 * math.sin(2.0 * math.pi * 1.1 * ts) if 40.0 <= ts <= 44.0 else 0.0
        roll = roll_des + gust + float(rng.normal(0.0, 0.01))
        pitch = pitch_des + float(rng.normal(0.0, 0.01))
        yaw = 0.25 * math.sin(0.1 * ts)

        messages.append({
            "mavpackettype": "ATTITUDE",
            "time_boot_ms": ms,
            "roll": round(roll, 5),
            "pitch": round(pitch, 5),
            "yaw": round(yaw, 5),
        })
        messages.append({
            "mavpackettype": "ATTITUDE_TARGET",
            "time_boot_ms": ms,
            "q": euler_deg_to_quat(math.degrees(roll_des), math.degrees(pitch_des), math.degrees(yaw)),
            "thrust": 0.58 if ts < 55.0 else 0.72,
        })

        # 4S battery with minor R_int (~68 mOhm) and RTH sag warning at t=58..66s
        curr_a = 20.0 + (16.0 if 58.0 <= ts <= 66.0 else 0.0) + float(rng.normal(0, 0.5))
        v_oc = 16.68 - 1.05 * (ts / duration_s)
        v_pack = v_oc - 0.068 * curr_a
        messages.append({
            "mavpackettype": "SYS_STATUS",
            "time_boot_ms": ms,
            "voltage_battery": int(round(v_pack * 1000.0)),
            "current_battery": int(round(curr_a * 100.0)),
            "battery_remaining": int(round(max(15, 92 - 68 * (ts / duration_s)))),
        })

        messages.append({
            "mavpackettype": "VIBRATION",
            "time_usec": us,
            "vibration_x": round(float(7.5 + rng.normal(0, 1.1)), 3),
            "vibration_y": round(float(7.2 + rng.normal(0, 1.1)), 3),
            "vibration_z": round(float(10.5 + rng.normal(0, 1.4)), 3),
            "clipping_0": 0,
            "clipping_1": 0,
            "clipping_2": 0,
        })

        if i % 2 == 0:
            lat = lat0 + (50.0 * math.sin(0.09 * ts)) / 111_132.0
            lon = lon0 + (50.0 * math.cos(0.09 * ts) - 50.0) / 75_300.0
            messages.append({
                "mavpackettype": "GPS_RAW_INT",
                "time_usec": us,
                "lat": int(round(lat * 1e7)),
                "lon": int(round(lon * 1e7)),
                "alt": int(round((450.0 + alt) * 1000)),
                "eph": 112,
                "satellites_visible": 13,
                "vel": 540,
            })
            messages.append({
                "mavpackettype": "VFR_HUD",
                "time_boot_ms": ms,
                "groundspeed": 5.4,
                "alt": round(alt, 2),
                "throttle": 58 if ts < 55.0 else 72,
            })

    messages.append({
        "mavpackettype": "STATUSTEXT",
        "time_boot_ms": 58000,
        "severity": 4,
        "text": "Low battery warning during RTH climb — synthetic simulation event",
    })

    payload = {
        "format": "mavlink_tlog",
        "metadata": {
            "autopilot": "ArduPilot / MAVLink2",
            "vehicle_type": "Mapping-Quad",
            "firmware_version": "MAVLink 2.0",
            "cell_count_hint": 4,
            "is_synthetic": True,
            "synthetic_label": "Synthetic MAVLink Mission + RTH Sag Simulation (Seed 404)",
            "notes": "Synthetic MAVLink JSON stream with mild wind gust and end-of-flight battery warning.",
        },
        "messages": messages,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    SAMPLE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    generate_nominal_survey_csv(SAMPLE_DATA_DIR / "synthetic_nominal_survey_flight.csv")
    generate_voltage_sag_vibration_csv(SAMPLE_DATA_DIR / "synthetic_voltage_sag_vibration_anomaly.csv")
    generate_gps_attitude_ulog_json(SAMPLE_DATA_DIR / "synthetic_gps_glitch_attitude_instability.ulog.json")
    generate_mavlink_tlog_json(SAMPLE_DATA_DIR / "synthetic_mavlink_telemetry_sample.tlog.json")
    print(f"Generated 4 deterministic synthetic flight logs in {SAMPLE_DATA_DIR}")


if __name__ == "__main__":
    main()
