"""Unit and security tests for Data Gateway Proxy endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.routes.analytics import (
    extract_cte_names,
    extract_table_names,
    strip_sql_comments,
    validate_query_security,
)


@pytest.fixture
def client() -> TestClient:
    """Standard FastAPI TestClient."""
    return TestClient(app)


# ============================================================================
# 1. SQL Parser & Security Unit Tests
# ============================================================================


def test_strip_sql_comments():
    """Verify single and multi-line comments are safely stripped."""
    sql = "SELECT * /* multi line \n comment */ FROM expense_reports -- single line comment"
    cleaned = strip_sql_comments(sql)
    assert "multi line" not in cleaned
    assert "single line" not in cleaned
    assert "SELECT *" in cleaned
    assert "FROM expense_reports" in cleaned


def test_extract_cte_names():
    """Verify CTE names are extracted properly."""
    sql = "WITH monthly_summary AS (SELECT * FROM expense_reports), item_ranks AS (SELECT * FROM expense_items) SELECT * FROM monthly_summary"
    ctes = extract_cte_names(sql)
    assert ctes == {"monthly_summary", "item_ranks"}


def test_extract_table_names():
    """Verify tables in FROM and JOIN are extracted."""
    sql = "SELECT * FROM expense_sections s JOIN expense_reports r ON s.report_id = r.id LEFT JOIN expense_items i ON i.section_id = s.id"
    tables = extract_table_names(sql)
    assert tables == {"expense_sections", "expense_reports", "expense_items"}


# ============================================================================
# 2. Scope & Security Guardrail Tests (Anti-Introspection & Mutability)
# ============================================================================


def test_security_blocks_system_catalogs():
    """Verify queries targeting sqlite_master or sqlite_schema are rejected with HTTP 403."""
    prohibited_queries = [
        "SELECT name FROM sqlite_master WHERE type='table'",
        "SELECT * FROM sqlite_schema",
        "SELECT sql FROM sqlite_temp_master",
        "SELECT * FROM pragma_table_info('expense_reports')",
        "PRAGMA table_info(expense_reports)",
    ]
    for q in prohibited_queries:
        with pytest.raises(Exception) as exc_info:
            validate_query_security(q)
        assert exc_info.value.status_code == 403
        assert "forbidden" in exc_info.value.detail.lower()


def test_security_blocks_unauthorized_tables():
    """Verify queries targeting non-whitelisted tables are rejected with HTTP 403."""
    queries = [
        "SELECT * FROM users",
        "SELECT * FROM passwords",
        "SELECT * FROM building_admin_secrets",
    ]
    for q in queries:
        with pytest.raises(Exception) as exc_info:
            validate_query_security(q)
        assert exc_info.value.status_code == 403
        assert "not in the authorized table whitelist" in exc_info.value.detail


def test_security_blocks_mutations_and_multi_statements():
    """Verify mutations and statement chaining are rejected with HTTP 400."""
    queries = [
        "DELETE FROM expense_reports",
        "DROP TABLE expense_items",
        "INSERT INTO expense_reports (id) VALUES ('123')",
        "UPDATE expense_sections SET calculated_subtotal = 0",
        "SELECT 1; DROP TABLE expense_reports",
        "SELECT 1; SELECT 2",
    ]
    for q in queries:
        with pytest.raises(Exception) as exc_info:
            validate_query_security(q)
        assert exc_info.value.status_code == 400


def test_security_allows_valid_whitelisted_and_cte_queries():
    """Verify legitimate read-only queries with CTEs pass security checks."""
    valid_queries = [
        "SELECT period_date, calculated_grand_total FROM expense_reports ORDER BY period_date",
        "WITH cte AS (SELECT * FROM expense_sections) SELECT * FROM cte",
        "SELECT s.category_name, i.name FROM expense_sections s JOIN expense_items i ON s.id = i.section_id",
    ]
    for q in valid_queries:
        validate_query_security(q)  # Should not raise


# ============================================================================
# 3. HTTP Data Gateway Endpoint Integration Tests
# ============================================================================


def test_gateway_execute_valid_query(client: TestClient):
    """Verify legitimate query execution against real database returns structured rows."""
    payload = {
        "sql": "SELECT period_date, calculated_grand_total FROM expense_reports ORDER BY period_date ASC LIMIT 5",
        "timeout_seconds": 3.0,
        "max_rows": 10,
    }
    response = client.post("/api/v1/analytics/execute-query", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "period_date" in data["columns"]
    assert "calculated_grand_total" in data["columns"]
    assert len(data["rows"]) > 0
    assert data["row_count"] == len(data["rows"])
    assert data["truncated"] is False
    assert data["execution_time_ms"] > 0


def test_gateway_execute_forbidden_introspection_http(client: TestClient):
    """Verify POST endpoint returns HTTP 403 on sqlite_master probe."""
    payload = {
        "sql": "SELECT name FROM sqlite_master WHERE type='table'",
    }
    response = client.post("/api/v1/analytics/execute-query", json=payload)
    assert response.status_code == 403
    assert "Access to system catalogs" in response.json()["detail"]


def test_gateway_execute_syntax_error(client: TestClient):
    """Verify invalid column returns HTTP 400 with diagnostic message."""
    payload = {
        "sql": "SELECT non_existent_column_xyz FROM expense_reports",
    }
    response = client.post("/api/v1/analytics/execute-query", json=payload)
    assert response.status_code == 400
    assert "SQLite Operational Error" in response.json()["detail"]


def test_gateway_execute_timeout_cancellation(client: TestClient):
    """Verify runaway recursive query is interrupted and returns HTTP 408 within ceiling."""
    payload = {
        "sql": "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt) SELECT count(*) FROM cnt",
        "timeout_seconds": 0.2,
    }
    response = client.post("/api/v1/analytics/execute-query", json=payload)
    assert response.status_code == 408
    assert "timeout" in response.json()["detail"].lower()


def test_gateway_execute_row_truncation(client: TestClient):
    """Verify query results are capped at max_rows with truncated flag set."""
    payload = {
        "sql": "SELECT * FROM expense_items",
        "max_rows": 5,
    }
    response = client.post("/api/v1/analytics/execute-query", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["row_count"] == 5
    assert len(data["rows"]) == 5
    assert data["truncated"] is True
