"""Tests for backend batch sync routes and queue transitions."""

from __future__ import annotations

import json
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.repository import find_report_by_period


def test_get_queue_status(client: TestClient, tmp_path: Path, monkeypatch):
    """Verify GET /api/v1/sync/status returns counts across queue directories."""
    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    settings.ensure_directories()

    # Create dummy files
    (settings.pending_dir / "pending1.json").write_text("{}", encoding="utf-8")
    (settings.synced_dir / "synced1.json").write_text("{}", encoding="utf-8")
    (settings.failed_dir / "failed1.json").write_text("{}", encoding="utf-8")

    res = client.get("/api/v1/sync/status")
    assert res.status_code == 200
    data = res.json()
    assert data["pending_count"] == 1
    assert data["synced_count"] == 1
    assert data["failed_count"] == 1
    assert data["pending"] == ["pending1.json"]
    assert data["synced"] == ["synced1.json"]
    assert data["failed"] == ["failed1.json"]


def test_sync_pending_moves_to_synced_and_persists(
    client: TestClient,
    db_session: Session,
    sample_payload_2026_9: dict,
    tmp_path: Path,
    monkeypatch,
):
    """Verify batch sync ingests pending file, commits to DB, and moves file to synced/."""
    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    settings.ensure_directories()

    pending_file = settings.pending_dir / "2026-9.json"
    with open(pending_file, "w", encoding="utf-8") as f:
        json.dump(sample_payload_2026_9, f)

    assert pending_file.exists()

    # Trigger batch sync
    res = client.post("/api/v1/sync/pending")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "completed"
    assert data["synced_count"] == 1
    assert data["failed_count"] == 0
    assert data["synced_files"] == ["2026-9.json"]

    # File moved out of pending and into synced
    assert not pending_file.exists()
    assert (settings.synced_dir / "2026-9.json").exists()

    # Data committed to database
    report = find_report_by_period(db_session, "2026-10-01")
    assert report is not None
    assert report.calculated_grand_total > 0


def test_sync_pending_moves_invalid_to_failed(
    client: TestClient,
    sample_payload_2026_9: dict,
    tmp_path: Path,
    monkeypatch,
):
    """Verify batch sync routes schema-invalid files to failed/ without blocking valid ones."""
    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    settings.ensure_directories()

    # 1. Valid pending file
    valid_file = settings.pending_dir / "valid_2026_9.json"
    with open(valid_file, "w", encoding="utf-8") as f:
        json.dump(sample_payload_2026_9, f)

    # 2. Corrupt / invalid pending file (bad date format)
    bad_payload = {**sample_payload_2026_9, "issue_date": "invalid-date"}
    bad_file = settings.pending_dir / "bad_report.json"
    with open(bad_file, "w", encoding="utf-8") as f:
        json.dump(bad_payload, f)

    # Run sync
    res = client.post("/api/v1/sync/pending")
    assert res.status_code == 200
    data = res.json()
    assert data["total_processed"] == 2
    assert data["synced_count"] == 1
    assert data["failed_count"] == 1
    assert "valid_2026_9.json" in data["synced_files"]
    assert any(item["filename"] == "bad_report.json" for item in data["failed_files"])

    # Verify directory movements
    assert (settings.synced_dir / "valid_2026_9.json").exists()
    assert (settings.failed_dir / "bad_report.json").exists()
    assert not (settings.pending_dir / "valid_2026_9.json").exists()
    assert not (settings.pending_dir / "bad_report.json").exists()
