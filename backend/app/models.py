"""SQLAlchemy 2.0 ORM models for expense reports, sections, and items."""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base
from backend.app.utils import generate_uuid7


class ExpenseReportModel(Base):
    """Monthly expense report header."""

    __tablename__ = "expense_reports"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    period_date: Mapped[str] = mapped_column(
        String(10), unique=True, nullable=False, index=True, comment="YYYY-MM-01"
    )
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False)
    operational_total: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="Operational total (TOTAL GASTOS COMUNES) in CLP"
    )
    fondos_total: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="Reserve funds and surcharges total in CLP"
    )
    calculated_grand_total: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Final billed total (operational + fondos) in Chilean Pesos (CLP)"
    )
    telemetry_metadata: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="JSON string of extraction telemetry"
    )
    created_at: Mapped[str] = mapped_column(
        String(32),
        default=lambda: datetime.now(timezone.utc).isoformat(),
        nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        String(32),
        default=lambda: datetime.now(timezone.utc).isoformat(),
        onupdate=lambda: datetime.now(timezone.utc).isoformat(),
        nullable=False,
    )

    # Relationships
    sections: Mapped[list[ExpenseSectionModel]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ExpenseSectionModel.display_order",
    )

    __table_args__ = (
        Index("idx_reports_period", "period_year", "period_month"),
    )


class ExpenseSectionModel(Base):
    """Categorical expense section grouping line items."""

    __tablename__ = "expense_sections"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    report_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("expense_reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category_name: Mapped[str] = mapped_column(String, nullable=False)
    expense_type: Mapped[str] = mapped_column(
        String(32), default="operational", nullable=False, comment="'operational' or 'fondos'"
    )
    calculated_subtotal: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Subtotal in Chilean Pesos (CLP)"
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    report: Mapped[ExpenseReportModel] = relationship(back_populates="sections")
    items: Mapped[list[ExpenseItemModel]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="ExpenseItemModel.display_order",
    )

    __table_args__ = (
        UniqueConstraint("report_id", "category_name", name="uq_report_category"),
    )


class ExpenseItemModel(Base):
    """Individual line item or sub-topic within a section."""

    __tablename__ = "expense_items"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    section_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("expense_sections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String, nullable=False, index=True, comment="Lowercase trimmed name"
    )
    amount: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Amount in Chilean Pesos (CLP). May be negative for credit notes",
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    section: Mapped[ExpenseSectionModel] = relationship(back_populates="items")


# ============================================================================
# Unit Billing Statement ("Nota de Cobro") Relational Hierarchy
# ============================================================================


class UnitBillModel(Base):
    """Individual apartment monthly billing statement header ('Nota de Cobro')."""

    __tablename__ = "unit_bills"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    unit_code: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, comment="Unit code e.g. 'B-504'"
    )
    tower: Mapped[str] = mapped_column(
        String(16), nullable=False, default="B", comment="Tower identifier e.g. 'B'"
    )
    unit_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="Unit number e.g. 504"
    )
    period_date: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True, comment="YYYY-MM-01"
    )
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False)
    alicuota_total: Mapped[float] = mapped_column(
        Float, nullable=False, comment="Prorate percentage e.g. 0.16736"
    )
    last_payment_date: Mapped[str | None] = mapped_column(
        String(10), nullable=True, comment="ISO date YYYY-MM-DD of last payment"
    )
    last_payment_amount: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Last payment amount in CLP"
    )
    calculated_dept_subtotal: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="Subtotal Departamento in CLP"
    )
    calculated_consumo_total: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="Subtotal Consumo in CLP"
    )
    calculated_unit_subtotal: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="Sum of Gastos por Unidad in CLP"
    )
    calculated_total_month: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Total del mes in CLP"
    )
    telemetry_metadata: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="JSON string of extraction telemetry"
    )
    created_at: Mapped[str] = mapped_column(
        String(32),
        default=lambda: datetime.now(timezone.utc).isoformat(),
        nullable=False,
    )
    updated_at: Mapped[str] = mapped_column(
        String(32),
        default=lambda: datetime.now(timezone.utc).isoformat(),
        onupdate=lambda: datetime.now(timezone.utc).isoformat(),
        nullable=False,
    )

    # Relationships
    concepts: Mapped[list[UnitBillConceptModel]] = relationship(
        back_populates="unit_bill",
        cascade="all, delete-orphan",
        order_by="UnitBillConceptModel.display_order",
    )
    consumptions: Mapped[list[UnitBillConsumptionModel]] = relationship(
        back_populates="unit_bill",
        cascade="all, delete-orphan",
        order_by="UnitBillConsumptionModel.display_order",
    )
    unit_charges: Mapped[list[UnitBillChargeModel]] = relationship(
        back_populates="unit_bill",
        cascade="all, delete-orphan",
        order_by="UnitBillChargeModel.display_order",
    )

    __table_args__ = (
        UniqueConstraint("unit_code", "period_date", name="uq_unit_bill_period"),
        Index("idx_unit_bills_unit_period", "unit_code", "period_year", "period_month"),
        Index("idx_unit_bills_tower_unit", "tower", "unit_number"),
    )


class UnitBillConceptModel(Base):
    """Row in 'Tabla Conceptos' (Concepto, Detalle, Monto a pagar)."""

    __tablename__ = "unit_bill_concepts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    unit_bill_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("unit_bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    concept_name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String, nullable=False, index=True, comment="Lowercase accent-stripped name"
    )
    detail: Mapped[str | None] = mapped_column(
        String, nullable=True, comment="Detail or percentage string e.g. '0,16736 %'"
    )
    amount: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Payable amount in CLP"
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    unit_bill: Mapped[UnitBillModel] = relationship(back_populates="concepts")


class UnitBillConsumptionModel(Base):
    """Row in 'Tabla Consumos Generales' (metered consumption)."""

    __tablename__ = "unit_bill_consumptions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    unit_bill_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("unit_bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String, nullable=False, index=True, comment="Lowercase accent-stripped name"
    )
    previous_reading: Mapped[float] = mapped_column(Float, nullable=False)
    current_reading: Mapped[float] = mapped_column(Float, nullable=False)
    consumption: Mapped[float] = mapped_column(Float, nullable=False)
    unit_rate: Mapped[float] = mapped_column(Float, nullable=False)
    total_amount: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Consumption total in CLP"
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    unit_bill: Mapped[UnitBillModel] = relationship(back_populates="consumptions")


class UnitBillChargeModel(Base):
    """Row in 'Tabla Gastos por Unidad' (fines/recargos and fixed unit charges)."""

    __tablename__ = "unit_bill_charges"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid7
    )
    unit_bill_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("unit_bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    charge_name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String, nullable=False, index=True, comment="Lowercase accent-stripped name"
    )
    charge_type: Mapped[str] = mapped_column(
        String(32),
        default="fixed_charge",
        nullable=False,
        comment="'recargo' or 'fixed_charge'",
    )
    amount: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Charge amount in CLP"
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    unit_bill: Mapped[UnitBillModel] = relationship(back_populates="unit_charges")

