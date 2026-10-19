"""Pydantic schemas for request validation and response serialization."""

from __future__ import annotations

import re
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ExpenseType = Literal["operational", "fondos"]


# ============================================================================
# Ingestion Request Schemas (Matching verbatim data_extractor JSON output)
# ============================================================================

class SubTopicRequest(BaseModel):
    """Line item within a category."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, description="Item or expense description")
    value: int = Field(
        ..., description="Amount in Chilean Pesos (CLP). May be negative for credit notes."
    )


class SectionRequest(BaseModel):
    """Categorical grouping of expense items."""

    model_config = ConfigDict(extra="ignore")

    general_topic: str = Field(..., min_length=1, description="Category name")
    expense_type: ExpenseType = Field(
        default="operational",
        description="Expense classification: 'operational' or 'fondos'",
    )
    sub_topics: list[SubTopicRequest] = Field(
        default_factory=list, description="List of items in this category"
    )

    @model_validator(mode="after")
    def enforce_expense_type(self) -> SectionRequest:
        """Deterministically set expense_type='fondos' when general_topic is 'Fondos'."""
        if self.general_topic.strip().lower() == "fondos":
            self.expense_type = "fondos"
        else:
            self.expense_type = "operational"
        return self


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
    operational_total: int = Field(default=0, description="Operational total in CLP")
    fondos_total: int = Field(default=0, description="Fondos total in CLP")
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
    expense_type: str = "operational"
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
    operational_total: int = 0
    fondos_total: int = 0
    calculated_grand_total: int
    created_at: str
    updated_at: str


class ExpenseReportDetailResponse(ExpenseReportSummaryResponse):
    """Full hierarchical report detail."""

    telemetry_metadata: dict[str, Any] | None = None
    sections: list[ExpenseSectionResponse] = Field(default_factory=list)


# ============================================================================
# Unit Billing Statement ("Nota de Cobro") Request & Response Schemas
# ============================================================================

UnitChargeType = Literal["recargo", "fixed_charge"]


class ConceptItemRequest(BaseModel):
    """Line item within Tabla Conceptos."""

    model_config = ConfigDict(extra="ignore")

    concept: str = Field(..., min_length=1, description="Concept name")
    detail: str | None = Field(default=None, description="Detail or percentage text")
    amount: int = Field(..., description="Payable amount in CLP")


class ConsumptionItemRequest(BaseModel):
    """Line item within Tabla Consumos Generales."""

    model_config = ConfigDict(extra="ignore")

    item_name: str = Field(..., min_length=1, description="Meter or tower name")
    previous_reading: float = Field(..., description="Previous meter reading")
    current_reading: float = Field(..., description="Current meter reading")
    consumption: float = Field(..., description="Metered consumption delta")
    unit_rate: float = Field(..., description="Unit price rate")
    total: int = Field(..., description="Consumption total in CLP")


class UnitChargeItemRequest(BaseModel):
    """Line item within Tabla Gastos por Unidad."""

    model_config = ConfigDict(extra="ignore")

    charge_name: str = Field(..., min_length=1, description="Unit charge or fine name")
    charge_type: UnitChargeType = Field(
        default="fixed_charge",
        description="'recargo' or 'fixed_charge'",
    )
    amount: int = Field(..., description="Charge amount in CLP")


class UnitBillIngestRequest(BaseModel):
    """Complete unit billing statement payload exported by data_extractor."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    issue_date: str = Field(
        ...,
        description="Billing period date formatted strictly as '01/MM/YYYY'",
    )
    unit_code: str = Field(
        ...,
        min_length=1,
        description="Unit identifier (e.g. 'B-504')",
    )
    alicuota_total: float = Field(
        ...,
        gt=0.0,
        description="Total prorate percentage (e.g. 0.16736)",
    )
    last_payment_date: str | None = Field(
        default=None,
        description="Last payment date formatted as 'DD/MM/YYYY'",
    )
    last_payment_amount: int | None = Field(
        default=None,
        description="Last payment amount in CLP",
    )
    concepts: list[ConceptItemRequest] = Field(
        default_factory=list,
        description="Rows from Tabla Conceptos",
    )
    consumptions: list[ConsumptionItemRequest] = Field(
        default_factory=list,
        description="Rows from Tabla Consumos Generales",
    )
    unit_charges: list[UnitChargeItemRequest] = Field(
        default_factory=list,
        description="Rows from Tabla Gastos por Unidad",
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

    @field_validator("unit_code")
    @classmethod
    def validate_unit_code(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if not cleaned:
            raise ValueError("unit_code must not be empty")
        return cleaned

    @field_validator("last_payment_date")
    @classmethod
    def validate_last_payment_date(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().replace("-", "/")
        if not cleaned:
            return None
        if not re.match(r"^\d{2}/\d{2}/\d{4}$", cleaned):
            raise ValueError(
                f"last_payment_date must be in 'DD/MM/YYYY' format, received: {v!r}"
            )
        return cleaned


class UnitBillIngestResponse(BaseModel):
    """Response returned upon successful unit bill ingestion or overwrite."""

    id: str = Field(description="UUIDv7 unit bill identifier")
    unit_code: str = Field(description="Normalized unit identifier (e.g. 'B-504')")
    period_date: str = Field(description="Normalized ISO date 'YYYY-MM-01'")
    status: str = Field(description="'created' or 'overwritten'")
    overwritten: bool = Field(description="True if an existing record was overwritten")
    calculated_dept_subtotal: int = Field(description="Subtotal Departamento in CLP")
    calculated_consumo_total: int = Field(description="Subtotal Consumo in CLP")
    calculated_unit_subtotal: int = Field(description="Gastos por Unidad subtotal in CLP")
    calculated_total_month: int = Field(description="Total del mes in CLP")
    concepts_count: int = Field(description="Number of concept rows stored")
    consumptions_count: int = Field(description="Number of consumption rows stored")
    unit_charges_count: int = Field(description="Number of unit charge rows stored")
    message: str = Field(description="Human-readable outcome description")


class UnitBillConceptResponse(BaseModel):
    """Read representation of a Tabla Conceptos row."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    concept_name: str
    normalized_name: str
    detail: str | None = None
    amount: int
    display_order: int


class UnitBillConsumptionResponse(BaseModel):
    """Read representation of a Tabla Consumos Generales row."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    item_name: str
    normalized_name: str
    previous_reading: float
    current_reading: float
    consumption: float
    unit_rate: float
    total_amount: int
    display_order: int


class UnitBillChargeResponse(BaseModel):
    """Read representation of a Tabla Gastos por Unidad row."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    charge_name: str
    normalized_name: str
    charge_type: str
    amount: int
    display_order: int


class UnitBillSummaryResponse(BaseModel):
    """Top-level unit bill summary for listing."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    unit_code: str
    period_date: str
    period_year: int
    period_month: int
    alicuota_total: float
    last_payment_date: str | None = None
    last_payment_amount: int | None = None
    calculated_dept_subtotal: int = 0
    calculated_consumo_total: int = 0
    calculated_unit_subtotal: int = 0
    calculated_total_month: int
    created_at: str
    updated_at: str


class UnitBillDetailResponse(UnitBillSummaryResponse):
    """Full hierarchical unit bill detail."""

    telemetry_metadata: dict[str, Any] | None = None
    concepts: list[UnitBillConceptResponse] = Field(default_factory=list)
    consumptions: list[UnitBillConsumptionResponse] = Field(default_factory=list)
    unit_charges: list[UnitBillChargeResponse] = Field(default_factory=list)

