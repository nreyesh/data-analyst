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
from backend.app.repository import save_report
from backend.app.schemas import ReportIngestRequest

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
    description="Returns current file counts and filenames across pending, synced, and failed queues.",
)
def get_queue_status() -> QueueStatusResponse:
    """Inspect file counts in pending, synced, and failed directories."""
    settings = get_settings()
    settings.ensure_directories()

    pending_files = sorted([f.name for f in settings.pending_dir.glob("*.json")])
    synced_files = sorted([f.name for f in settings.synced_dir.glob("*.json")])
    failed_files = sorted([f.name for f in settings.failed_dir.glob("*.json")])

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
    summary="Reconcile all pending reports into database",
    description="Scans pending/ directory, commits valid reports to SQLite, moves successes to synced/, and moves rejections to failed/.",
)
def sync_pending_reports(db: Session = Depends(get_db)) -> SyncResultResponse:
    """Batch reconcile all files currently staged in pending queue."""
    settings = get_settings()
    settings.ensure_directories()

    pending_files = sorted(list(settings.pending_dir.glob("*.json")))

    synced_names: list[str] = []
    failed_infos: list[FailedFileInfo] = []

    for file_path in pending_files:
        filename = file_path.name
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            # Validate payload schema
            ingest_request = ReportIngestRequest.model_validate(raw_data)

            # Commit to SQLite
            save_report(db, ingest_request)

            # Atomic move to synced directory
            target_synced = settings.synced_dir / filename
            shutil.move(str(file_path), str(target_synced))
            synced_names.append(filename)
            logger.info(f"Reconciled and moved to synced: {filename}")

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Failed to reconcile {filename}: {error_msg}")
            # Move unprocessable file to failed dead-letter directory
            target_failed = settings.failed_dir / filename
            try:
                shutil.move(str(file_path), str(target_failed))
            except Exception as move_err:
                logger.error(f"Could not move {filename} to failed: {move_err}")

            failed_infos.append(FailedFileInfo(filename=filename, error=error_msg))

    msg = f"Reconciled {len(synced_names)} reports successfully. {len(failed_infos)} failed."
    return SyncResultResponse(
        status="completed",
        total_processed=len(pending_files),
        synced_count=len(synced_names),
        failed_count=len(failed_infos),
        synced_files=synced_names,
        failed_files=failed_infos,
        message=msg,
    )
