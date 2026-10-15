"""LangGraph state machine assembling Planner, SQL Coder, Gateway, Visualizer, and Synthesizer."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal
from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from analytics.config import get_analytics_settings
from analytics.nodes import (
    planner_node,
    sandbox_runner_node,
    sql_coder_node,
    sql_gateway_node,
    synthesizer_node,
    visualizer_node,
)
from analytics.state import AnalyticsResult, DataAnalystState

logger = logging.getLogger("analytics.workflow")


# ============================================================================
# Conditional Edge Routers
# ============================================================================


def route_after_planner(
    state: DataAnalystState,
) -> Literal["sql_coder", "synthesizer"]:
    """Route to SQL Coder if query is authorized, or bypass to Synthesizer on scope violation."""
    plan = state.get("intent_plan")
    if plan and not plan.is_authorized_query:
        logger.warning("Scope Guard triggered: refusing query '%s'", state.get("user_query"))
        return "synthesizer"
    return "sql_coder"


def route_after_sql_gateway(
    state: DataAnalystState,
) -> Literal["sql_coder", "visualizer", "synthesizer"]:
    """Evaluate gateway response: retry on SQL error, proceed to visualizer, or go to synthesizer."""
    results = state.get("sql_results")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)

    # 1. Query failed: retry loop
    if results is None:
        if retry_count < max_retries:
            logger.info("Retrying SQL generation (attempt %d/%d)", retry_count + 1, max_retries)
            return "sql_coder"
        logger.warning("SQL retries exhausted (%d/%d); proceeding to synthesizer", retry_count, max_retries)
        return "synthesizer"

    # 2. Query succeeded: check if charting requested
    plan = state.get("intent_plan")
    if plan and plan.requires_visualization and len(results) > 0:
        return "visualizer"

    return "synthesizer"


def route_after_sandbox(
    state: DataAnalystState,
) -> Literal["visualizer", "synthesizer"]:
    """Evaluate sandbox rendering: retry on plotting error or proceed to synthesizer."""
    chart_path = state.get("chart_file_path")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)

    # 1. Plotting failed: retry loop
    if not chart_path:
        if retry_count < max_retries:
            logger.info("Retrying visualization script generation (attempt %d/%d)", retry_count + 1, max_retries)
            return "visualizer"
        logger.warning("Visualization retries exhausted (%d/%d); gracefully falling back to text", retry_count, max_retries)
        return "synthesizer"

    # 2. Plotting succeeded
    return "synthesizer"


# ============================================================================
# Graph Assembly
# ============================================================================


def build_analytics_graph():
    """Assemble and compile the LangGraph analytics state machine."""
    workflow = StateGraph(DataAnalystState)

    # 1. Add nodes
    workflow.add_node("planner", planner_node)
    workflow.add_node("sql_coder", sql_coder_node)
    workflow.add_node("sql_gateway", sql_gateway_node)
    workflow.add_node("visualizer", visualizer_node)
    workflow.add_node("sandbox_runner", sandbox_runner_node)
    workflow.add_node("synthesizer", synthesizer_node)

    # 2. Add edges
    workflow.add_edge(START, "planner")

    workflow.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "sql_coder": "sql_coder",
            "synthesizer": "synthesizer",
        },
    )

    workflow.add_edge("sql_coder", "sql_gateway")

    workflow.add_conditional_edges(
        "sql_gateway",
        route_after_sql_gateway,
        {
            "sql_coder": "sql_coder",
            "visualizer": "visualizer",
            "synthesizer": "synthesizer",
        },
    )

    workflow.add_edge("visualizer", "sandbox_runner")

    workflow.add_conditional_edges(
        "sandbox_runner",
        route_after_sandbox,
        {
            "visualizer": "visualizer",
            "synthesizer": "synthesizer",
        },
    )

    workflow.add_edge("synthesizer", END)

    return workflow.compile(name="ExpenseAnalyticsGraph")


# ============================================================================
# Primary Execution Interface
# ============================================================================


@traceable(name="run_analytics_query")
def run_analysis(
    query: str,
    chart_dir: Path | str | None = None,
    max_retries: int | None = None,
) -> AnalyticsResult:
    """Execute end-to-end analytical question answering and optional charting.

    Args:
        query: User natural language prompt.
        chart_dir: Optional custom destination directory for chart PNGs.
        max_retries: Maximum self-healing retry attempts (default: 2).

    Returns:
        AnalyticsResult with summary markdown, data records, chart path, and telemetry.
    """
    settings = get_analytics_settings()
    settings.ensure_directories()

    retries = max_retries if max_retries is not None else settings.max_retries
    start_time = time.perf_counter()

    initial_state: DataAnalystState = {
        "messages": [],
        "user_query": query,
        "intent_plan": None,
        "current_sql": None,
        "sql_results": None,
        "visualization_code": None,
        "chart_file_path": None,
        "final_markdown": None,
        "retry_count": 0,
        "max_retries": retries,
        "error_history": [],
    }

    graph = build_analytics_graph()
    final_state = graph.invoke(
        initial_state,
        config={
            "run_name": f"Analytics: {query[:40]}",
            "tags": ["analytics-graph"],
        },
    )

    duration = round(time.perf_counter() - start_time, 2)
    plan = final_state.get("intent_plan")
    is_authorized = plan.is_authorized_query if plan else True

    return AnalyticsResult(
        summary=final_state.get("final_markdown") or "No se pudo generar respuesta.",
        is_authorized=is_authorized,
        sql_query=final_state.get("current_sql"),
        data=final_state.get("sql_results"),
        chart_file_path=final_state.get("chart_file_path"),
        error_history=list(final_state.get("error_history", [])),
        latency_seconds=duration,
    )
