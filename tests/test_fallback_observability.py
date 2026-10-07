"""Unit tests for dual-model failover and observability telemetry."""

from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest
from google.genai.errors import ServerError

from data_extractor.config import Settings
from data_extractor.extractor import extract_from_pdf
from data_extractor.models import ExtractionMetadata, ExtractionState
from data_extractor.workflow import format_output_node


SAMPLE_JSON = json.dumps({
    "issue_date": "01/10/2026",
    "sections": [
        {
            "general_topic": "Consumos",
            "sub_topics": [{"name": "Agua Potable", "value": 3072726}],
        }
    ]
})


def test_primary_model_success_telemetry(monkeypatch):
    """Test telemetry generation when primary model succeeds."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = SAMPLE_JSON
    mock_client.models.generate_content.return_value = mock_response

    # Force known test settings
    test_settings = Settings(
        default_model="gemini-3.5-flash-lite",
        fallback_model="gemini-3.1-flash-lite",
    )
    monkeypatch.setattr("data_extractor.extractor.get_settings", lambda: test_settings)

    report, metadata = extract_from_pdf(
        pdf_source=b"%PDF-1.4 mock",
        client=mock_client,
    )

    assert report.issue_date == "01/10/2026"
    assert metadata.model_used == "gemini-3.5-flash-lite"
    assert metadata.fallback_triggered is False
    assert metadata.fallback_reason is None
    assert metadata.latency_seconds >= 0.0
    assert mock_client.models.generate_content.call_count == 1


def test_fallback_activated_on_primary_error(monkeypatch):
    """Test transparent failover to secondary model when primary fails."""
    mock_client = MagicMock()
    mock_success_response = MagicMock()
    mock_success_response.text = SAMPLE_JSON

    # First call (primary) raises ServerError(503), second call (fallback) succeeds
    mock_client.models.generate_content.side_effect = [
        ServerError(503, {"message": "Service Unavailable on primary model"}),
        mock_success_response,
    ]

    test_settings = Settings(
        default_model="gemini-3.5-flash-lite",
        fallback_model="gemini-3.1-flash-lite",
    )
    monkeypatch.setattr("data_extractor.extractor.get_settings", lambda: test_settings)

    report, metadata = extract_from_pdf(
        pdf_source=b"%PDF-1.4 mock",
        client=mock_client,
    )

    assert report.issue_date == "01/10/2026"
    assert metadata.model_used == "gemini-3.1-flash-lite"
    assert metadata.fallback_triggered is True
    assert "ServerError" in metadata.fallback_reason
    assert "503" in metadata.fallback_reason
    assert mock_client.models.generate_content.call_count == 2


def test_all_models_failing_raises_runtime_error(monkeypatch):
    """Test exception propagation when all candidate models fail."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        ServerError(503, {"message": "Primary 503"}),
        ServerError(500, {"message": "Fallback 500"}),
    ]

    test_settings = Settings(
        default_model="gemini-3.5-flash-lite",
        fallback_model="gemini-3.1-flash-lite",
    )
    monkeypatch.setattr("data_extractor.extractor.get_settings", lambda: test_settings)

    with pytest.raises(RuntimeError) as exc_info:
        extract_from_pdf(
            pdf_source=b"%PDF-1.4 mock",
            client=mock_client,
        )

    assert "All configured extraction models failed" in str(exc_info.value)
    assert mock_client.models.generate_content.call_count == 2


def test_format_output_node_attaches_metadata():
    """Test that format_output_node includes _metadata in final output dict."""
    metadata = ExtractionMetadata(
        model_used="gemini-3.1-flash-lite",
        fallback_triggered=True,
        fallback_reason="503 Error",
        latency_seconds=1.23,
        total_duration_seconds=2.45,
        validation_status="PASSED",
        retry_count=0,
        timestamp="2026-10-07T12:00:00Z",
    )

    state: ExtractionState = {
        "pdf_path": "test.pdf",
        "pdf_bytes": b"",
        "ground_truth_totals": {},
        "expected_grand_total": None,
        "report": None,
        "metadata": metadata,
        "validation_result": None,
        "errors": [],
        "retry_count": 0,
        "max_retries": 2,
        "is_valid": False,
        "final_output": None,
    }

    result = format_output_node(state)
    assert "_metadata" in result["final_output"]
    meta_dict = result["final_output"]["_metadata"]
    assert meta_dict["model_used"] == "gemini-3.1-flash-lite"
    assert meta_dict["fallback_triggered"] is True
    assert meta_dict["latency_seconds"] == 1.23
