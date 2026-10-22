"""API routes for ingesting, listing, retrieving, and deleting individual unit bills ('Nota de Cobro')."""

from __future__ import annotations

import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.repository import (
    delete_unit_bill,
    find_unit_bill_by_id,
    list_unit_bills,
    save_unit_bill,
)
from backend.app.schemas import (
    UnitBillChargeResponse,
    UnitBillConceptResponse,
    UnitBillConsumptionResponse,
    UnitBillDetailResponse,
    UnitBillIngestRequest,
    UnitBillIngestResponse,
    UnitBillSummaryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/unit-bills", tags=["unit-bills"])


@router.post(
    "",
    response_model=UnitBillIngestResponse,
    summary="Ingest or overwrite a unit billing statement",
    description="Accepts a validated unit bill JSON payload from data_extractor and persists it to SQLite. Atomically overwrites if (unit_code, period_date) already exists.",
)
def ingest_unit_bill(
    payload: UnitBillIngestRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> UnitBillIngestResponse:
    """Ingest a unit billing statement ('Nota de Cobro') into the SQLite database."""
    try:
        bill, overwritten = save_unit_bill(db, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    response.status_code = status.HTTP_200_OK if overwritten else status.HTTP_201_CREATED
    action_str = "overwritten" if overwritten else "created"

    return UnitBillIngestResponse(
        id=bill.id,
        unit_code=bill.unit_code,
        period_date=bill.period_date,
        status=action_str,
        overwritten=overwritten,
        calculated_dept_subtotal=bill.calculated_dept_subtotal,
        calculated_consumo_total=bill.calculated_consumo_total,
        calculated_unit_subtotal=bill.calculated_unit_subtotal,
        calculated_total_month=bill.calculated_total_month,
        concepts_count=len(bill.concepts),
        consumptions_count=len(bill.consumptions),
        unit_charges_count=len(bill.unit_charges),
        message=f"Unit bill for {bill.unit_code} ({bill.period_date}) {action_str} successfully.",
    )


@router.get(
    "",
    response_model=list[UnitBillSummaryResponse],
    summary="List stored unit billing statements",
    description="Returns stored unit bill summaries ordered by period_date descending, with optional unit_code filter.",
)
def get_unit_bills(
    unit_code: str | None = Query(default=None, description="Optional unit code filter (e.g. 'B-504' or '504B')"),
    tower: str | None = Query(default=None, description="Optional tower filter (e.g. 'B')"),
    unit_number: int | None = Query(default=None, description="Optional unit number filter (e.g. 504)"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[UnitBillSummaryResponse]:
    """List unit bill summaries."""
    bills = list_unit_bills(
        db,
        unit_code=unit_code,
        tower=tower,
        unit_number=unit_number,
        skip=skip,
        limit=limit,
    )
    return [UnitBillSummaryResponse.model_validate(b) for b in bills]


@router.get(
    "/{bill_id}",
    response_model=UnitBillDetailResponse,
    summary="Get full unit bill detail by ID",
)
def get_unit_bill_by_id(
    bill_id: str,
    db: Session = Depends(get_db),
) -> UnitBillDetailResponse:
    """Retrieve a single unit bill with all nested tables."""
    bill = find_unit_bill_by_id(db, bill_id)
    if not bill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unit bill with id '{bill_id}' not found.",
        )

    parsed_metadata = None
    if bill.telemetry_metadata:
        try:
            parsed_metadata = json.loads(bill.telemetry_metadata)
        except Exception:
            parsed_metadata = None

    return UnitBillDetailResponse(
        id=bill.id,
        unit_code=bill.unit_code,
        period_date=bill.period_date,
        period_year=bill.period_year,
        period_month=bill.period_month,
        alicuota_total=bill.alicuota_total,
        last_payment_date=bill.last_payment_date,
        last_payment_amount=bill.last_payment_amount,
        calculated_dept_subtotal=bill.calculated_dept_subtotal,
        calculated_consumo_total=bill.calculated_consumo_total,
        calculated_unit_subtotal=bill.calculated_unit_subtotal,
        calculated_total_month=bill.calculated_total_month,
        created_at=bill.created_at,
        updated_at=bill.updated_at,
        telemetry_metadata=parsed_metadata,
        concepts=[UnitBillConceptResponse.model_validate(c) for c in bill.concepts],
        consumptions=[
            UnitBillConsumptionResponse.model_validate(c) for c in bill.consumptions
        ],
        unit_charges=[
            UnitBillChargeResponse.model_validate(c) for c in bill.unit_charges
        ],
    )


@router.delete(
    "/{bill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a unit bill by ID",
)
def remove_unit_bill(
    bill_id: str,
    db: Session = Depends(get_db),
) -> Response:
    """Delete a unit bill and cascade delete its child records."""
    deleted = delete_unit_bill(db, bill_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unit bill with id '{bill_id}' not found.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
