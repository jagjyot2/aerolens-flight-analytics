# AeroLens Engineering Roadmap

## Completed (v1.0.0)

- **Milestone 1 — Modular Flight Log Parsers**:
  - Canonical telemetry schema & abstract `BaseFlightParser` + `ParserRegistry` auto-detection.
  - `ArduPilotParser` supporting DataFlash `.csv` and raw text `.log` (`FMT`/`ATT`/`BAT`/`VIBE`/`GPS`/`MODE`/`MSG`).
  - `PX4ULogParser` supporting binary `.ulg` decoding (`F`/`I`/`A`/`D`/`L` blocks), structured `.ulog.json` with quaternion-to-Euler conversion, and `.ulog.csv`.
  - `MAVLinkParser` supporting `.tlog.json` and binary `.tlog` wire-format packets (v1 `0xFE` & v2 `0xFD` with trailing-zero unpadding).
- **Milestone 2 — Physics & Signal Processing Engine**:
  - Electro-chemical $R_{\text{int}}$ regression (`V = V_0 - k_soc*Q - R_int*I`) and voltage sag detection.
  - Hann-windowed SciPy `rfft` vibration spectral analyzer & rolling RMS clipping detector.
  - Attitude PID cross-correlation phase lag & local FFT oscillation detector.
  - Haversine GNSS jump, HDOP spike, and satellite drop detector.
  - Composite 0–100 Flight Health Score, unified timeline events, and multi-flight comparison engine.
- **Milestone 3 — Persistence, REST API & Exporters**:
  - SQLAlchemy 2.0 + SQLite repository layer with automatic synthetic sample seeding.
  - FastAPI REST endpoints (`/api/flights/*`, `/api/parsers`, `/api/health`).
  - ReportLab binary PDF report with vector telemetry plots, self-contained interactive HTML report, and RFC-4180 CSV exporter.
- **Milestone 4 — Interactive Frontend**:
  - React 18 + TypeScript + Vite SPA with Plotly.js synchronized charts, playback scrubber, anomaly navigator, drag-and-drop uploader, and multi-flight comparison view.
- **Milestone 5 — Verification & Documentation**:
  - Complete pytest unit & integration test suite (`tests/`), `ARCHITECTURE.md`, `README.md`, and deterministic sample generator (`scripts/generate_sample_logs.py`).

---

## Future Roadmap (v1.1+)

| Priority | Capability | Description |
|---|---|---|
| High | **ArduPilot Binary `.bin` Decoder** | Pure-Python DataFlash binary `.bin` (`0xA3 0x95`) header & packet decoder to complement `.log` and `.csv`. |
| High | **Motor / ESC Imbalance Localization** | Per-motor PWM (`RCOU.C1..C8` / `actuator_outputs`) correlation against roll/pitch moments and current draw to isolate failing ESCs or bent props. |
| Medium | **Dynamic Notch Filter Recommendation** | Auto-compute ArduPilot `INS_HNTCH_FREQ` & `INS_HNTCH_BW` parameters from multi-peak Welch PSD spectra. |
| Medium | **3D Cesium / Three.js Flight Replay** | Attitude-driven 3D UAV orientation + terrain altitude replay synchronized with the playback cursor. |
| Low | **PostgreSQL & Async Worker Mode** | Optional Celery/Redis ingestion worker + TimescaleDB backend for fleet-scale (10,000+ flights) telemetry archiving. |
