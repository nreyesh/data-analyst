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
