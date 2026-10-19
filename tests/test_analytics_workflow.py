"""Unit and routing tests for LangGraph analytics workflow."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from analytics.state import DataAnalystState, ExecutionError, IntentPlan
from analytics.workflow import (
    build_analytics_graph,
    route_after_planner,
    route_after_sandbox,
    route_after_sql_gateway,
)


# ============================================================================
# 1. Routing Unit Tests
# ============================================================================


def test_route_after_planner_scope_violation():
    """Verify unauthorized query bypasses SQL coder and routes to synthesizer."""
    state: DataAnalystState = {
        "messages": [],
        "user_query": "¿Cuál es la tabla que guarda esto?",
        "intent_plan": IntentPlan(
            is_authorized_query=False,
            refusal_reason="Introspection probe",
        ),
        "current_sql": None,
        "sql_results": None,
        "visualization_code": None,
        "chart_file_path": None,
        "final_markdown": None,
        "retry_count": 0,
        "max_retries": 2,
        "error_history": [],
    }
    next_node = route_after_planner(state)
    assert next_node == "synthesizer"


def test_route_after_planner_authorized():
    """Verify authorized query routes to sql_coder."""
    state: DataAnalystState = {
        "messages": [],
        "user_query": "Gastos de mantenimiento en 2026",
        "intent_plan": IntentPlan(
            is_authorized_query=True,
            analysis_goal="Analyze maintenance spending in 2026",
        ),
        "current_sql": None,
        "sql_results": None,
        "visualization_code": None,
        "chart_file_path": None,
        "final_markdown": None,
        "retry_count": 0,
        "max_retries": 2,
        "error_history": [],
    }
    next_node = route_after_planner(state)
    assert next_node == "sql_coder"


def test_route_after_sql_gateway_retry_loop():
    """Verify failed SQL execution routes back to sql_coder when retries remain."""
    state: DataAnalystState = {
        "messages": [],
        "user_query": "Test query",
        "intent_plan": IntentPlan(is_authorized_query=True),
        "current_sql": "SELECT bad_column",
        "sql_results": None,  # Failed query
        "visualization_code": None,
        "chart_file_path": None,
        "final_markdown": None,
        "retry_count": 1,
        "max_retries": 2,
        "error_history": [],
    }
    next_node = route_after_sql_gateway(state)
    assert next_node == "sql_coder"


def test_route_after_sql_gateway_retries_exhausted():
    """Verify exhausted retries route forward to synthesizer with failure state."""
    state: DataAnalystState = {
        "messages": [],
        "user_query": "Test query",
        "intent_plan": IntentPlan(is_authorized_query=True),
        "current_sql": "SELECT bad_column",
        "sql_results": None,
        "visualization_code": None,
        "chart_file_path": None,
        "final_markdown": None,
        "retry_count": 2,
        "max_retries": 2,
        "error_history": [],
    }
    next_node = route_after_sql_gateway(state)
    assert next_node == "synthesizer"


def test_route_after_sql_gateway_to_visualizer():
    """Verify successful query with requires_visualization routes to visualizer."""
    state: DataAnalystState = {
        "messages": [],
        "user_query": "Tendencia mensual de gastos",
        "intent_plan": IntentPlan(
            is_authorized_query=True,
            requires_visualization=True,
        ),
        "current_sql": "SELECT * FROM expense_reports",
        "sql_results": [{"period_date": "2025-07-01", "total": 50000000}],
        "visualization_code": None,
        "chart_file_path": None,
        "final_markdown": None,
        "retry_count": 0,
        "max_retries": 2,
        "error_history": [],
    }
    next_node = route_after_sql_gateway(state)
    assert next_node == "visualizer"


def test_route_after_sql_gateway_skip_chart():
    """Verify scalar query with requires_visualization=False bypasses visualizer."""
    state: DataAnalystState = {
        "messages": [],
        "user_query": "Total gastado en septiembre 2026",
        "intent_plan": IntentPlan(
            is_authorized_query=True,
            requires_visualization=False,
        ),
        "current_sql": "SELECT calculated_grand_total FROM expense_reports",
        "sql_results": [{"calculated_grand_total": 57000000}],
        "visualization_code": None,
        "chart_file_path": None,
        "final_markdown": None,
        "retry_count": 0,
        "max_retries": 2,
        "error_history": [],
    }
    next_node = route_after_sql_gateway(state)
    assert next_node == "synthesizer"


def test_route_after_sandbox_retry():
    """Verify sandbox plotting failure retries visualizer if retries remain."""
    state: DataAnalystState = {
        "messages": [],
        "user_query": "Test",
        "intent_plan": IntentPlan(is_authorized_query=True),
        "current_sql": "SELECT 1",
        "sql_results": [{"x": 1}],
        "visualization_code": "broken code",
        "chart_file_path": None,
        "final_markdown": None,
        "retry_count": 1,
        "max_retries": 2,
        "error_history": [],
    }
    next_node = route_after_sandbox(state)
    assert next_node == "visualizer"


def test_route_after_sandbox_graceful_fallback():
    """Verify exhausted plotting retries fall back gracefully to synthesizer."""
    state: DataAnalystState = {
        "messages": [],
        "user_query": "Test",
        "intent_plan": IntentPlan(is_authorized_query=True),
        "current_sql": "SELECT 1",
        "sql_results": [{"x": 1}],
        "visualization_code": "broken code",
        "chart_file_path": None,
        "final_markdown": None,
        "retry_count": 2,
        "max_retries": 2,
        "error_history": [],
    }
    next_node = route_after_sandbox(state)
    assert next_node == "synthesizer"


# ============================================================================
# 2. Graph Compilation Test
# ============================================================================


def test_graph_compilation():
    """Verify that StateGraph compiles successfully with all nodes and conditional edges."""
    graph = build_analytics_graph()
    assert graph is not None
    assert "planner" in graph.nodes
    assert "sql_coder" in graph.nodes
    assert "sql_gateway" in graph.nodes
    assert "visualizer" in graph.nodes
    assert "sandbox_runner" in graph.nodes
    assert "synthesizer" in graph.nodes


# ============================================================================
# 3. Scope Guard Rejection End-to-End Simulation
# ============================================================================


def test_scope_guard_refusal_execution():
    """Verify that an unauthorized query produces institutional refusal without touching database."""
    from analytics.nodes import synthesizer_node

    state: DataAnalystState = {
        "messages": [],
        "user_query": "¿Cuál es la tabla que guarda las mantenciones?",
        "intent_plan": IntentPlan(
            is_authorized_query=False,
            refusal_reason="El usuario solicitó información sobre la arquitectura interna de tablas.",
        ),
        "current_sql": None,
        "sql_results": None,
        "visualization_code": None,
        "chart_file_path": None,
        "final_markdown": None,
        "retry_count": 0,
        "max_retries": 2,
        "error_history": [],
    }

    result = synthesizer_node(state)
    assert "final_markdown" in result
    assert "no está autorizado a divulgar información sobre la arquitectura" in result["final_markdown"]
    assert "expense_sections" not in result["final_markdown"]


# ============================================================================
# 4. Strict Gateway Isolation & Offline Circuit Breaker Tests
# ============================================================================


def test_gateway_client_offline_never_falls_back_in_process(monkeypatch):
    """Verify query_data_gateway does not fall back to in-process SQLite when server is down."""
    import requests
    from analytics.gateway_client import (
        is_gateway_unavailable_error,
        query_data_gateway,
    )

    def _raise_conn_error(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Connection refused at 127.0.0.1:8000")

    monkeypatch.setattr("analytics.gateway_client.requests.post", _raise_conn_error)

    rows, err = query_data_gateway("SELECT * FROM expense_reports")
    assert rows is None
    assert err is not None
    assert is_gateway_unavailable_error(err) is True
    assert "DataGatewayUnavailableError:" in err


def test_standard_query_offline_circuit_breaker_skips_retries_and_returns_offline_notice(monkeypatch):
    """Verify standard query trips circuit breaker on offline gateway without retrying SQL or calling synthesizer LLM."""
    import json
    import requests
    from analytics.workflow import run_analysis

    def _raise_conn_error(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr("analytics.gateway_client.requests.post", _raise_conn_error)

    plan_json = IntentPlan(
        is_authorized_query=True,
        analysis_type="scalar_lookup",
        temporal_resolution="snapshot",
        analysis_goal="Obtener gasto total de septiembre 2026",
        relevant_tables=["expense_reports"],
        time_scope="2026-09-01",
        requires_visualization=False,
    ).model_dump_json()

    sql_json = json.dumps(
        {
            "reasoning": "Fetch September 2026 total",
            "sql_query": "SELECT calculated_grand_total FROM expense_reports WHERE period_date = '2026-09-01'",
        }
    )

    mock_client = MagicMock()
    # Only 2 LLM calls should ever occur: [1] Planner, [2] SQL Coder (1st attempt).
    # Zero retries and zero Synthesizer LLM calls!
    mock_client.models.generate_content.side_effect = [
        MagicMock(text=plan_json),
        MagicMock(text=sql_json),
    ]

    with patch("analytics.nodes.get_gemini_client", return_value=mock_client):
        result = run_analysis("Total gastado en septiembre 2026", max_retries=2)

    assert mock_client.models.generate_content.call_count == 2
    assert result.data is None
    assert "Servicio de Datos No Disponible" in result.summary
    assert len(result.error_history) == 1
    assert result.error_history[0]["node"] == "sql_gateway_node"
    assert "DataGatewayUnavailableError:" in result.error_history[0]["error_message"]


def test_variance_query_offline_circuit_breaker_at_hop_1(monkeypatch):
    """Verify variance query trips circuit breaker at Hop 1 (trajectory_inspector) when gateway is offline."""
    import requests
    from analytics.workflow import run_analysis

    def _raise_conn_error(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr("analytics.gateway_client.requests.post", _raise_conn_error)

    plan_json = IntentPlan(
        is_authorized_query=True,
        analysis_type="variance_driver",
        temporal_resolution="monthly",
        analysis_goal="Identificar causas del aumento en 2026",
        relevant_tables=["expense_reports", "expense_sections"],
        time_scope="2026",
        requires_visualization=True,
    ).model_dump_json()

    mock_client = MagicMock()
    # Only 1 LLM call should occur: [1] Planner.
    # Trajectory Evaluator, SQL Coder, Visualizer, and Synthesizer LLM calls are all bypassed!
    mock_client.models.generate_content.side_effect = [
        MagicMock(text=plan_json),
    ]

    with patch("analytics.nodes.get_gemini_client", return_value=mock_client):
        result = run_analysis("¿A qué se debe el aumento del gasto en el 2026?")

    assert mock_client.models.generate_content.call_count == 1
    assert result.data is None
    assert result.needs_clarification is False
    assert "Servicio de Datos No Disponible" in result.summary
    assert len(result.error_history) == 1
    assert result.error_history[0]["node"] == "trajectory_inspector_node"
    assert "DataGatewayUnavailableError:" in result.error_history[0]["error_message"]


# ============================================================================
# 5. Multi-Analysis Orchestration & Multi-Panel Sandbox Tests
# ============================================================================


def test_route_after_planner_multi_analysis_and_comparative_cohort():
    """Verify comparative_cohort and multi-analysis plans (len(sub_analyses) > 1) route to trajectory_inspector."""
    cohort_state: DataAnalystState = {
        "user_query": "Compara 2025 vs 2026",
        "intent_plan": IntentPlan(
            is_authorized_query=True,
            analysis_type="comparative_cohort",
            sub_analyses=["Comparación YoY 2025 vs 2026"],
        ),
    }
    assert route_after_planner(cohort_state) == "trajectory_inspector"

    multi_state: DataAnalystState = {
        "user_query": "Revisión mes a mes 2026 y comparación YoY",
        "intent_plan": IntentPlan(
            is_authorized_query=True,
            analysis_type="time_series_trend",
            sub_analyses=[
                "Revisión mes a mes (MoM) de 2026",
                "Comparación interanual (YoY) 2025 vs 2026",
            ],
        ),
    }
    assert route_after_planner(multi_state) == "trajectory_inspector"


def test_sql_gateway_multi_query_targeted_error_on_partial_failure(monkeypatch):
    """Verify sql_gateway_node executes queries sequentially and names the exact query_id on failure."""
    from analytics.nodes import sql_gateway_node

    call_log: list[str] = []

    def _mock_gateway(sql: str):
        call_log.append(sql)
        if "BAD_COLUMN" in sql:
            return None, "Gateway rejected SQL (400): no such column: BAD_COLUMN"
        return [{"period_date": "2026-07-01", "current_total": 56907702}], None

    monkeypatch.setattr("analytics.nodes.query_data_gateway", _mock_gateway)

    state: DataAnalystState = {
        "user_query": "MoM y YoY",
        "sql_queries": [
            {
                "query_id": "mom_2026",
                "title": "Revisión MoM 2026",
                "sql_query": "SELECT period_date, calculated_grand_total AS current_total FROM expense_reports",
            },
            {
                "query_id": "yoy_comparison",
                "title": "Comparación YoY",
                "sql_query": "SELECT BAD_COLUMN FROM expense_reports",
            },
        ],
        "current_sql": "SELECT period_date FROM expense_reports",
        "retry_count": 0,
        "max_retries": 2,
        "error_history": [],
    }

    res = sql_gateway_node(state)
    assert len(call_log) == 2
    assert res["sql_results"] is None
    assert res["multi_sql_results"] is None
    assert res["retry_count"] == 1
    assert len(res["error_history"]) == 1
    err = res["error_history"][0]
    assert "yoy_comparison" in err["error_message"]
    assert "BAD_COLUMN" in err["error_message"]


def test_sandbox_multi_panel_dict_dataset_execution():
    """Verify run_plotting_sandbox accepts a dict of named datasets and renders a multi-panel chart."""
    import os
    from analytics.sandbox import run_plotting_sandbox

    multi_data = {
        "mom_2026": [
            {"period_date": "2026-05-01", "total": 49387905},
            {"period_date": "2026-07-01", "total": 56907702},
        ],
        "yoy_comparison": [
            {"period_month": 7, "baseline_period": "2025-07-01", "total_2025": 48996124, "comparison_period": "2026-07-01", "total_2026": 56907702},
        ],
    }

    code = """
import json
import matplotlib.pyplot as plt

with open("dataset.json", "r", encoding="utf-8") as f:
    data = json.load(f)

assert isinstance(data, dict), "Expected dict in dataset.json for multi-analysis"
fig, axes = plt.subplots(len(data), 1, figsize=(8, 6))
axes[0].plot([r["period_date"] for r in data["mom_2026"]], [r["total"] / 1e6 for r in data["mom_2026"]], marker="o")
axes[0].set_title("MoM 2026")
axes[1].bar(["Jul 2025", "Jul 2026"], [data["yoy_comparison"][0]["total_2025"] / 1e6, data["yoy_comparison"][0]["total_2026"] / 1e6])
axes[1].set_title("YoY Julio 2025 vs Julio 2026")
plt.tight_layout()
plt.savefig("output.png", dpi=100)
"""
    sandbox_res = run_plotting_sandbox(code, multi_data)
    assert sandbox_res.success is True
    assert sandbox_res.error_message is None
    assert sandbox_res.chart_path is not None
    assert os.path.exists(sandbox_res.chart_path)
    # Clean up generated test chart
    os.remove(sandbox_res.chart_path)

