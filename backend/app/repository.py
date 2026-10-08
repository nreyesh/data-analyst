"""Repository layer for database operations, persistence, and duplicate overwrite."""

from __future__ import annotations

import json
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import ExpenseItemModel, ExpenseReportModel, ExpenseSectionModel
from backend.app.schemas import ReportIngestRequest
from backend.app.utils import normalize_item_name, parse_issue_date


def find_report_by_period(db: Session, period_date: str) -> ExpenseReportModel | None:
    """Find an existing expense report by its normalized period_date (YYYY-MM-01)."""
    stmt = (
        select(ExpenseReportModel)
        .where(ExpenseReportModel.period_date == period_date)
        .options(
            selectinload(ExpenseReportModel.sections).selectinload(
                ExpenseSectionModel.items
            )
        )
    )
    return db.execute(stmt).scalar_one_or_none()


def find_report_by_id(db: Session, report_id: str) -> ExpenseReportModel | None:
    """Find an expense report by its unique UUIDv7 identifier with loaded hierarchy."""
    stmt = (
        select(ExpenseReportModel)
        .where(ExpenseReportModel.id == report_id)
        .options(
            selectinload(ExpenseReportModel.sections).selectinload(
                ExpenseSectionModel.items
            )
        )
    )
    return db.execute(stmt).scalar_one_or_none()


def list_reports(
    db: Session, skip: int = 0, limit: int = 100
) -> list[ExpenseReportModel]:
    """List expense reports ordered by period_date descending."""
    stmt = (
        select(ExpenseReportModel)
        .order_by(ExpenseReportModel.period_date.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def save_report(
    db: Session, request: ReportIngestRequest
) -> tuple[ExpenseReportModel, bool]:
    """Persist an expense report, performing an atomic overwrite if period_date exists.

    Returns:
        tuple[ExpenseReportModel, bool]: (persisted_report, was_overwritten)
    """
    period_date, year, month = parse_issue_date(request.issue_date)

    # Check for existing duplicate by period_date
    existing_report = (
        db.query(ExpenseReportModel)
        .filter(ExpenseReportModel.period_date == period_date)
        .first()
    )

    overwritten = False
    if existing_report:
        overwritten = True
        # Cascade delete old report and children to ensure clean state
        db.delete(existing_report)
        db.flush()

    # Calculate deterministic subtotals and grand total in integer CLP
    grand_total = 0
    section_models: list[ExpenseSectionModel] = []

    for s_idx, sec_req in enumerate(request.sections):
        subtotal = sum(item.value for item in sec_req.sub_topics)
        grand_total += subtotal

        sec_model = ExpenseSectionModel(
            category_name=sec_req.general_topic.strip(),
            calculated_subtotal=subtotal,
            display_order=s_idx,
        )

        for i_idx, item_req in enumerate(sec_req.sub_topics):
            item_model = ExpenseItemModel(
                name=item_req.name.strip(),
                normalized_name=normalize_item_name(item_req.name),
                amount=item_req.value,
                display_order=i_idx,
            )
            sec_model.items.append(item_model)

        section_models.append(sec_model)

    # Prepare telemetry metadata JSON
    metadata_json = json.dumps(request.metadata) if request.metadata else None

    # Construct and persist the new report header
    report = ExpenseReportModel(
        period_date=period_date,
        period_year=year,
        period_month=month,
        calculated_grand_total=grand_total,
        telemetry_metadata=metadata_json,
        sections=section_models,
    )

    db.add(report)
    db.commit()
    db.refresh(report)

    return report, overwritten


def delete_report(db: Session, report_id: str) -> bool:
    """Delete a report by its UUID, cascading to sections and items."""
    report = db.query(ExpenseReportModel).filter(ExpenseReportModel.id == report_id).first()
    if not report:
        return False
    db.delete(report)
    db.commit()
    return True
