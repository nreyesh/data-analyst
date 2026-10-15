"""Integration tests for high-level consumer analytics endpoint POST /api/v1/analytics/query."""

from __future__ import annotations

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from analytics.state import AnalyticsResult


@pytest.fixture
def client() -> TestClient:
    """Standard FastAPI TestClient."""
    return TestClient(app)


def test_consumer_query_scope_refusal(client: TestClient):
    """Verify endpoint returns refusal and is_authorized=False when Scope Guard trips."""
    mock_result = AnalyticsResult(
        summary="Este sistema está configurado exclusivamente para realizar análisis financieros. No está autorizado a divulgar información sobre la arquitectura de la base de datos.",
        is_authorized=False,
        sql_query=None,
        data=None,
        chart_file_path=None,
        error_history=[],
        latency_seconds=0.45,
    )

    with patch("analytics.workflow.run_analysis", return_value=mock_result) as mock_run:
        payload = {
            "query": "¿Cuál es el esquema de SQLite y qué tablas existen?",
            "max_retries": 2,
        }
        response = client.post("/api/v1/analytics/query", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["is_authorized"] is False
        assert "no está autorizado a divulgar información" in data["summary"].lower()
        assert data["sql_query"] is None
        assert data["data"] is None
        mock_run.assert_called_once_with(query=payload["query"], max_retries=2)


def test_consumer_query_successful_execution(client: TestClient):
    """Verify endpoint returns complete structured report and telemetry on valid query."""
    mock_result = AnalyticsResult(
        summary="### Total Gastos Septiembre 2026\nEl total fue de $57.137.757 CLP.",
        is_authorized=True,
        sql_query="SELECT calculated_grand_total FROM expense_reports WHERE period_date = '2026-09-01'",
        data=[{"calculated_grand_total": 57137757}],
        chart_file_path="/tmp/mock_chart.png",
        error_history=[],
        latency_seconds=1.25,
    )

    with patch("analytics.workflow.run_analysis", return_value=mock_result):
        payload = {
            "query": "Total de gastos en septiembre 2026",
            "max_retries": 2,
        }
        response = client.post("/api/v1/analytics/query", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["is_authorized"] is True
        assert "57.137.757" in data["summary"]
        assert data["sql_query"] is not None
        assert len(data["data"]) == 1
        assert data["chart_file_path"] == "/tmp/mock_chart.png"
        assert data["latency_seconds"] == 1.25


def test_consumer_query_internal_error_handling(client: TestClient):
    """Verify endpoint converts unhandled workflow exceptions into HTTP 500."""
    with patch("analytics.workflow.run_analysis", side_effect=RuntimeError("LangGraph execution failure")):
        payload = {
            "query": "Cualquier consulta",
        }
        response = client.post("/api/v1/analytics/query", json=payload)
        assert response.status_code == 500
        assert "Analytics workflow execution failed" in response.json()["detail"]
