"""Integration tests for FastAPI endpoints, SQLite persistence, and PDF/HTML/CSV exports."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.database import create_sqlite_engine, init_db
from backend.main import app
from backend.config import settings
from sqlalchemy.orm import sessionmaker
from backend import database as db_module


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Provide a FastAPI TestClient wired to an isolated temporary SQLite database."""
    test_db_url = f"sqlite:///{(tmp_path / 'test_aerolens.db').as_posix()}"
    test_engine = create_sqlite_engine(test_db_url)
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)

    monkeypatch.setattr(settings, "database_url", test_db_url)
    monkeypatch.setattr(settings, "auto_seed_samples", True)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestingSession)
    import backend.main as main_module
    monkeypatch.setattr(main_module, "SessionLocal", TestingSession)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[db_module.get_db] = override_get_db
    init_db(test_engine)

    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()
    test_engine.dispose()


def test_health_parsers_and_seeded_samples(client: TestClient) -> None:
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    body = r_health.json()
    assert body["status"] == "ok"
    assert body["flight_count"] == 4
    assert set(body["parsers"]) == {"px4_ulog", "mavlink_tlog", "ardupilot_csv"}

    r_parsers = client.get("/api/parsers")
    assert r_parsers.status_code == 200
    assert len(r_parsers.json()) == 3

    r_root = client.get("/")
    assert r_root.status_code == 200
    assert "text/html" in r_root.headers["content-type"]
    assert "AeroLens" in r_root.text or "AEROLENS" in r_root.text


def test_upload_telemetry_compare_and_delete(client: TestClient, sample_data_dir: Path) -> None:
    sample_file = sample_data_dir / "synthetic_nominal_survey_flight.csv"
    with sample_file.open("rb") as f:
        r_up = client.post(
            "/api/flights/upload",
            files={"file": (sample_file.name, f, "text/csv")},
            data={"name": "Custom Uploaded Survey"},
        )
    assert r_up.status_code == 201
    uploaded = r_up.json()
    fid = uploaded["id"]
    assert uploaded["name"] == "Custom Uploaded Survey"
    assert uploaded["is_synthetic"] is True
    assert uploaded["health_score"] >= 90.0

    r_detail = client.get(f"/api/flights/{fid}")
    assert r_detail.status_code == 200
    assert r_detail.json()["summary"]["sample_count"] == 2400

    r_tel = client.get(f"/api/flights/{fid}/telemetry?max_points=250")
    assert r_tel.status_code == 200
    tel = r_tel.json()
    assert tel["returned_count"] <= 260
    assert len(tel["records"]) == tel["returned_count"]

    r_list = client.get("/api/flights")
    flights = r_list.json()
    assert len(flights) == 5

    r_cmp = client.post("/api/flights/compare", json={"flight_ids": [flights[0]["id"], flights[1]["id"]], "n_points": 40})
    assert r_cmp.status_code == 200
    cmp_body = r_cmp.json()
    assert len(cmp_body["flights"]) == 2
    assert len(cmp_body["metric_deltas"]) >= 5

    r_del = client.delete(f"/api/flights/{fid}")
    assert r_del.status_code == 200
    assert r_del.json()["deleted"] is True
    assert client.get(f"/api/flights/{fid}").status_code == 404


def test_exports_pdf_html_csv(client: TestClient) -> None:
    flights = client.get("/api/flights").json()
    assert len(flights) >= 1
    # Pick the vibration/voltage anomaly flight to verify anomaly sections render in exports
    target = next((f for f in flights if "voltage_sag" in f["source_filename"].lower()), flights[0])
    fid = target["id"]

    r_pdf = client.get(f"/api/flights/{fid}/export/pdf")
    assert r_pdf.status_code == 200
    assert r_pdf.headers["content-type"].startswith("application/pdf")
    assert r_pdf.content.startswith(b"%PDF-")
    assert len(r_pdf.content) > 2500

    r_html = client.get(f"/api/flights/{fid}/export/html")
    assert r_html.status_code == 200
    assert "text/html" in r_html.headers["content-type"]
    assert "AEROLENS" in r_html.text
    assert "<svg" in r_html.text

    r_csv = client.get(f"/api/flights/{fid}/export/csv")
    assert r_csv.status_code == 200
    assert "text/csv" in r_csv.headers["content-type"]
    assert "timestamp_s" in r_csv.text
    assert "anomaly_active" in r_csv.text


def test_upload_error_handling(client: TestClient) -> None:
    r_bad = client.post(
        "/api/flights/upload",
        files={"file": ("broken.csv", b"not_valid_telemetry", "text/csv")},
    )
    assert r_bad.status_code == 400
    assert client.get("/api/flights/999999").status_code == 404
    assert client.get("/api/flights/999999/export/pdf").status_code == 404
    assert client.post("/api/flights/compare", json={"flight_ids": [1]}).status_code == 422
    assert client.post("/api/flights/compare", json={"flight_ids": [1, 999999]}).status_code == 404
