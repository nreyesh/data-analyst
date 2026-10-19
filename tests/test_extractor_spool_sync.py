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


def test_workflow_routes_to_failed_when_invalid_validation(tmp_path: Path, monkeypatch):
    """Verify that failed cross-validation routes to failed/ spool with errors intact."""
    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    settings.ensure_directories()

    output_data = {
        "issue_date": "01/10/2026",
        "sections": [],
        "_errors": ["Section 'Consumos' sum mismatch: difference: +500 CLP."],
    }

    dest, spool_status = load_and_spool_report(
        output_data=output_data,
        stem="2026-10",
        is_valid=False,
    )

    assert spool_status == "failed"
    assert dest == settings.failed_dir / "2026-10.json"
    assert dest.exists()

    saved_data = json.loads(dest.read_text(encoding="utf-8"))
    assert saved_data["_sync"]["status"] == "FAILED_VALIDATION"
    assert len(saved_data["_sync"]["errors"]) == 1
    assert "Section 'Consumos' sum mismatch" in saved_data["_sync"]["errors"][0]


def test_format_output_node_preserves_status_and_errors():
    """Verify format_output_node attaches _status: FAILED_VALIDATION and _errors when is_valid is False."""
    from data_extractor.models import ExpenseReport, Section, SubTopic
    from data_extractor.workflow import format_output_node

    report = ExpenseReport(
        issue_date="01/10/2026",
        sections=[Section(general_topic="Consumos", sub_topics=[SubTopic(name="Agua", value=100)])],
    )
    state = {
        "report": report,
        "is_valid": False,
        "errors": ["Section 'Consumos' sum mismatch: difference: -50 CLP."],
        "metadata": None,
    }

    res = format_output_node(state)
    final_output = res["final_output"]
    assert final_output["_status"] == "FAILED_VALIDATION"
    assert final_output["_errors"] == ["Section 'Consumos' sum mismatch: difference: -50 CLP."]


def test_validation_logging_does_not_leak_amounts(caplog):
    """Verify that validator logging records structural metadata without leaking CLP amounts."""
    import logging
    from data_extractor.ground_truth import GroundTruthData
    from data_extractor.models import ExpenseReport, Section, SubTopic
    from data_extractor.validator import validate_expense_report

    report = ExpenseReport(
        issue_date="01/10/2026",
        sections=[
            Section(
                general_topic="Remuneraciones",
                sub_topics=[SubTopic(name="Juan Perez", value=12345678)],
            )
        ],
    )
    gt = GroundTruthData(
        period_date="01/10/2026",
        grand_total=99999999,
        section_totals={"Remuneraciones": 87654321},
    )

    with caplog.at_level(logging.WARNING, logger="data_extractor.validator"):
        result = validate_expense_report(report, gt)

    assert result.is_valid is False
    # Logging was emitted
    assert any("Expense report validation failed" in record.message for record in caplog.records)
    # Ensure sensitive amounts and item names were NOT logged in the record messages
    for record in caplog.records:
        assert "12345678" not in record.message
        assert "87654321" not in record.message
        assert "99999999" not in record.message
        assert "Juan Perez" not in record.message


def test_stale_spool_cleanup_on_re_ingestion(
    tmp_path: Path, monkeypatch, mock_output_data: dict
):
    """Verify that when a previously failed report succeeds on re-ingestion, the stale failed JSON is unlinked."""
    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    settings.ensure_directories()

    # Create a stale failed file first
    stale_failed = settings.failed_dir / "2026-9.json"
    stale_failed.write_text('{"_status": "FAILED_VALIDATION"}', encoding="utf-8")
    assert stale_failed.exists()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "019273c5-reingest", "overwritten": True}

    with patch("requests.post", return_value=mock_resp):
        dest, spool_status = load_and_spool_report(
            output_data=copy.deepcopy(mock_output_data),
            stem="2026-9",
            is_valid=True,
        )

    assert spool_status == "synced"
    assert dest == settings.synced_dir / "2026-9.json"
    assert dest.exists()
    assert not stale_failed.exists()


def test_process_input_batch_moves_pdfs_to_processed_and_failed(tmp_path: Path, monkeypatch):
    """Verify --process-input moves valid PDFs to input/processed/ and failing PDFs to input/failed/."""
    from data_extractor.cli import handle_process_input_command

    settings = get_settings()
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    monkeypatch.setattr(settings, "input_dir", input_dir)
    monkeypatch.setattr(settings, "output_dir", output_dir)
    settings.ensure_directories()

    good_pdf = input_dir / "2026-9.pdf"
    bad_pdf = input_dir / "bad.pdf"
    good_pdf.write_bytes(b"%PDF-1.4 dummy valid")
    bad_pdf.write_bytes(b"%PDF-1.4 dummy invalid")

    def _mock_run_workflow(pdf_path: Path, max_retries=None, output_path=None):
        if Path(pdf_path).name == "2026-9.pdf":
            return {"issue_date": "01/09/2026", "sections": [], "_spool_status": "synced"}
        return {
            "issue_date": "",
            "sections": [],
            "_status": "FAILED_VALIDATION",
            "_spool_status": "failed",
        }

    with patch("data_extractor.cli.run_workflow", side_effect=_mock_run_workflow):
        exit_code = handle_process_input_command()

    assert exit_code == 1  # 1 succeeded, 1 failed
    assert not good_pdf.exists()
    assert not bad_pdf.exists()
    assert (settings.input_processed_dir / "2026-9.pdf").exists()
    assert (settings.input_failed_dir / "bad.pdf").exists()

