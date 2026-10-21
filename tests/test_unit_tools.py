"""Unit tests for the fast-path unit billing tool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
import pytest

from analytics.tools.unit_tools import get_unit_bill_details
from backend.app.main import app

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "504b_2026_08_expected.json"


@pytest.fixture
def backend_client():
    """Create a FastAPI test client for backend testing."""
    return TestClient(app)


def test_get_unit_bill_details_empty_unit():
    """Verify empty unit_code returns error immediately."""
    res = get_unit_bill_details(unit_code="")
    assert "error" in res
    assert "unit_code is required" in res["error"]


def test_get_unit_bill_details_not_found(backend_client):
    """Verify non-existent unit returns clean error dict."""
    res = get_unit_bill_details(unit_code="NON-EXISTENT-UNIT-999", client=backend_client)
    assert "error" in res
    assert "No unit bill found" in res["error"]


def test_get_unit_bill_details_live_backend(backend_client):
    """Verify querying B-504 against backend returns structured billing snapshot."""
    # Ensure fixture is ingested if not already present
    if FIXTURE_PATH.exists():
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        backend_client.post("/api/v1/unit-bills", json=payload)

    # 1. Test latest period lookup
    latest_res = get_unit_bill_details(unit_code="B-504", client=backend_client)
    assert "error" not in latest_res
    assert latest_res["unit_code"] == "B-504"
    assert "calculated_total_month" in latest_res
    assert isinstance(latest_res["concepts"], list)
    assert isinstance(latest_res["consumptions"], list)

    # 2. Test exact period lookup
    exact_res = get_unit_bill_details(
        unit_code="B-504", period_date="2026-08-01", client=backend_client
    )
    assert "error" not in exact_res
    assert exact_res["period_date"] == "2026-08-01"
    assert exact_res["unit_code"] == "B-504"


def test_get_unit_bill_details_network_error():
    """Verify network or connection errors return clean error dict."""
    mock_client = MagicMock()
    mock_client.get.side_effect = ConnectionError("Backend connection refused")

    res = get_unit_bill_details(unit_code="B-504", client=mock_client)
    assert "error" in res
    assert "Service error" in res["error"]
