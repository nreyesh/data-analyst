"""Pydantic schemas for request validation and response serialization."""

from __future__ import annotations

import re
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ============================================================================
# Ingestion Request Schemas (Matching verbatim data_extractor JSON output)
# ============================================================================

class SubTopicRequest(BaseModel):
    """Line item within a category."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, description="Item or expense description")
    value: int = Field(..., ge=0, description="Amount in Chilean Pesos (CLP) >= 0")


class SectionRequest(BaseModel):
    """Categorical grouping of expense items."""

    model_config = ConfigDict(extra="ignore")

    general_topic: str = Field(..., min_length=1, description="Category name")
    sub_topics: list[SubTopicRequest] = Field(
        default_factory=list, description="List of items in this category"
    )


class ReportIngestRequest(BaseModel):
    """Complete report payload exported by data_extractor."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    issue_date: str = Field(
        ...,
        description="Report emission date formatted strictly as '01/MM/YYYY'",
    )
    sections: list[SectionRequest] = Field(
        ..., min_length=1, description="List of expense sections"
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        alias="_metadata",
        description="Optional telemetry metrics from extractor",
    )

    @field_validator("issue_date")
    @classmethod
    def validate_issue_date(cls, v: str) -> str:
        if not re.match(r"^01/\d{2}/\d{4}$", v.strip()):
            raise ValueError(
                f"issue_date must be strictly in '01/MM/YYYY' format, received: {v!r}"
            )
        return v.strip()


# ============================================================================
# Ingestion Response Schema
# ============================================================================

class ReportIngestResponse(BaseModel):
    """Response returned upon successful ingestion or overwrite."""

    id: str = Field(description="UUIDv7 report identifier")
    period_date: str = Field(description="Normalized ISO date 'YYYY-MM-01'")
    status: str = Field(description="'created' or 'overwritten'")
    overwritten: bool = Field(description="True if an existing record was overwritten")
    calculated_grand_total: int = Field(description="Grand total in CLP")
    sections_count: int = Field(description="Number of sections stored")
    items_count: int = Field(description="Total line items stored across sections")
    message: str = Field(description="Human-readable outcome description")


# ============================================================================
# Read / Query Response Schemas
# ============================================================================

class ExpenseItemResponse(BaseModel):
    """Line item read representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    normalized_name: str
    amount: int
    display_order: int


class ExpenseSectionResponse(BaseModel):
    """Section read representation with nested items."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    category_name: str
    calculated_subtotal: int
    display_order: int
    items: list[ExpenseItemResponse] = Field(default_factory=list)


class ExpenseReportSummaryResponse(BaseModel):
    """Top-level report summary for listing."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    period_date: str
    period_year: int
    period_month: int
    calculated_grand_total: int
    created_at: str
    updated_at: str


class ExpenseReportDetailResponse(ExpenseReportSummaryResponse):
    """Full hierarchical report detail."""

    telemetry_metadata: dict[str, Any] | None = None
    sections: list[ExpenseSectionResponse] = Field(default_factory=list)
