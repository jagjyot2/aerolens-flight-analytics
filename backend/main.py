"""FastAPI application entry point for AeroLens Flight Analytics Platform."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from backend import __version__
from backend.analysis.anomaly_engine import FlightAnalyzer
from backend.analysis.comparator import compare_flights
from backend.config import settings
from backend.database import SessionLocal, get_db, init_db
from backend.exporters.report_exporter import export_csv_report, export_html_report, export_pdf_report
from backend.models import FlightRecord, FlightRepository
from backend.parsers.base import ParserError
from backend.parsers.registry import default_registry
from backend.schemas import (
    AnomalyDTO,
    CompareRequest,
    FlightDetailDTO,
    FlightListItemDTO,
    FlightTelemetryDTO,
    ParserInfoDTO,
    TimelineEventDTO,
)


def _safe_slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
    return slug or "flight_report"


def _friendly_flight_name(filename: str) -> str:
    stem = Path(filename).name
    for ext in (".ulog.json", ".tlog.json", ".ulog.csv", ".csv", ".ulg", ".ulog", ".tlog", ".log", ".json"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem.replace("_", " ").title()


def _seed_sample_flights(db: Session) -> int:
    sample_dir = settings.sample_data_dir
    if not sample_dir.exists():
        return 0
    repo = FlightRepository(db)
    analyzer = FlightAnalyzer()
    seeded = 0
    for path in sorted(sample_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".csv", ".json", ".ulg", ".tlog", ".log"):
            continue
        if repo.get_flight_by_filename(path.name) is not None:
            continue
        try:
            parsed = default_registry.parse_bytes(path.read_bytes(), filename=path.name)
            analysis = analyzer.analyze(parsed)
            repo.save_analyzed_flight(_friendly_flight_name(path.name), parsed, analysis)
            seeded += 1
        except ParserError:
            continue
    return seeded


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if settings.auto_seed_samples:
        db = SessionLocal()
        try:
            _seed_sample_flights(db)
        finally:
            db.close()
    ui_dir = _resolve_ui_dir()
    if ui_dir and (ui_dir / "assets").is_dir():
        already_mounted = any(getattr(r, "path", None) == "/assets" for r in _app.routes)
        if not already_mounted:
            _app.mount("/assets", StaticFiles(directory=str(ui_dir / "assets")), name="frontend-assets")
    yield


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "Aerospace UAV flight log ingestion, physics-based anomaly detection, "
        "multi-flight comparison, and diagnostic reporting platform."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _flight_to_list_dto(rec: FlightRecord) -> FlightListItemDTO:
    summary = json.loads(rec.summary_json or "{}")
    crit = sum(1 for a in rec.anomalies if a.severity == "critical")
    warn = sum(1 for a in rec.anomalies if a.severity == "warning")
    return FlightListItemDTO(
        id=rec.id,
        name=rec.name,
        source_filename=rec.source_filename,
        format_id=rec.format_id,
        autopilot=rec.autopilot,
        vehicle_type=rec.vehicle_type,
        firmware_version=rec.firmware_version,
        is_synthetic=rec.is_synthetic,
        synthetic_label=rec.synthetic_label,
        health_score=rec.health_score,
        health_grade=rec.health_grade,
        flight_status=rec.flight_status,
        duration_s=rec.duration_s,
        sample_count=rec.sample_count,
        anomaly_count=rec.anomaly_count,
        critical_count=crit,
        warning_count=warn,
        created_at=rec.created_at,
        summary=summary,
    )


def _flight_to_detail_dto(rec: FlightRecord) -> FlightDetailDTO:
    base = _flight_to_list_dto(rec)
    anomalies = [
        AnomalyDTO(
            anomaly_id=a.anomaly_code_id,
            category=a.category,
            code=a.code,
            severity=a.severity,
            title=a.title,
            description=a.description,
            start_time_s=a.start_time_s,
            end_time_s=a.end_time_s,
            peak_time_s=a.peak_time_s,
            peak_value=a.peak_value,
            threshold_value=a.threshold_value,
            unit=a.unit,
            recommendation=a.recommendation,
            metrics=json.loads(a.metrics_json or "{}"),
        )
        for a in rec.anomalies
    ]
    events = [
        TimelineEventDTO(
            timestamp_s=e.timestamp_s,
            end_time_s=e.end_time_s,
            event_type=e.event_type,
            category=e.category,
            severity=e.severity,
            title=e.title,
            description=e.description,
        )
        for e in rec.events
    ]
    return FlightDetailDTO(
        **base.model_dump(),
        metadata=json.loads(rec.metadata_json or "{}"),
        fft_spectrum=json.loads(rec.fft_spectrum_json or "{}"),
        anomalies=anomalies,
        events=events,
    )


@app.get("/api/health", tags=["System"])
def health_check(db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    repo = FlightRepository(db)
    flights = repo.list_flights()
    ui_source = "frontend_dist" if (settings.frontend_dist_dir / "index.html").exists() else "backend_static"
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": __version__,
        "flight_count": len(flights),
        "parsers": [p.format_id for p in default_registry.list_formats()],
        "ui_source": ui_source,
    }


@app.get("/api/parsers", response_model=list[ParserInfoDTO], tags=["System"])
def list_parsers() -> list[ParserInfoDTO]:
    return [
        ParserInfoDTO(
            format_id=p.format_id,
            display_name=p.display_name,
            supported_extensions=list(p.supported_extensions),
        )
        for p in default_registry.list_formats()
    ]


@app.get("/api/flights", response_model=list[FlightListItemDTO], tags=["Flights"])
def list_flights(db: Annotated[Session, Depends(get_db)]) -> list[FlightListItemDTO]:
    repo = FlightRepository(db)
    return [_flight_to_list_dto(r) for r in repo.list_flights()]


@app.post("/api/flights/seed-samples", tags=["Flights"])
def seed_samples(db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    seeded = _seed_sample_flights(db)
    return {"seeded": seeded}


@app.post(
    "/api/flights/upload",
    response_model=FlightDetailDTO,
    status_code=status.HTTP_201_CREATED,
    tags=["Flights"],
)
async def upload_flight_log(
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File(...)],
    name: Annotated[str | None, Form()] = None,
    format_hint: Annotated[str | None, Form()] = None,
) -> FlightDetailDTO:
    filename = file.filename or "uploaded_flight.csv"
    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Flight log exceeds {settings.max_upload_mb} MB upload limit.",
        )

    try:
        parsed = default_registry.parse_bytes(
            content, filename=filename, format_hint=format_hint or None
        )
    except ParserError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    analysis = FlightAnalyzer().analyze(parsed)
    flight_name = name.strip() if (name and name.strip()) else _friendly_flight_name(filename)

    repo = FlightRepository(db)
    saved = repo.save_analyzed_flight(flight_name, parsed, analysis)
    return _flight_to_detail_dto(saved)


@app.post("/api/flights/compare", tags=["Analysis"])
def compare_flight_logs(
    req: CompareRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    repo = FlightRepository(db)
    inputs: list[dict[str, Any]] = []
    for fid in req.flight_ids:
        rec = repo.get_flight(fid)
        if not rec:
            raise HTTPException(status_code=404, detail=f"Flight {fid} not found.")
        inputs.append(
            {
                "id": rec.id,
                "name": rec.name,
                "autopilot": rec.autopilot,
                "is_synthetic": rec.is_synthetic,
                "summary": json.loads(rec.summary_json or "{}"),
                "anomalies": [
                    {"severity": a.severity, "category": a.category, "code": a.code}
                    for a in rec.anomalies
                ],
                "telemetry": repo.get_telemetry_records(rec, max_points=1200),
            }
        )
    try:
        result = compare_flights(inputs, n_points=req.n_points)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.to_dict()


@app.get("/api/flights/{flight_id}", response_model=FlightDetailDTO, tags=["Flights"])
def get_flight_detail(
    flight_id: int, db: Annotated[Session, Depends(get_db)]
) -> FlightDetailDTO:
    repo = FlightRepository(db)
    rec = repo.get_flight(flight_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"Flight {flight_id} not found.")
    return _flight_to_detail_dto(rec)


@app.get("/api/flights/{flight_id}/telemetry", response_model=FlightTelemetryDTO, tags=["Flights"])
def get_flight_telemetry(
    flight_id: int,
    db: Annotated[Session, Depends(get_db)],
    max_points: Annotated[int, Query(ge=0, le=50000)] = 2500,
) -> FlightTelemetryDTO:
    repo = FlightRepository(db)
    rec = repo.get_flight(flight_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"Flight {flight_id} not found.")
    records = repo.get_telemetry_records(rec, max_points=max_points if max_points > 0 else None)
    return FlightTelemetryDTO(
        flight_id=rec.id,
        name=rec.name,
        sample_count=rec.sample_count,
        returned_count=len(records),
        records=records,
        fft_spectrum=json.loads(rec.fft_spectrum_json or "{}"),
    )


@app.delete("/api/flights/{flight_id}", tags=["Flights"])
def delete_flight(flight_id: int, db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    repo = FlightRepository(db)
    if not repo.delete_flight(flight_id):
        raise HTTPException(status_code=404, detail=f"Flight {flight_id} not found.")
    return {"deleted": True, "flight_id": flight_id}


def _load_export_context(
    flight_id: int, db: Session
) -> tuple[FlightRecord, dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    repo = FlightRepository(db)
    rec = repo.get_flight(flight_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"Flight {flight_id} not found.")
    detail = _flight_to_detail_dto(rec)
    meta = {
        "name": rec.name,
        "autopilot": rec.autopilot,
        "vehicle_type": rec.vehicle_type,
        "format_id": rec.format_id,
        "is_synthetic": rec.is_synthetic,
        "synthetic_label": rec.synthetic_label,
    }
    anomalies = [a.model_dump() for a in detail.anomalies]
    events = [e.model_dump() for e in detail.events]
    telemetry = repo.get_telemetry_records(rec, max_points=None)
    return rec, meta, detail.summary, anomalies, events, telemetry


@app.get("/api/flights/{flight_id}/export/pdf", tags=["Exports"])
def export_flight_pdf(flight_id: int, db: Annotated[Session, Depends(get_db)]) -> Response:
    rec, meta, summary, anomalies, events, telemetry = _load_export_context(flight_id, db)
    pdf_bytes = export_pdf_report(meta, summary, anomalies, events, telemetry)
    fname = f"aerolens_{_safe_slug(rec.name)}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/flights/{flight_id}/export/html", tags=["Exports"])
def export_flight_html(flight_id: int, db: Annotated[Session, Depends(get_db)]) -> Response:
    rec, meta, summary, anomalies, events, telemetry = _load_export_context(flight_id, db)
    fft_spec = json.loads(rec.fft_spectrum_json or "{}")
    html_str = export_html_report(meta, summary, anomalies, events, fft_spec, telemetry)
    fname = f"aerolens_{_safe_slug(rec.name)}.html"
    return Response(
        content=html_str,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/flights/{flight_id}/export/csv", tags=["Exports"])
def export_flight_csv(flight_id: int, db: Annotated[Session, Depends(get_db)]) -> Response:
    rec, meta, summary, anomalies, _events, telemetry = _load_export_context(flight_id, db)
    csv_str = export_csv_report(meta, summary, anomalies, telemetry)
    fname = f"aerolens_{_safe_slug(rec.name)}.csv"
    return Response(
        content=csv_str,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---- Static UI Serving (prefers built React+Vite frontend/dist/, falls back to backend/static/) ----
def _resolve_ui_dir() -> Path | None:
    if (settings.frontend_dist_dir / "index.html").exists():
        return settings.frontend_dist_dir
    if (settings.static_fallback_dir / "index.html").exists():
        return settings.static_fallback_dir
    return None


@app.get("/", include_in_schema=False)
def serve_root_ui() -> Response:
    ui_dir = _resolve_ui_dir()
    if ui_dir and (ui_dir / "index.html").exists():
        return FileResponse(str(ui_dir / "index.html"), media_type="text/html")
    return HTMLResponse(
        "<h1>AeroLens API Online</h1><p>UI build not found. Visit <a href='/docs'>/docs</a>.</p>"
    )
