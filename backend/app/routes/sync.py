"""Router endpoints for batch queue reconciliation and spool status."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.repository import save_report, save_unit_bill
from backend.app.schemas import ReportIngestRequest, UnitBillIngestRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])


class QueueStatusResponse(BaseModel):
    """Telemetry report of file counts and names across spool queues."""

    pending: list[str] = Field(default_factory=list)
    synced: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    pending_count: int = 0
    synced_count: int = 0
    failed_count: int = 0


class FailedFileInfo(BaseModel):
    """Details on why a report file was routed to the failed queue."""

    filename: str
    error: str


class SyncResultResponse(BaseModel):
    """Outcome of batch pending queue reconciliation."""

    status: str = "completed"
    total_processed: int = 0
    synced_count: int = 0
    failed_count: int = 0
    synced_files: list[str] = Field(default_factory=list)
    failed_files: list[FailedFileInfo] = Field(default_factory=list)
    message: str = ""


@router.get(
    "/status",
    response_model=QueueStatusResponse,
    summary="Get spool queue status",
    description="Returns current file counts and filenames across pending, synced, and failed queues for both general and unit domains.",
)
def get_queue_status() -> QueueStatusResponse:
    """Inspect file counts in pending, synced, and failed directories across general and unit domains."""
    settings = get_settings()
    settings.ensure_directories()

    pending_files = sorted(
        [f.name for f in settings.pending_dir.glob("*.json")]
        + [f.name for f in settings.unit_pending_dir.glob("*.json")]
    )
    synced_files = sorted(
        [f.name for f in settings.synced_dir.glob("*.json")]
        + [f.name for f in settings.unit_synced_dir.glob("*.json")]
    )
    failed_files = sorted(
        [f.name for f in settings.failed_dir.glob("*.json")]
        + [f.name for f in settings.unit_failed_dir.glob("*.json")]
    )

    return QueueStatusResponse(
        pending=pending_files,
        synced=synced_files,
        failed=failed_files,
        pending_count=len(pending_files),
        synced_count=len(synced_files),
        failed_count=len(failed_files),
    )


@router.post(
    "/pending",
    response_model=SyncResultResponse,
    status_code=status.HTTP_200_OK,
    summary="Reconcile all pending general reports and unit bills into database",
    description="Scans general/pending/ and unit/pending/ directories, commits valid documents to SQLite, moves successes to synced/, and moves rejections to failed/.",
)
def sync_pending_reports(db: Session = Depends(get_db)) -> SyncResultResponse:
    """Batch reconcile all files currently staged in general and unit pending queues."""
    settings = get_settings()
    settings.ensure_directories()

    general_pending = sorted(list(settings.pending_dir.glob("*.json")))
    unit_pending = sorted(list(settings.unit_pending_dir.glob("*.json")))

    synced_names: list[str] = []
    failed_infos: list[FailedFileInfo] = []

    # 1. Reconcile general building reports
    for file_path in general_pending:
        filename = file_path.name
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            ingest_request = ReportIngestRequest.model_validate(raw_data)
            save_report(db, ingest_request)

            target_synced = settings.synced_dir / filename
            shutil.move(str(file_path), str(target_synced))
            synced_names.append(filename)
            logger.info(f"Reconciled general report and moved to synced: {filename}")

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Failed to reconcile general report {filename}: {error_msg}")
            target_failed = settings.failed_dir / filename
            try:
                shutil.move(str(file_path), str(target_failed))
            except Exception as move_err:
                logger.error(f"Could not move {filename} to failed: {move_err}")

            failed_infos.append(FailedFileInfo(filename=filename, error=error_msg))

    # 2. Reconcile individual unit bills
    for file_path in unit_pending:
        filename = file_path.name
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            unit_request = UnitBillIngestRequest.model_validate(raw_data)
            save_unit_bill(db, unit_request)

            target_synced = settings.unit_synced_dir / filename
            shutil.move(str(file_path), str(target_synced))
            synced_names.append(filename)
            logger.info(f"Reconciled unit bill and moved to synced: {filename}")

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Failed to reconcile unit bill {filename}: {error_msg}")
            target_failed = settings.unit_failed_dir / filename
            try:
                shutil.move(str(file_path), str(target_failed))
            except Exception as move_err:
                logger.error(f"Could not move {filename} to unit failed: {move_err}")

            failed_infos.append(FailedFileInfo(filename=filename, error=error_msg))

    total_count = len(general_pending) + len(unit_pending)
    msg = f"Reconciled {len(synced_names)} reports successfully. {len(failed_infos)} failed."
    return SyncResultResponse(
        status="completed",
        total_processed=total_count,
        synced_count=len(synced_names),
        failed_count=len(failed_infos),
        synced_files=synced_names,
        failed_files=failed_infos,
        message=msg,
    )
