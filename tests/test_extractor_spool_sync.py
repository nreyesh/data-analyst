"""Integration tests for extractor direct loading, spool routing, and CLI sync/status."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import requests

from data_extractor.cli import handle_status_command, handle_sync_command
from data_extractor.config import get_settings
from data_extractor.workflow import load_and_spool_report


@pytest.fixture
def mock_output_data() -> dict:
    """Sample minimal validated expense report dictionary."""
    return {
        "issue_date": "01/10/2026",
        "sections": [
            {
                "general_topic": "Consumos",
                "sub_topics": [{"name": "Agua", "value": 1000}],
            }
        ],
        "_metadata": {"validation_status": "PASSED"},
    }


def test_workflow_direct_save_to_synced_when_online(
    tmp_path: Path, monkeypatch, mock_output_data: dict
):
    """Verify that when backend responds 201/200, report is saved directly to synced/."""
    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    settings.ensure_directories()

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"id": "019273c5-test", "overwritten": False}

    with patch("requests.post", return_value=mock_resp):
        dest, spool_status = load_and_spool_report(
            output_data=copy.deepcopy(mock_output_data),
            stem="2026-9",
            is_valid=True,
        )

    assert spool_status == "synced"
    assert dest == settings.synced_dir / "2026-9.json"
    assert dest.exists()
    assert not (settings.pending_dir / "2026-9.json").exists()

    saved_data = json.loads(dest.read_text(encoding="utf-8"))
    assert saved_data["_sync"]["status"] == "SYNCED"
    assert saved_data["_sync"]["report_id"] == "019273c5-test"


def test_workflow_fallback_to_pending_when_offline(
    tmp_path: Path, monkeypatch, mock_output_data: dict
):
    """Verify that when backend is offline, report is saved to pending/ without crashing."""
    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    settings.ensure_directories()

    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("Connection refused")):
        dest, spool_status = load_and_spool_report(
            output_data=copy.deepcopy(mock_output_data),
            stem="2026-9",
            is_valid=True,
        )

    assert spool_status == "pending"
    assert dest == settings.pending_dir / "2026-9.json"
    assert dest.exists()
    assert not (settings.synced_dir / "2026-9.json").exists()

    saved_data = json.loads(dest.read_text(encoding="utf-8"))
    assert saved_data["_sync"]["status"] == "PENDING"
    assert "Connection refused" in saved_data["_sync"]["error"]


def test_workflow_routes_to_failed_when_rejected(
    tmp_path: Path, monkeypatch, mock_output_data: dict
):
    """Verify that when backend returns 422, report is saved to failed/."""
    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    settings.ensure_directories()

    mock_resp = MagicMock()
    mock_resp.status_code = 422
    mock_resp.text = "Unprocessable Entity: Malformed issue_date"

    with patch("requests.post", return_value=mock_resp):
        dest, spool_status = load_and_spool_report(
            output_data=copy.deepcopy(mock_output_data),
            stem="2026-9",
            is_valid=True,
        )

    assert spool_status == "failed"
    assert dest == settings.failed_dir / "2026-9.json"
    assert dest.exists()
    assert not (settings.synced_dir / "2026-9.json").exists()
    assert not (settings.pending_dir / "2026-9.json").exists()

    saved_data = json.loads(dest.read_text(encoding="utf-8"))
    assert saved_data["_sync"]["status"] == "FAILED"
    assert saved_data["_sync"]["status_code"] == 422


def test_cli_status_command_online_and_offline(tmp_path: Path, monkeypatch):
    """Verify handle_status_command queries backend when online, falls back to local scan when offline."""
    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    settings.ensure_directories()

    # 1. Online backend
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "pending_count": 1,
        "synced_count": 2,
        "failed_count": 0,
        "pending": ["2026-10.json"],
        "synced": ["2026-8.json", "2026-9.json"],
        "failed": [],
    }
    with patch("requests.get", return_value=mock_resp):
        exit_code = handle_status_command()
        assert exit_code == 0

    # 2. Offline backend
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError("Offline")):
        exit_code = handle_status_command()
        assert exit_code == 0


def test_cli_sync_command_execution():
    """Verify handle_sync_command sends POST to backend sync endpoint."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "completed",
        "message": "Reconciled 1 reports successfully.",
        "synced_files": ["2026-10.json"],
        "failed_files": [],
    }
    with patch("requests.post", return_value=mock_resp) as mock_post:
        exit_code = handle_sync_command()
        assert exit_code == 0
        mock_post.assert_called_once()
