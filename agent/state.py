"""State schemas and Pydantic contracts for the Conversational Supervisor Agent."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, model_validator

UserRole = Literal["admin", "resident"]

KnowledgeDomain = Literal["building", "unit", "cross_domain"]

SupervisorRoute = Literal[
    "lookup_unit_bill",
    "lookup_building_summary",
    "run_deep_financial_analysis",
    "check_system_status",
    "clarify_intent",
    "direct_response",
]


class UserContext(BaseModel):
    """Cryptographically verified session identity and tenant scope."""

    uid: str = Field(..., description="Unique user identifier (Firebase UID or dev ID)")
    email: str = Field(..., description="Authenticated user email")
    role: UserRole = Field(..., description="'admin' or 'resident'")
    unit_code: Optional[str] = Field(
        default=None,
        description="Assigned apartment code (e.g. 'B-504'). Mandatory when role is 'resident'.",
    )

    @model_validator(mode="after")
    def validate_resident_unit(self) -> UserContext:
        """Ensure resident users always have a normalized unit_code."""
        if self.unit_code:
            self.unit_code = self.unit_code.strip().upper()
        if self.role == "resident" and not self.unit_code:
            raise ValueError("Users with role='resident' MUST have an assigned unit_code.")
        return self


class FocusContext(BaseModel):
    """Sticky entity focus tracked across ephemeral multi-turn conversations."""

    last_period: Optional[str] = Field(
        default=None, description="Last queried ISO period 'YYYY-MM-01'"
    )
    last_unit: Optional[str] = Field(
        default=None, description="Last queried unit code (e.g. 'B-504')"
    )
    last_domain: Optional[Literal["unit", "building", "cross_domain", "system"]] = Field(
        default=None, description="Last active analytical domain"
    )
    last_route: Optional[SupervisorRoute] = Field(
        default=None, description="Last tool route selected"
    )
    active_analytics_thread_id: Optional[str] = Field(
        default=None,
        description="Paused analytics workflow thread_id awaiting user clarification",
    )
    pending_clarification_query: Optional[str] = Field(
        default=None,
        description="Original user query paused by Supervisor clarify_intent awaiting disambiguation",
    )
    pending_clarification_route: Optional[SupervisorRoute] = Field(
        default=None,
        description="Intended target tool route once pending Supervisor clarification is resolved",
    )


class SupervisorDecision(BaseModel):
    """Structured routing decision emitted by the Supervisor node."""

    route: SupervisorRoute = Field(
        description="Selected action route: 'lookup_unit_bill', 'lookup_building_summary', 'run_deep_financial_analysis', 'check_system_status', 'clarify_intent', or 'direct_response'."
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of why this route was selected and how follow-up context was resolved.",
    )
    resolved_query: str = Field(
        default="",
        description="Fully self-contained natural language query after resolving follow-up references (e.g. '¿Y el mes anterior?').",
    )
    knowledge_domain: Optional[KnowledgeDomain] = Field(
        default=None,
        description="Resolved domain scope: 'building', 'unit', or 'cross_domain'.",
    )
    unit_code: Optional[str] = Field(
        default=None,
        description="Requested apartment unit code (e.g. 'B-504') if specified in query or focus.",
    )
    period_date: Optional[str] = Field(
        default=None,
        description="Normalized period date 'YYYY-MM-01' if a specific month is requested.",
    )
    needs_clarification: bool = Field(
        default=False,
        description="True when route is 'clarify_intent' and user input is required before calling a tool.",
    )
    clarification_prompt: Optional[str] = Field(
        default=None,
        description="Question in Spanish explaining the ambiguity when route is 'clarify_intent'.",
    )
    candidate_options: list[str] = Field(
        default_factory=list,
        description="Clickable options in Spanish when route is 'clarify_intent' (empty if asking admin to type a unit code).",
    )
    resume_pending_thread: bool = Field(
        default=False,
        description="True if the user's message directly answers an active clarification prompt; False if the user switched topics.",
    )
    direct_reply: Optional[str] = Field(
        default=None,
        description="Friendly Spanish response when route is 'direct_response'.",
    )


class SupervisorTurnResult(BaseModel):
    """Output returned by the Supervisor to the Chainlit UI for a single user turn."""

    reply: str = Field(description="User-facing markdown response in Spanish")
    route: SupervisorRoute = Field(description="Executed route for this turn")
    step_name: Optional[str] = Field(
        default=None, description="Collapsible step label for Chainlit UI"
    )
    step_detail: Optional[str] = Field(
        default=None, description="SQL query or REST payload summary for cl.Step"
    )
    chart_file_path: Optional[str] = Field(
        default=None, description="Path to rendered chart PNG if generated"
    )
    needs_clarification: bool = Field(
        default=False, description="True if awaiting user clarification button click"
    )
    clarification_prompt: Optional[str] = Field(
        default=None, description="Clarification prompt to display"
    )
    candidate_options: list[str] = Field(
        default_factory=list, description="Clickable options for cl.Action buttons"
    )
    thread_id: Optional[str] = Field(
        default=None, description="Analytics thread_id when paused for clarification"
    )
    focus: FocusContext = Field(
        default_factory=FocusContext, description="Updated ephemeral focus state"
    )


class SupervisorState(TypedDict, total=False):
    """LangGraph state for the Conversational Supervisor."""

    messages: Annotated[list[BaseMessage], add_messages]
    user_message: str
    clarification_choice: Optional[str]
    user_context: UserContext
    focus: FocusContext
    decision: Optional[SupervisorDecision]
    tool_output: Optional[dict[str, Any]]
    turn_result: Optional[SupervisorTurnResult]
