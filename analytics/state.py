"""State schemas and data models for analytics workflow."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, Optional, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class ExecutionError(TypedDict):
    """Structured telemetry record of a node failure."""

    node: str
    error_message: str
    attempt: int


class IntentPlan(BaseModel):
    """Output contract for the Planner node with Scope Guarding."""

    is_authorized_query: bool = Field(
        description="False if the query probes database infrastructure, table names, schema metadata, or credentials."
    )
    refusal_reason: Optional[str] = Field(
        default=None,
        description="Reason for refusal if is_authorized_query is False.",
    )
    analysis_goal: str = Field(
        default="",
        description="Summary of the business question to solve (only populated if authorized).",
    )
    relevant_tables: list[str] = Field(
        default_factory=list,
        description="Tables required (e.g. ['expense_reports', 'expense_sections'])",
    )
    time_scope: str = Field(
        default="",
        description="Time horizon (e.g. 'all periods', '2026', '2025-06-01 to 2026-06-01')",
    )
    requires_visualization: bool = Field(
        default=False,
        description="True if a chart is needed or analytically beneficial.",
    )
    visualization_goal: Optional[str] = Field(
        default=None,
        description="What the chart should highlight (e.g., 'Monthly trend of maintenance costs')",
    )
    suggested_chart_type: Optional[Literal["line", "bar", "pie", "stacked_bar"]] = (
        Field(default=None)
    )


class DataAnalystState(TypedDict):
    """Full LangGraph state machine state definition."""

    # 1. CONVERSATION TIER (Append-only reducer; enables Phase 4 multi-turn integration)
    messages: Annotated[list[BaseMessage], add_messages]

    # 2. OPERATIONAL TIER (Overwritten per pass)
    user_query: str
    intent_plan: Optional[IntentPlan]
    current_sql: Optional[str]
    sql_results: Optional[list[dict[str, Any]]]
    visualization_code: Optional[str]
    chart_file_path: Optional[str]
    final_markdown: Optional[str]

    # 3. CONTROL & SAFETY TIER (Circuit breakers & retry routing)
    retry_count: int
    max_retries: int
    error_history: Annotated[list[ExecutionError], operator.add]


class AnalyticsResult(BaseModel):
    """Final user-facing result of an analytical query."""

    summary: str = Field(description="Final executive summary in markdown")
    is_authorized: bool = Field(
        default=True, description="False if query was refused by scope guard"
    )
    sql_query: Optional[str] = Field(
        default=None, description="Executed SQL query (internal telemetry)"
    )
    data: Optional[list[dict[str, Any]]] = Field(
        default=None, description="Fetched database records"
    )
    chart_file_path: Optional[str] = Field(
        default=None, description="Path to rendered chart PNG if generated"
    )
    error_history: list[dict[str, Any]] = Field(
        default_factory=list, description="List of retry errors if any occurred"
    )
    latency_seconds: float = Field(
        default=0.0, description="Total execution duration in seconds"
    )
