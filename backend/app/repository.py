"""Repository layer for database operations, persistence, and duplicate overwrite."""

from __future__ import annotations

import json
import re
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import (
    ExpenseItemModel,
    ExpenseReportModel,
    ExpenseSectionModel,
    UnitBillChargeModel,
    UnitBillConceptModel,
    UnitBillConsumptionModel,
    UnitBillModel,
)
from backend.app.schemas import ReportIngestRequest, UnitBillIngestRequest
from backend.app.utils import (
    normalize_item_name,
    normalize_text,
    parse_full_date,
    parse_issue_date,
)


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

    # Calculate deterministic subtotals, operational_total, fondos_total, and grand total in integer CLP
    operational_total = 0
    fondos_total = 0
    section_models: list[ExpenseSectionModel] = []

    for s_idx, sec_req in enumerate(request.sections):
        subtotal = sum(item.value for item in sec_req.sub_topics)
        norm_cat = normalize_text(sec_req.general_topic)
        sec_type = "fondos" if norm_cat == "fondos" or sec_req.expense_type == "fondos" else "operational"

        if sec_type == "fondos":
            fondos_total += subtotal
        else:
            operational_total += subtotal

        sec_model = ExpenseSectionModel(
            category_name=norm_cat,
            expense_type=sec_type,
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

    grand_total = operational_total + fondos_total

    # Prepare telemetry metadata JSON
    metadata_json = json.dumps(request.metadata) if request.metadata else None

    # Construct and persist the new report header
    report = ExpenseReportModel(
        period_date=period_date,
        period_year=year,
        period_month=month,
        operational_total=operational_total,
        fondos_total=fondos_total,
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


# ============================================================================
# Unit Bill ("Nota de Cobro") Repository Operations
# ============================================================================


def find_unit_bill_by_unit_and_period(
    db: Session, unit_code: str, period_date: str
) -> UnitBillModel | None:
    """Find an existing unit bill by (unit_code, period_date) with loaded child tables."""
    norm_unit = unit_code.strip().upper()
    stmt = (
        select(UnitBillModel)
        .where(
            UnitBillModel.unit_code == norm_unit,
            UnitBillModel.period_date == period_date,
        )
        .options(
            selectinload(UnitBillModel.concepts),
            selectinload(UnitBillModel.consumptions),
            selectinload(UnitBillModel.unit_charges),
        )
    )
    return db.execute(stmt).scalar_one_or_none()


def find_unit_bill_by_id(db: Session, bill_id: str) -> UnitBillModel | None:
    """Find a unit bill by its UUIDv7 identifier with loaded child tables."""
    stmt = (
        select(UnitBillModel)
        .where(UnitBillModel.id == bill_id)
        .options(
            selectinload(UnitBillModel.concepts),
            selectinload(UnitBillModel.consumptions),
            selectinload(UnitBillModel.unit_charges),
        )
    )
    return db.execute(stmt).scalar_one_or_none()


def list_unit_bills(
    db: Session,
    unit_code: str | None = None,
    tower: str | None = None,
    unit_number: int | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[UnitBillModel]:
    """List unit bills ordered by period_date descending, optionally filtered by unit_code, tower, and/or unit_number."""
    stmt = select(UnitBillModel)
    if tower is not None and tower.strip():
        stmt = stmt.where(UnitBillModel.tower == tower.strip().upper())
    if unit_number is not None:
        stmt = stmt.where(UnitBillModel.unit_number == unit_number)
    if unit_code is not None and unit_code.strip():
        norm_code = unit_code.strip().upper()
        m_suffix = re.match(r"^(\d+)\s*[-]?\s*([A-Za-z]+)$", norm_code)
        if m_suffix:
            norm_code = f"{m_suffix.group(2)}-{m_suffix.group(1)}"
        stmt = stmt.where(UnitBillModel.unit_code == norm_code)
    stmt = (
        stmt.order_by(UnitBillModel.period_date.desc(), UnitBillModel.unit_code.asc())
        .offset(skip)
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def list_towers_for_unit_number(db: Session, unit_number: int) -> list[str]:
    """Return distinct tower identifiers that contain the given unit number."""
    stmt = (
        select(UnitBillModel.tower)
        .where(UnitBillModel.unit_number == unit_number)
        .distinct()
        .order_by(UnitBillModel.tower.asc())
    )
    return list(db.execute(stmt).scalars().all())


def save_unit_bill(
    db: Session, request: UnitBillIngestRequest
) -> tuple[UnitBillModel, bool]:
    """Persist an individual unit bill, performing an atomic overwrite if (unit_code, period_date) exists.

    Returns:
        tuple[UnitBillModel, bool]: (persisted_unit_bill, was_overwritten)
    """
    period_date, year, month = parse_issue_date(request.issue_date)
    norm_unit = request.unit_code.strip().upper()
    iso_last_payment_date = parse_full_date(request.last_payment_date)

    # Check for existing duplicate by (unit_code, period_date)
    existing_bill = (
        db.query(UnitBillModel)
        .filter(
            UnitBillModel.unit_code == norm_unit,
            UnitBillModel.period_date == period_date,
        )
        .first()
    )

    overwritten = False
    if existing_bill:
        overwritten = True
        db.delete(existing_bill)
        db.flush()

    # 1. Build Tabla Conceptos rows and deterministic dept subtotal
    concept_models: list[UnitBillConceptModel] = []
    dept_subtotal = 0
    for idx, c_req in enumerate(request.concepts):
        dept_subtotal += c_req.amount
        concept_models.append(
            UnitBillConceptModel(
                concept_name=c_req.concept.strip(),
                normalized_name=normalize_item_name(c_req.concept),
                detail=c_req.detail.strip() if c_req.detail and c_req.detail.strip() else None,
                amount=c_req.amount,
                display_order=idx,
            )
        )

    # 2. Build Tabla Consumos Generales rows and deterministic consumo subtotal
    consumption_models: list[UnitBillConsumptionModel] = []
    consumo_subtotal = 0
    for idx, cons_req in enumerate(request.consumptions):
        consumo_subtotal += cons_req.total
        consumption_models.append(
            UnitBillConsumptionModel(
                item_name=cons_req.item_name.strip(),
                normalized_name=normalize_item_name(cons_req.item_name),
                previous_reading=cons_req.previous_reading,
                current_reading=cons_req.current_reading,
                consumption=cons_req.consumption,
                unit_rate=cons_req.unit_rate,
                total_amount=cons_req.total,
                display_order=idx,
            )
        )

    # 3. Build Tabla Gastos por Unidad rows and deterministic unit subtotal
    charge_models: list[UnitBillChargeModel] = []
    unit_subtotal = 0
    for idx, chg_req in enumerate(request.unit_charges):
        unit_subtotal += chg_req.amount
        charge_models.append(
            UnitBillChargeModel(
                charge_name=chg_req.charge_name.strip(),
                normalized_name=normalize_item_name(chg_req.charge_name),
                charge_type=chg_req.charge_type,
                amount=chg_req.amount,
                display_order=idx,
            )
        )

    total_month = dept_subtotal + consumo_subtotal + unit_subtotal
    metadata_json = json.dumps(request.metadata) if request.metadata else None

    norm_tower = request.unit_tower.strip().upper() if request.unit_tower else None
    norm_number = request.unit_number
    if not norm_tower or norm_number is None:
        m1 = re.match(r"^([A-Za-z]+)\s*[-]?\s*(\d+)$", norm_unit)
        m2 = re.match(r"^(\d+)\s*[-]?\s*([A-Za-z]+)$", norm_unit)
        if m1:
            norm_tower = norm_tower or m1.group(1).upper()
            norm_number = norm_number if norm_number is not None else int(m1.group(2))
        elif m2:
            norm_tower = norm_tower or m2.group(2).upper()
            norm_number = norm_number if norm_number is not None else int(m2.group(1))
        else:
            norm_tower = norm_tower or "B"
            norm_number = norm_number if norm_number is not None else 0

    unit_bill = UnitBillModel(
        unit_code=norm_unit,
        tower=norm_tower,
        unit_number=norm_number,
        period_date=period_date,
        period_year=year,
        period_month=month,
        alicuota_total=request.alicuota_total,
        last_payment_date=iso_last_payment_date,
        last_payment_amount=request.last_payment_amount,
        calculated_dept_subtotal=dept_subtotal,
        calculated_consumo_total=consumo_subtotal,
        calculated_unit_subtotal=unit_subtotal,
        calculated_total_month=total_month,
        telemetry_metadata=metadata_json,
        concepts=concept_models,
        consumptions=consumption_models,
        unit_charges=charge_models,
    )

    db.add(unit_bill)
    db.commit()
    db.refresh(unit_bill)

    return unit_bill, overwritten


def delete_unit_bill(db: Session, bill_id: str) -> bool:
    """Delete a unit bill by its UUID, cascading to concepts, consumptions, and unit_charges."""
    bill = db.query(UnitBillModel).filter(UnitBillModel.id == bill_id).first()
    if not bill:
        return False
    db.delete(bill)
    db.commit()
    return True

