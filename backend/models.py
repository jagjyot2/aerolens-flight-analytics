"""SQLAlchemy ORM models and repository abstraction for flight telemetry persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship, selectinload

from backend.analysis.anomaly_engine import AnomalyDetectionResult
from backend.database import Base
from backend.parsers.base import ParsedFlight


class FlightRecord(Base):
    """Persisted flight log metadata, summary metrics, and parsed telemetry payload."""

    __tablename__ = "flights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    format_id: Mapped[str] = mapped_column(String(64), nullable=False)
    autopilot: Mapped[str] = mapped_column(String(100), nullable=False, default="Unknown")
    vehicle_type: Mapped[str] = mapped_column(String(100), nullable=False, default="Multirotor")
    firmware_version: Mapped[str] = mapped_column(String(120), nullable=False, default="Unknown")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    synthetic_label: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    health_score: Mapped[float] = mapped_column(Float, nullable=False)
    health_grade: Mapped[str] = mapped_column(String(4), nullable=False)
    flight_status: Mapped[str] = mapped_column(String(24), nullable=False)
    duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    anomaly_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    fft_spectrum_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    telemetry_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    anomalies: Mapped[list["FlightAnomalyRecord"]] = relationship(
        back_populates="flight",
        cascade="all, delete-orphan",
        order_by="FlightAnomalyRecord.start_time_s",
    )
    events: Mapped[list["FlightEventRecord"]] = relationship(
        back_populates="flight",
        cascade="all, delete-orphan",
        order_by="FlightEventRecord.timestamp_s",
    )


class FlightAnomalyRecord(Base):
    """Persisted anomaly interval linked to a flight."""

    __tablename__ = "flight_anomalies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flight_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("flights.id", ondelete="CASCADE"), nullable=False, index=True
    )
    anomaly_code_id: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    start_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    peak_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    peak_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    recommendation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    flight: Mapped[FlightRecord] = relationship(back_populates="anomalies")


class FlightEventRecord(Base):
    """Persisted chronological event marker linked to a flight."""

    __tablename__ = "flight_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flight_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("flights.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    flight: Mapped[FlightRecord] = relationship(back_populates="events")


def _clean_records_for_json(parsed: ParsedFlight, analysis: AnomalyDetectionResult) -> list[dict[str, Any]]:
    df = parsed.dataframe.copy()
    for col, vals in analysis.derived_series.items():
        df[col] = vals
    # Convert NaN/inf to None cleanly for JSON serialization
    records = df.where(df.notna(), None).to_dict(orient="records")
    cleaned: list[dict[str, Any]] = []
    for row in records:
        out: dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
                out[k] = None
            elif isinstance(v, float):
                out[k] = round(v, 6) if k in ("gps_lat", "gps_lon") else round(v, 4)
            else:
                out[k] = v
        cleaned.append(out)
    return cleaned


class FlightRepository:
    """Repository pattern encapsulating SQLite persistence and query logic."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save_analyzed_flight(
        self,
        name: str,
        parsed: ParsedFlight,
        analysis: AnomalyDetectionResult,
    ) -> FlightRecord:
        summary_dict = analysis.summary.to_dict()
        meta = parsed.metadata
        meta_dict = {
            "format_id": meta.format_id,
            "autopilot": meta.autopilot,
            "vehicle_type": meta.vehicle_type,
            "firmware_version": meta.firmware_version,
            "source_filename": meta.source_filename,
            "is_synthetic": meta.is_synthetic,
            "synthetic_label": meta.synthetic_label,
            "cell_count_hint": meta.cell_count_hint,
            "notes": meta.notes,
        }
        telemetry_records = _clean_records_for_json(parsed, analysis)

        record = FlightRecord(
            name=name,
            source_filename=meta.source_filename or name,
            format_id=meta.format_id,
            autopilot=meta.autopilot,
            vehicle_type=meta.vehicle_type,
            firmware_version=meta.firmware_version,
            is_synthetic=meta.is_synthetic,
            synthetic_label=meta.synthetic_label,
            health_score=analysis.summary.health_score,
            health_grade=analysis.summary.health_grade,
            flight_status=analysis.summary.flight_status,
            duration_s=analysis.summary.duration_s,
            sample_count=analysis.summary.sample_count,
            anomaly_count=len(analysis.anomalies),
            metadata_json=json.dumps(meta_dict),
            summary_json=json.dumps(summary_dict),
            fft_spectrum_json=json.dumps(analysis.fft_spectrum),
            telemetry_json=json.dumps(telemetry_records),
        )

        for anom in analysis.anomalies:
            record.anomalies.append(
                FlightAnomalyRecord(
                    anomaly_code_id=anom.anomaly_id,
                    category=anom.category,
                    code=anom.code,
                    severity=anom.severity,
                    title=anom.title,
                    description=anom.description,
                    start_time_s=anom.start_time_s,
                    end_time_s=anom.end_time_s,
                    peak_time_s=anom.peak_time_s,
                    peak_value=anom.peak_value,
                    threshold_value=anom.threshold_value,
                    unit=anom.unit,
                    recommendation=anom.recommendation,
                    metrics_json=json.dumps(anom.metrics),
                )
            )

        for ev in analysis.events:
            record.events.append(
                FlightEventRecord(
                    timestamp_s=ev.timestamp_s,
                    end_time_s=ev.end_time_s,
                    event_type=ev.event_type,
                    category=ev.category,
                    severity=ev.severity,
                    title=ev.title,
                    description=ev.description,
                )
            )

        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def list_flights(self) -> list[FlightRecord]:
        stmt = (
            select(FlightRecord)
            .options(selectinload(FlightRecord.anomalies))
            .order_by(FlightRecord.id.asc())
        )
        return list(self.session.scalars(stmt).all())

    def get_flight(self, flight_id: int) -> FlightRecord | None:
        stmt = (
            select(FlightRecord)
            .options(
                selectinload(FlightRecord.anomalies),
                selectinload(FlightRecord.events),
            )
            .where(FlightRecord.id == flight_id)
        )
        return self.session.scalars(stmt).first()

    def get_flight_by_filename(self, source_filename: str) -> FlightRecord | None:
        stmt = select(FlightRecord).where(FlightRecord.source_filename == source_filename)
        return self.session.scalars(stmt).first()

    def delete_flight(self, flight_id: int) -> bool:
        rec = self.get_flight(flight_id)
        if not rec:
            return False
        self.session.delete(rec)
        self.session.commit()
        return True

    def get_telemetry_records(
        self, flight: FlightRecord, max_points: int | None = None
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = json.loads(flight.telemetry_json or "[]")
        if max_points and len(rows) > max_points > 2:
            # Uniform index spacing guarantees exact <= max_points inclusive of first & last sample
            n = len(rows)
            indices = sorted({int(round(i * (n - 1) / (max_points - 1))) for i in range(max_points)})
            return [rows[idx] for idx in indices]
        return rows
