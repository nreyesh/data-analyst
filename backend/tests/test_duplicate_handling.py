"""Tests for duplicate detection, overwrite logic, and orphaned records prevention."""

from __future__ import annotations

import copy
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session


def test_duplicate_overwrite_status_flags(client: TestClient, sample_payload_2026_9: dict):
    """Verify first post returns created (201) and second post returns overwritten (200)."""
    # 1. First submission
    res1 = client.post("/api/v1/reports", json=sample_payload_2026_9)
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["status"] == "created"
    assert data1["overwritten"] is False

    # 2. Duplicate submission for the same month
    res2 = client.post("/api/v1/reports", json=sample_payload_2026_9)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "overwritten"
    assert data2["overwritten"] is True
    assert "already existed and was successfully overwritten" in data2["message"]


def test_duplicate_overwrite_updates_content(client: TestClient, sample_payload_2026_9: dict):
    """Verify that overwriting updates line item amounts and totals."""
    # First submission
    res1 = client.post("/api/v1/reports", json=sample_payload_2026_9)
    initial_total = res1.json()["calculated_grand_total"]

    # Modified duplicate submission with higher amount
    modified_payload = copy.deepcopy(sample_payload_2026_9)
    modified_payload["sections"][0]["sub_topics"][0]["value"] = 50_000_000

    res2 = client.post("/api/v1/reports", json=modified_payload)
    assert res2.status_code == 200
    updated_total = res2.json()["calculated_grand_total"]
    assert updated_total > initial_total

    # Verify query returns updated total
    by_period = client.get("/api/v1/reports/period/2026-10-01").json()
    assert by_period["calculated_grand_total"] == updated_total


def test_zero_orphaned_records_after_overwrite(
    client: TestClient, db_session: Session, sample_payload_2026_9: dict
):
    """Verify that overwriting does not leave orphaned sections or items."""
    # First submission
    client.post("/api/v1/reports", json=sample_payload_2026_9)

    # Overwrite submission with only 1 section and 1 item
    reduced_payload = {
        "issue_date": "01/10/2026",
        "sections": [
            {
                "general_topic": "Only Section",
                "sub_topics": [
                    {"name": "Single Item", "value": 12345}
                ],
            }
        ],
    }

    res2 = client.post("/api/v1/reports", json=reduced_payload)
    assert res2.status_code == 200
    assert res2.json()["sections_count"] == 1
    assert res2.json()["items_count"] == 1

    # Check raw SQL table counts
    reports_count = db_session.execute(text("SELECT COUNT(*) FROM expense_reports")).scalar()
    sections_count = db_session.execute(text("SELECT COUNT(*) FROM expense_sections")).scalar()
    items_count = db_session.execute(text("SELECT COUNT(*) FROM expense_items")).scalar()

    assert reports_count == 1
    assert sections_count == 1
    assert items_count == 1
