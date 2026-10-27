"""Unit tests for macro building summary and system diagnostics fast-path tools."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import pytest

from analytics.tools.building_tools import get_building_summary, get_system_diagnostics
from backend.app.main import app

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "2026_9_expected.json"


@pytest.fixture
def backend_client():
    """Create a FastAPI test client for backend testing."""
    return TestClient(app)


def test_get_building_summary_live_backend(backend_client):
    """Verify querying latest and specific building report periods against backend."""
    if FIXTURE_PATH.exists():
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        backend_client.post("/api/v1/reports", json=payload)

    # 1. Latest period lookup
    latest_res = get_building_summary(client=backend_client)
    assert "error" not in latest_res
    assert "period_date" in latest_res
    assert "operational_total" in latest_res
    assert "calculated_grand_total" in latest_res
    assert isinstance(latest_res["sections"], list)

    # 2. Specific period lookup (both YYYY-MM and YYYY-MM-01)
    exact_res = get_building_summary(period_date="2026-09", client=backend_client)
    assert "error" not in exact_res
    assert exact_res["period_date"] == "2026-09-01"
    assert exact_res["calculated_grand_total"] > 0


def test_get_building_summary_not_found(backend_client):
    """Verify non-existent period returns clean error dict."""
    res = get_building_summary(period_date="1999-01-01", client=backend_client)
    assert "error" in res
    assert "No building expense report found" in res["error"]


def test_get_system_diagnostics_live_backend(backend_client):
    """Verify system diagnostics retrieves loaded periods and spool queue counts."""
    res = get_system_diagnostics(client=backend_client)
    assert "error" not in res
    assert res["status"] == "healthy"
    assert isinstance(res["loaded_periods"], list)
    assert "pending_count" in res
    assert "synced_count" in res
    assert "failed_count" in res


def test_building_tools_network_error():
    """Verify network errors return clean error dict without unhandled exceptions."""
    mock_client = MagicMock()
    mock_client.get.side_effect = ConnectionError("Backend connection refused")

    res_summary = get_building_summary(client=mock_client)
    assert "error" in res_summary
    assert "Service error" in res_summary["error"]

    res_diag = get_system_diagnostics(client=mock_client)
    assert "error" in res_diag
    assert "Service error" in res_diag["error"]
