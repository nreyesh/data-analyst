"""API router endpoints for expense reports ingestion, retrieval, and listing."""

from __future__ import annotations

import json
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import ExpenseReportModel
from backend.app.repository import (
    delete_report,
    find_report_by_id,
    find_report_by_period,
    list_reports,
    save_report,
)
from backend.app.schemas import (
    ExpenseReportDetailResponse,
    ExpenseReportSummaryResponse,
    ReportIngestRequest,
    ReportIngestResponse,
)

router = APIRouter(prefix="/reports", tags=["reports"])


def _format_detail_response(report: ExpenseReportModel) -> ExpenseReportDetailResponse:
    """Format ORM model with parsed telemetry metadata into response schema."""
    parsed_meta: dict[str, Any] | None = None
    if report.telemetry_metadata:
        try:
            parsed_meta = json.loads(report.telemetry_metadata)
        except Exception:
            parsed_meta = None

    return ExpenseReportDetailResponse(
        id=report.id,
        period_date=report.period_date,
        period_year=report.period_year,
        period_month=report.period_month,
        calculated_grand_total=report.calculated_grand_total,
        created_at=report.created_at,
        updated_at=report.updated_at,
        telemetry_metadata=parsed_meta,
        sections=[s for s in report.sections],
    )


@router.post(
    "",
    response_model=ReportIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest or overwrite an expense report",
    description="Directly ingests validated JSON from data_extractor. Overwrites existing record if period_date already exists.",
)
def ingest_expense_report(
    payload: ReportIngestRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> ReportIngestResponse:
    """Ingest expense report, check duplicate, overwrite if exists, and save to SQLite."""
    try:
        report, overwritten = save_report(db, payload)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to persist report: {str(e)}",
        )

    # Set response code to 200 OK if overwritten, or 201 Created if brand new
    if overwritten:
        response.status_code = status.HTTP_200_OK
        message = (
            f"Report for period {report.period_date} already existed and was successfully overwritten."
        )
        status_text = "overwritten"
    else:
        response.status_code = status.HTTP_201_CREATED
        message = f"Report for period {report.period_date} created successfully."
        status_text = "created"

    total_items = sum(len(section.items) for section in report.sections)

    return ReportIngestResponse(
        id=report.id,
        period_date=report.period_date,
        status=status_text,
        overwritten=overwritten,
        calculated_grand_total=report.calculated_grand_total,
        sections_count=len(report.sections),
        items_count=total_items,
        message=message,
    )


@router.get(
    "",
    response_model=list[ExpenseReportSummaryResponse],
    summary="List all stored expense reports",
    description="Returns high-level summary overview of all expense reports ordered by date descending.",
)
def get_expense_reports(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[ExpenseReportSummaryResponse]:
    """Retrieve list of expense reports."""
    reports = list_reports(db, skip=skip, limit=limit)
    return [ExpenseReportSummaryResponse.model_validate(r) for r in reports]


@router.get(
    "/{report_id}",
    response_model=ExpenseReportDetailResponse,
    summary="Get full expense report detail by ID",
    description="Returns full hierarchical report with all sections, line items, and metadata.",
)
def get_expense_report_by_id(
    report_id: str,
    db: Session = Depends(get_db),
) -> ExpenseReportDetailResponse:
    """Retrieve single expense report by its UUID."""
    report = find_report_by_id(db, report_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report with id '{report_id}' was not found.",
        )
    return _format_detail_response(report)


@router.get(
    "/period/{period_date}",
    response_model=ExpenseReportDetailResponse,
    summary="Get full expense report by period date",
    description="Lookup report by ISO period date 'YYYY-MM-01'.",
)
def get_expense_report_by_period(
    period_date: str,
    db: Session = Depends(get_db),
) -> ExpenseReportDetailResponse:
    """Retrieve single expense report by period date (e.g. 2026-10-01)."""
    report = find_report_by_period(db, period_date)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report for period '{period_date}' was not found.",
        )
    return _format_detail_response(report)


@router.delete(
    "/{report_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an expense report",
    description="Deletes report and all cascaded sections and items.",
)
def remove_expense_report(
    report_id: str,
    db: Session = Depends(get_db),
) -> Response:
    """Delete report by ID."""
    deleted = delete_report(db, report_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report with id '{report_id}' was not found.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
