"""Tests for expense report ingestion endpoint and retrieval."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_ingest_sample_report_success(client: TestClient, sample_payload_2026_9: dict):
    """Verify successful ingestion of sample 2026-9.json."""
    response = client.post("/api/v1/reports", json=sample_payload_2026_9)
    assert response.status_code == 201

    data = response.json()
    assert data["status"] == "created"
    assert data["overwritten"] is False
    assert data["period_date"] == "2026-10-01"
    assert data["sections_count"] == len(sample_payload_2026_9["sections"])
    assert data["calculated_grand_total"] > 0
    assert "id" in data
    assert len(data["id"]) > 0


def test_list_reports_after_ingestion(client: TestClient, sample_payload_2026_9: dict):
    """Verify GET /api/v1/reports returns stored summaries."""
    # Ingest report
    client.post("/api/v1/reports", json=sample_payload_2026_9)

    # List reports
    response = client.get("/api/v1/reports")
    assert response.status_code == 200
    reports = response.json()
    assert len(reports) == 1
    assert reports[0]["period_date"] == "2026-10-01"
    assert reports[0]["period_year"] == 2026
    assert reports[0]["period_month"] == 10


def test_get_report_by_id_and_period(client: TestClient, sample_payload_2026_9: dict):
    """Verify hierarchical detail retrieval by UUID and period date."""
    ingest_res = client.post("/api/v1/reports", json=sample_payload_2026_9)
    report_id = ingest_res.json()["id"]

    # Fetch by ID
    by_id_res = client.get(f"/api/v1/reports/{report_id}")
    assert by_id_res.status_code == 200
    detail = by_id_res.json()
    assert detail["id"] == report_id
    assert len(detail["sections"]) == len(sample_payload_2026_9["sections"])
    assert detail["telemetry_metadata"] is not None
    assert detail["telemetry_metadata"]["model_used"] == "gemini-3.1-flash-lite"

    # Fetch by Period
    by_period_res = client.get("/api/v1/reports/period/2026-10-01")
    assert by_period_res.status_code == 200
    assert by_period_res.json()["id"] == report_id


def test_lowercase_normalization(client: TestClient, sample_payload_2026_9: dict):
    """Verify line items have their names normalized to strictly lowercase."""
    ingest_res = client.post("/api/v1/reports", json=sample_payload_2026_9)
    report_id = ingest_res.json()["id"]

    detail = client.get(f"/api/v1/reports/{report_id}").json()
    for section in detail["sections"]:
        for item in section["items"]:
            assert item["normalized_name"] == item["name"].lower().strip()
