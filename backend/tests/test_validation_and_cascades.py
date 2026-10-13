"""Tests for schema validation failures, cascade deletes, and system health."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session


def test_invalid_issue_date_format(client: TestClient, sample_payload_2026_9: dict):
    """Verify that an issue_date not formatted as 01/MM/YYYY is rejected with 422."""
    bad_payload = {**sample_payload_2026_9, "issue_date": "2026-10-01"}
    response = client.post("/api/v1/reports", json=bad_payload)
    assert response.status_code == 422

    bad_payload2 = {**sample_payload_2026_9, "issue_date": "15/10/2026"}
    response2 = client.post("/api/v1/reports", json=bad_payload2)
    assert response2.status_code == 422


def test_negative_amount_accepted_and_persisted(client: TestClient):
    """Verify that negative line item values (credit notes) are accepted and persisted."""
    payload = {
        "issue_date": "01/07/2026",
        "sections": [
            {
                "general_topic": "Remuneraciones",
                "sub_topics": [
                    {"name": "Sueldos", "value": 1000000},
                    {"name": "Finiquitos", "value": -250000},
                ],
            }
        ],
    }
    response = client.post("/api/v1/reports", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["calculated_grand_total"] == 750000

    # Retrieve and verify subtotal and negative line item
    get_res = client.get(f"/api/v1/reports/{data['id']}")
    assert get_res.status_code == 200
    report_data = get_res.json()
    rem_section = report_data["sections"][0]
    assert rem_section["calculated_subtotal"] == 750000
    credit_item = next(it for it in rem_section["items"] if it["name"] == "Finiquitos")
    assert credit_item["amount"] == -250000


def test_empty_sections_rejected(client: TestClient):
    """Verify that an empty sections array triggers HTTP 422."""
    bad_payload = {"issue_date": "01/10/2026", "sections": []}
    response = client.post("/api/v1/reports", json=bad_payload)
    assert response.status_code == 422


def test_cascade_deletion(client: TestClient, db_session: Session, sample_payload_2026_9: dict):
    """Verify that deleting a report cascades and deletes all associated sections and items."""
    ingest_res = client.post("/api/v1/reports", json=sample_payload_2026_9)
    report_id = ingest_res.json()["id"]

    # Verify rows exist
    assert db_session.execute(text("SELECT COUNT(*) FROM expense_reports")).scalar() == 1
    assert db_session.execute(text("SELECT COUNT(*) FROM expense_sections")).scalar() > 0
    assert db_session.execute(text("SELECT COUNT(*) FROM expense_items")).scalar() > 0

    # Delete report
    del_res = client.delete(f"/api/v1/reports/{report_id}")
    assert del_res.status_code == 204

    # Verify cascade deleted all children
    assert db_session.execute(text("SELECT COUNT(*) FROM expense_reports")).scalar() == 0
    assert db_session.execute(text("SELECT COUNT(*) FROM expense_sections")).scalar() == 0
    assert db_session.execute(text("SELECT COUNT(*) FROM expense_items")).scalar() == 0


def test_health_endpoint(client: TestClient):
    """Verify GET /health returns status healthy."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
