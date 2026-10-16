"""LangGraph state machine assembling Planner, Trajectory Inspector/Evaluator, SQL Coder, Gateway, Visualizer, and Synthesizer."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Literal
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from langsmith import traceable

from analytics.config import get_analytics_settings
from analytics.nodes import (
    clarification_node,
    planner_node,
    sandbox_runner_node,
    sql_coder_node,
    sql_gateway_node,
    synthesizer_node,
    trajectory_evaluator_node,
    trajectory_inspector_node,
    visualizer_node,
)
from analytics.state import AnalyticsResult, DataAnalystState

logger = logging.getLogger("analytics.workflow")

# Shared in-memory checkpointer for stateful pause/resume across turns
_DEFAULT_CHECKPOINTER = MemorySaver()


# ============================================================================
# Conditional Edge Routers
# ============================================================================


def route_after_planner(
    state: DataAnalystState,
) -> Literal["synthesizer", "clarification", "trajectory_inspector", "sql_coder"]:
    """Route after Planner: refusal -> synthesizer, ambiguity -> clarification, variance -> trajectory_inspector, else -> sql_coder."""
    plan = state.get("intent_plan")
    if plan and not plan.is_authorized_query:
        logger.warning("Scope Guard triggered: refusing query '%s'", state.get("user_query"))
        return "synthesizer"

    if plan and plan.needs_clarification and not state.get("user_clarification_choice"):
        logger.info("Planner requested clarification for query '%s'", state.get("user_query"))
        return "clarification"

    if plan and plan.analysis_type == "variance_driver":
        logger.info("Routing variance_driver query to trajectory_inspector (Hop 1)")
        return "trajectory_inspector"

    return "sql_coder"


def route_after_evaluator(
    state: DataAnalystState,
) -> Literal["clarification", "sql_coder"]:
    """Route after Trajectory Evaluator: pause for clarification if needed, otherwise proceed to SQL Coder (Hop 2)."""
    eval_res = state.get("trajectory_evaluation")
    if eval_res and eval_res.needs_clarification and not state.get("user_clarification_choice"):
        logger.info("Trajectory evaluator triggered user clarification")
        return "clarification"
    return "sql_coder"


def route_after_clarification(
    state: DataAnalystState,
) -> Literal["planner", "trajectory_evaluator"]:
    """Route after user resumes from clarification interrupt."""
    if state.get("macro_trajectory") is not None:
        return "trajectory_evaluator"
    return "planner"


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


def build_analytics_graph(checkpointer: MemorySaver | None = None):
    """Assemble and compile the LangGraph analytics state machine."""
    workflow = StateGraph(DataAnalystState)

    # 1. Add nodes
    workflow.add_node("planner", planner_node)
    workflow.add_node("trajectory_inspector", trajectory_inspector_node)
    workflow.add_node("trajectory_evaluator", trajectory_evaluator_node)
    workflow.add_node("clarification", clarification_node)
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
            "synthesizer": "synthesizer",
            "clarification": "clarification",
            "trajectory_inspector": "trajectory_inspector",
            "sql_coder": "sql_coder",
        },
    )

    workflow.add_edge("trajectory_inspector", "trajectory_evaluator")

    workflow.add_conditional_edges(
        "trajectory_evaluator",
        route_after_evaluator,
        {
            "clarification": "clarification",
            "sql_coder": "sql_coder",
        },
    )

    workflow.add_conditional_edges(
        "clarification",
        route_after_clarification,
        {
            "planner": "planner",
            "trajectory_evaluator": "trajectory_evaluator",
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

    active_checkpointer = checkpointer if checkpointer is not None else _DEFAULT_CHECKPOINTER
    return workflow.compile(
        checkpointer=active_checkpointer,
        name="ExpenseAnalyticsGraph",
    )


# ============================================================================
# Primary Execution Interface
# ============================================================================


@traceable(name="run_analytics_query")
def run_analysis(
    query: str,
    chart_dir: Path | str | None = None,
    max_retries: int | None = None,
    thread_id: str | None = None,
    resume_choice: str | None = None,
) -> AnalyticsResult:
    """Execute end-to-end analytical question answering, supporting checkpointed clarification pause/resume.

    Args:
        query: User natural language prompt.
        chart_dir: Optional custom destination directory for chart PNGs.
        max_retries: Maximum self-healing retry attempts (default: 2).
        thread_id: Optional session thread identifier for stateful pause/resume.
        resume_choice: User's clarification choice when resuming a paused thread.

    Returns:
        AnalyticsResult with summary markdown, clarification payload (if paused), data, chart path, and telemetry.
    """
    settings = get_analytics_settings()
    settings.ensure_directories()

    retries = max_retries if max_retries is not None else settings.max_retries
    active_thread_id = thread_id or f"thread_{uuid.uuid4().hex[:12]}"
    start_time = time.perf_counter()

    graph = build_analytics_graph()
    run_config: dict[str, Any] = {
        "configurable": {"thread_id": active_thread_id},
        "run_name": f"Analytics: {query[:40]}",
        "tags": ["analytics-graph"],
    }

    if resume_choice is not None:
        final_state = graph.invoke(
            Command(resume=resume_choice),
            config=run_config,
        )
    else:
        initial_state: DataAnalystState = {
            "messages": [],
            "user_query": query,
            "intent_plan": None,
            "macro_trajectory": None,
            "trajectory_evaluation": None,
            "user_clarification_choice": None,
            "current_sql": None,
            "sql_results": None,
            "visualization_code": None,
            "chart_file_path": None,
            "final_markdown": None,
            "retry_count": 0,
            "max_retries": retries,
            "error_history": [],
        }
        final_state = graph.invoke(
            initial_state,
            config=run_config,
        )

    duration = round(time.perf_counter() - start_time, 2)

    # Check if graph execution paused via interrupt()
    interrupts = final_state.get("__interrupt__")
    if interrupts:
        intr_payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        prompt = intr_payload.get("clarification_prompt", "Se requiere aclaración.")
        options = intr_payload.get("candidate_options", [])
        return AnalyticsResult(
            summary=prompt,
            is_authorized=True,
            needs_clarification=True,
            clarification_prompt=prompt,
            candidate_options=options,
            thread_id=active_thread_id,
            sql_query=final_state.get("current_sql"),
            data=final_state.get("macro_trajectory"),
            chart_file_path=None,
            error_history=list(final_state.get("error_history", [])),
            latency_seconds=duration,
        )

    plan = final_state.get("intent_plan")
    is_authorized = plan.is_authorized_query if plan else True

    return AnalyticsResult(
        summary=final_state.get("final_markdown") or "No se pudo generar respuesta.",
        is_authorized=is_authorized,
        needs_clarification=False,
        clarification_prompt=None,
        candidate_options=[],
        thread_id=active_thread_id,
        sql_query=final_state.get("current_sql"),
        data=final_state.get("sql_results"),
        chart_file_path=final_state.get("chart_file_path"),
        error_history=list(final_state.get("error_history", [])),
        latency_seconds=duration,
    )
