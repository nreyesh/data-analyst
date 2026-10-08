"""SQLAlchemy 2.0 ORM models for expense reports, sections, and items."""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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
    calculated_grand_total: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Total in Chilean Pesos (CLP)"
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
        CheckConstraint("amount >= 0", name="chk_amount_positive"),
        nullable=False,
        comment="Amount in Chilean Pesos (CLP)",
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    section: Mapped[ExpenseSectionModel] = relationship(back_populates="items")
