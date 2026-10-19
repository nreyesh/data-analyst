"""Domain models and state types for expense report extraction."""

from __future__ import annotations

import re
from typing import Literal, TypedDict
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ExpenseType = Literal["operational", "fondos"]


class SubTopic(BaseModel):
    """Lowest-level expense item within a section."""

    name: str = Field(
        description="Clean base name of the item or sub-topic, stripped of voucher/invoice parentheses."
    )
    value: int = Field(
        description="Monetary integer value in Chilean Pesos (CLP), e.g. 13314915. May be negative for credit notes.",
    )


class Section(BaseModel):
    """Categorical section containing individual sub-topic line items."""

    general_topic: str = Field(
        description="Category name (e.g. Consumos, Remuneraciones, Mantenciones y Servicios, Fondos)."
    )
    expense_type: ExpenseType = Field(
        default="operational",
        description="Expense classification: 'fondos' when general_topic is 'Fondos', otherwise 'operational'.",
    )
    sub_topics: list[SubTopic] = Field(
        default_factory=list,
        description="Individual items and their values belonging to this category.",
    )

    @model_validator(mode="after")
    def enforce_expense_type(self) -> Section:
        """Deterministically set expense_type='fondos' when general_topic is 'Fondos', otherwise 'operational'."""
        if self.general_topic.strip().lower() == "fondos":
            self.expense_type = "fondos"
        else:
            self.expense_type = "operational"
        return self


class ExpenseReport(BaseModel):
    """Complete structured expense report."""

    issue_date: str = Field(
        description="Expense billing period date normalized strictly to 01/MM/YYYY (e.g. 01/09/2026 for September 2026)."
    )
    sections: list[Section] = Field(
        default_factory=list,
        description="All expense sections and their line items.",
    )

    @field_validator("issue_date")
    @classmethod
    def validate_issue_date(cls, v: str) -> str:
        if not re.match(r"^01/\d{2}/\d{4}$", v):
            raise ValueError(
                f"issue_date must be strictly in '01/MM/YYYY' format, received: {v!r}"
            )
        return v

    @model_validator(mode="after")
    def consolidate_duplicate_sections(self) -> ExpenseReport:
        """Consolidate multiple sections sharing identical general_topic into a single unified section."""
        consolidated: dict[str, Section] = {}
        for sec in self.sections:
            topic = sec.general_topic.strip()
            if topic in consolidated:
                consolidated[topic].sub_topics.extend(sec.sub_topics)
            else:
                consolidated[topic] = Section(
                    general_topic=topic,
                    expense_type=sec.expense_type,
                    sub_topics=list(sec.sub_topics),
                )
        self.sections = list(consolidated.values())
        return self


class SectionValidation(BaseModel):
    """Validation report for a single section."""

    general_topic: str
    calculated_sum: int
    declared_subtotal: int | None
    difference: int
    is_valid: bool


class ValidationResult(BaseModel):
    """Overall cross-validation outcome for an expense report."""

    is_valid: bool
    calculated_operational_total: int = 0
    declared_operational_total: int | None = None
    operational_total_difference: int = 0
    calculated_fondos_total: int = 0
    declared_fondos_total: int | None = None
    fondos_total_difference: int = 0
    calculated_grand_total: int
    declared_grand_total: int | None
    grand_total_difference: int
    section_validations: list[SectionValidation]
    errors: list[str] = Field(default_factory=list)


class ExtractionMetadata(BaseModel):
    """Operational telemetry and execution metadata."""

    model_config = ConfigDict(protected_namespaces=())

    model_used: str = Field(description="Name of the model that performed extraction")
    fallback_triggered: bool = Field(
        default=False, description="Whether fallback model was activated"
    )
    fallback_reason: str | None = Field(
        default=None, description="Error reason from primary model if fallback was triggered"
    )
    latency_seconds: float = Field(
        default=0.0, description="Duration in seconds of the LLM extraction call"
    )
    total_duration_seconds: float = Field(
        default=0.0, description="Total workflow execution time in seconds"
    )
    validation_status: str = Field(
        default="PASSED", description="Validation outcome (PASSED, WARNING, FAILED)"
    )
    retry_count: int = Field(
        default=0, description="Number of self-healing retries performed"
    )
    timestamp: str = Field(
        description="ISO-8601 UTC timestamp of the extraction"
    )


class ExtractionState(TypedDict):
    """State schema for the LangGraph self-healing workflow."""

    pdf_path: str
    pdf_bytes: bytes
    ground_truth_totals: dict[str, int]
    expected_operational_total: int | None
    expected_fondos_total: int | None
    expected_grand_total: int | None
    expected_period_date: str | None
    report: ExpenseReport | None
    metadata: ExtractionMetadata | None
    validation_result: ValidationResult | None
    errors: list[str]
    retry_count: int
    max_retries: int
    is_valid: bool
    final_output: dict | None
