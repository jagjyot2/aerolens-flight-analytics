"""Pydantic v2 request and response schemas for AeroLens REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AnomalyDTO(BaseModel):
    anomaly_id: str
    category: str
    code: str
    severity: str
    title: str
    description: str
    start_time_s: float
    end_time_s: float
    peak_time_s: float
    peak_value: float
    threshold_value: float
    unit: str
    recommendation: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class TimelineEventDTO(BaseModel):
    timestamp_s: float
    end_time_s: float | None = None
    event_type: str
    category: str
    severity: str
    title: str
    description: str


class FlightListItemDTO(BaseModel):
    id: int
    name: str
    source_filename: str
    format_id: str
    autopilot: str
    vehicle_type: str
    firmware_version: str
    is_synthetic: bool
    synthetic_label: str
    health_score: float
    health_grade: str
    flight_status: str
    duration_s: float
    sample_count: int
    anomaly_count: int
    critical_count: int
    warning_count: int
    created_at: datetime
    summary: dict[str, Any] = Field(default_factory=dict)


class FlightDetailDTO(FlightListItemDTO):
    metadata: dict[str, Any] = Field(default_factory=dict)
    fft_spectrum: dict[str, list[float]] = Field(default_factory=dict)
    anomalies: list[AnomalyDTO] = Field(default_factory=list)
    events: list[TimelineEventDTO] = Field(default_factory=list)


class FlightTelemetryDTO(BaseModel):
    flight_id: int
    name: str
    sample_count: int
    returned_count: int
    records: list[dict[str, Any]]
    fft_spectrum: dict[str, list[float]]


class CompareRequest(BaseModel):
    flight_ids: list[int] = Field(..., min_length=2, max_length=6)
    n_points: int = Field(default=80, ge=20, le=300)


class ParserInfoDTO(BaseModel):
    format_id: str
    display_name: str
    supported_extensions: list[str]
