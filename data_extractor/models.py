"""Domain models and state types for expense report extraction."""

from __future__ import annotations

import re
from typing import Any, Literal, TypedDict
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


# ============================================================================
# Unit Billing Statement ("Nota de Cobro") Domain Models
# ============================================================================

UnitChargeType = Literal["recargo", "fixed_charge"]


class ConceptItem(BaseModel):
    """Line item from the 'Concepto / Detalle / Monto a pagar' table."""

    concept: str = Field(
        description="Clean concept name (e.g. 'Gasto Común', 'Fondo de Reserva 5%', 'Fondo Impermeabilizacion')."
    )
    detail: str | None = Field(
        default=None,
        description="Detail string or percentage (e.g. '0,16736 %', '5,00 %') or None if empty.",
    )
    amount: int = Field(
        description="Payable amount in integer Chilean Pesos (CLP), e.g. 93236."
    )


class ConsumptionItem(BaseModel):
    """Row from the 'Consumos Generales' metered consumption table."""

    item_name: str = Field(
        description="Name of the consumption meter or tower (e.g. 'Torre B')."
    )
    previous_reading: float = Field(
        description="Previous meter reading ('Lectura Anterior'), e.g. 100.581."
    )
    current_reading: float = Field(
        description="Current meter reading ('Lectura Actual'), e.g. 102.2823."
    )
    consumption: float = Field(
        description="Metered consumption delta ('Consumos'), e.g. 1.7013."
    )
    unit_rate: float = Field(
        description="Unit price rate ('Valor'), e.g. 8340.24."
    )
    total: int = Field(
        description="Total consumption charge in integer CLP ('Total'), e.g. 14189."
    )


class UnitChargeItem(BaseModel):
    """Row from the 'Gastos por Unidad' table (recargos/fines and fixed per-unit charges)."""

    charge_name: str = Field(
        description="Name of the unit charge, fine/infraction, or fixed fund (e.g. 'Fondo de Indemnizacion')."
    )
    charge_type: UnitChargeType = Field(
        default="fixed_charge",
        description="'recargo' for fines/surcharges appearing above 'Subtotal Recargos', or 'fixed_charge' for flat per-unit charges appearing below 'Subtotal Recargos' (e.g. 'Fondo de Indemnizacion').",
    )
    amount: int = Field(
        description="Monetary integer value in Chilean Pesos (CLP), e.g. 2500."
    )


class UnitBillReport(BaseModel):
    """Complete structured individual apartment billing statement ('Nota de Cobro')."""

    issue_date: str = Field(
        description="Billing period date normalized strictly to 01/MM/YYYY (e.g. 01/08/2026 for Agosto 2026)."
    )
    unit_code: str = Field(
        default="",
        description="Apartment unit identifier from the header (e.g. 'B-504').",
    )
    unit_tower: str = Field(
        default="B",
        description="Building tower identifier (e.g. 'B', 'A').",
    )
    unit_number: int = Field(
        default=0,
        description="Apartment number as an integer (e.g. 504, 506).",
    )
    alicuota_total: float = Field(
        description="Total prorate percentage ('Alícuota Total') as a float (e.g. 0.16736 for '0,16736 %')."
    )
    last_payment_date: str | None = Field(
        default=None,
        description="Date of last payment ('Fecha ultimo Pago') normalized strictly to DD/MM/YYYY (e.g. '31/08/2026'), or None if empty.",
    )
    last_payment_amount: int | None = Field(
        default=None,
        description="Amount of last payment ('Monto ultimo Pago') in integer CLP (e.g. 125204), or None if empty.",
    )
    concepts: list[ConceptItem] = Field(
        default_factory=list,
        description="Rows from the 'Concepto / Detalle / Monto a pagar' table (excluding 'Subtotal Departamento').",
    )
    consumptions: list[ConsumptionItem] = Field(
        default_factory=list,
        description="Rows from the 'Consumos Generales' table (excluding 'Subtotal Consumo').",
    )
    unit_charges: list[UnitChargeItem] = Field(
        default_factory=list,
        description="Non-zero rows from the 'Gastos por Unidad' table (excluding 'Subtotal Recargos' and 'Total del mes').",
    )

    @model_validator(mode="before")
    @classmethod
    def populate_unit_dimensions(cls, data: Any) -> Any:
        if isinstance(data, dict):
            code = str(data.get("unit_code") or "").strip().upper()
            tower = data.get("unit_tower")
            number = data.get("unit_number")
            if (not tower or not number) and code:
                m1 = re.match(r"^([A-Za-z]+)\s*[-]?\s*(\d+)$", code)
                m2 = re.match(r"^(\d+)\s*[-]?\s*([A-Za-z]+)$", code)
                if m1:
                    tower = tower or m1.group(1).upper()
                    number = number if number is not None else int(m1.group(2))
                elif m2:
                    tower = tower or m2.group(2).upper()
                    number = number if number is not None else int(m2.group(1))
            if tower and number and not code:
                code = f"{str(tower).upper().strip()}-{int(number)}"
            elif tower and number and code and "-" not in code:
                code = f"{str(tower).upper().strip()}-{int(number)}"
            if tower:
                data["unit_tower"] = str(tower).upper().strip()
            if number is not None:
                data["unit_number"] = int(number)
            if code:
                data["unit_code"] = code
        return data

    @field_validator("issue_date")
    @classmethod
    def validate_issue_date(cls, v: str) -> str:
        v_clean = v.strip()
        if not re.match(r"^01/\d{2}/\d{4}$", v_clean):
            raise ValueError(
                f"issue_date must be strictly in '01/MM/YYYY' format, received: {v!r}"
            )
        return v_clean

    @field_validator("unit_code")
    @classmethod
    def normalize_unit_code(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("unit_code must not be empty")
        return v_clean

    @field_validator("last_payment_date")
    @classmethod
    def validate_last_payment_date(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v_clean = v.strip().replace("-", "/")
        if not v_clean:
            return None
        if not re.match(r"^\d{2}/\d{2}/\d{4}$", v_clean):
            raise ValueError(
                f"last_payment_date must be in 'DD/MM/YYYY' format, received: {v!r}"
            )
        return v_clean


class UnitValidationResult(BaseModel):
    """Cross-validation outcome for an individual unit billing statement."""

    is_valid: bool
    calculated_dept_subtotal: int = 0
    declared_dept_subtotal: int | None = None
    dept_subtotal_difference: int = 0
    calculated_consumo_subtotal: int = 0
    declared_consumo_subtotal: int | None = None
    consumo_subtotal_difference: int = 0
    calculated_recargos_subtotal: int = 0
    declared_recargos_subtotal: int | None = None
    recargos_subtotal_difference: int = 0
    calculated_unit_subtotal: int = 0
    calculated_total_month: int = 0
    declared_total_month: int | None = None
    total_month_difference: int = 0
    errors: list[str] = Field(default_factory=list)


class UnitExtractionState(TypedDict):
    """State schema for the LangGraph unit bill self-healing workflow."""

    pdf_path: str
    pdf_bytes: bytes
    expected_period_date: str | None
    expected_unit_code: str | None
    expected_alicuota_total: float | None
    expected_last_payment_date: str | None
    expected_last_payment_amount: int | None
    declared_dept_subtotal: int | None
    declared_consumo_subtotal: int | None
    declared_recargos_subtotal: int | None
    declared_total_month: int | None
    report: UnitBillReport | None
    metadata: ExtractionMetadata | None
    validation_result: UnitValidationResult | None
    errors: list[str]
    retry_count: int
    max_retries: int
    is_valid: bool
    final_output: dict | None

