"""LangGraph workflow orchestrating document preparation, extraction, validation, and self-healing."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Literal

import requests
from langgraph.graph import StateGraph, START, END

from data_extractor.config import get_settings
from data_extractor.extractor import extract_from_pdf, extract_unit_from_pdf
from data_extractor.ground_truth import (
    GroundTruthData,
    UnitGroundTruthData,
    extract_ground_truth,
    extract_unit_ground_truth,
)
from data_extractor.models import (
    ExpenseReport,
    ExtractionMetadata,
    ExtractionState,
    UnitBillReport,
    UnitExtractionState,
    UnitValidationResult,
    ValidationResult,
)
from data_extractor.prompt_loader import (
    build_self_heal_prompt,
    build_unit_self_heal_prompt,
)
from data_extractor.validator import validate_expense_report, validate_unit_bill

logger = logging.getLogger("data_extractor.workflow")


def prepare_document_node(state: ExtractionState) -> dict:
    """Read PDF bytes and extract ground-truth declared totals."""
    pdf_path = Path(state["pdf_path"])
    pdf_bytes = state.get("pdf_bytes") or pdf_path.read_bytes()

    gt = extract_ground_truth(pdf_bytes)

    return {
        "pdf_bytes": pdf_bytes,
        "ground_truth_totals": gt.section_totals,
        "expected_operational_total": gt.operational_total,
        "expected_fondos_total": gt.fondos_total,
        "expected_grand_total": gt.grand_total,
        "expected_period_date": gt.period_date,
        "retry_count": 0,
        "is_valid": False,
        "errors": [],
    }


def extract_data_node(state: ExtractionState) -> dict:
    """Execute primary multimodal extraction via Gemini."""
    report, metadata = extract_from_pdf(pdf_source=state["pdf_bytes"])
    return {
        "report": report,
        "metadata": metadata,
    }


def validate_extraction_node(state: ExtractionState) -> dict:
    """Validate extracted items against ground truth figures."""
    report = state["report"]
    metadata = state.get("metadata")

    if report is None:
        if metadata:
            metadata.validation_status = "FAILED"
        return {
            "is_valid": False,
            "errors": ["Extraction report is None; extraction failed."],
            "metadata": metadata,
        }

    gt_data = GroundTruthData(
        period_date=state.get("expected_period_date"),
        operational_total=state.get("expected_operational_total"),
        fondos_total=state.get("expected_fondos_total"),
        grand_total=state.get("expected_grand_total"),
        section_totals=state.get("ground_truth_totals", {}),
    )

    validation_result = validate_expense_report(report, gt_data)
    if metadata:
        metadata.validation_status = "PASSED" if validation_result.is_valid else "FAILED"

    if not validation_result.is_valid:
        failed_sections = [
            sv.general_topic
            for sv in validation_result.section_validations
            if not sv.is_valid
        ]
        has_grand_total_mismatch = (
            validation_result.declared_grand_total is not None
            and validation_result.grand_total_difference != 0
        )
        logger.warning(
            "Validation discrepancy detected: retry=%d/%d, failing_sections=%s, grand_total_mismatch=%s",
            state.get("retry_count", 0),
            state.get("max_retries", 2),
            failed_sections,
            has_grand_total_mismatch,
        )

    return {
        "validation_result": validation_result,
        "is_valid": validation_result.is_valid,
        "errors": validation_result.errors,
        "metadata": metadata,
    }


def self_heal_node(state: ExtractionState) -> dict:
    """Query model with targeted error feedback to correct discrepancies."""
    current_retry = state.get("retry_count", 0) + 1
    vr = state.get("validation_result")
    failed_sections = (
        [sv.general_topic for sv in vr.section_validations if not sv.is_valid]
        if vr
        else []
    )
    logger.info(
        "Initiating self-healing iteration %d/%d for sections: %s",
        current_retry,
        state.get("max_retries", 2),
        failed_sections,
    )
    feedback = build_self_heal_prompt(state.get("errors", []))

    corrected_report, metadata = extract_from_pdf(
        pdf_source=state["pdf_bytes"],
        feedback_prompt=feedback,
    )
    metadata.retry_count = current_retry

    return {
        "report": corrected_report,
        "metadata": metadata,
        "retry_count": current_retry,
    }


def format_output_node(state: ExtractionState) -> dict:
    """Format final validated report and attach operational telemetry."""
    report = state.get("report")
    metadata = state.get("metadata")
    is_valid = state.get("is_valid", False)

    if report is None:
        final_dict = {
            "issue_date": "",
            "sections": [],
            "_status": "FAILED",
            "_errors": state.get("errors", []),
        }
    else:
        final_dict = {
            "issue_date": report.issue_date,
            "sections": [
                {
                    "general_topic": sec.general_topic,
                    "expense_type": sec.expense_type,
                    "sub_topics": [
                        {"name": it.name, "value": it.value}
                        for it in sec.sub_topics
                    ],
                }
                for sec in report.sections
            ],
        }
        if not is_valid:
            final_dict["_status"] = "FAILED_VALIDATION"
            final_dict["_errors"] = state.get("errors", [])

    if metadata is not None:
        final_dict["_metadata"] = metadata.model_dump()

    return {
        "final_output": final_dict,
    }


def route_after_validation(
    state: ExtractionState,
) -> Literal["self_heal", "format_output"]:
    """Conditional router based on validation status and retry limits."""
    if state.get("is_valid", False):
        return "format_output"

    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)

    if retry_count < max_retries:
        return "self_heal"

    # Max retries reached; proceed to format with warnings
    return "format_output"


def build_extraction_graph() -> StateGraph:
    """Assemble and compile the LangGraph extraction state machine."""
    workflow = StateGraph(ExtractionState)

    # 1. Add nodes
    workflow.add_node("prepare_document", prepare_document_node)
    workflow.add_node("extract_data", extract_data_node)
    workflow.add_node("validate_extraction", validate_extraction_node)
    workflow.add_node("self_heal", self_heal_node)
    workflow.add_node("format_output", format_output_node)

    # 2. Add edges
    workflow.add_edge(START, "prepare_document")
    workflow.add_edge("prepare_document", "extract_data")
    workflow.add_edge("extract_data", "validate_extraction")

    workflow.add_conditional_edges(
        "validate_extraction",
        route_after_validation,
        {
            "self_heal": "self_heal",
            "format_output": "format_output",
        },
    )

    workflow.add_edge("self_heal", "validate_extraction")
    workflow.add_edge("format_output", END)

    return workflow.compile(name="ExpenseReportExtractor")


def run_workflow(
    pdf_path: str | Path,
    output_path: str | Path | None = None,
    max_retries: int | None = None,
) -> dict:
    """Run end-to-end extraction workflow on a PDF and save clean JSON.

    Args:
        pdf_path: Path to the input PDF file.
        output_path: Optional destination path for output JSON.
        max_retries: Maximum self-healing attempts (defaults to settings).

    Returns:
        Clean JSON dictionary.
    """
    settings = get_settings()
    settings.ensure_directories()

    resolved_pdf = Path(pdf_path).resolve()
    if not resolved_pdf.exists():
        raise FileNotFoundError(f"Input PDF does not exist: {resolved_pdf}")

    retries = max_retries if max_retries is not None else settings.max_retries

    start_workflow_time = time.perf_counter()

    initial_state: ExtractionState = {
        "pdf_path": str(resolved_pdf),
        "pdf_bytes": resolved_pdf.read_bytes(),
        "ground_truth_totals": {},
        "expected_operational_total": None,
        "expected_fondos_total": None,
        "expected_grand_total": None,
        "expected_period_date": None,
        "report": None,
        "metadata": None,
        "validation_result": None,
        "errors": [],
        "retry_count": 0,
        "max_retries": retries,
        "is_valid": False,
        "final_output": None,
    }
    graph = build_extraction_graph()
    final_state = graph.invoke(
        initial_state,
        config={
            "run_name": f"Extract: {resolved_pdf.name}",
            "tags": ["pdf-extractor"],
            "metadata": {
                "file_name": resolved_pdf.name,
            },
        },
    )
    total_time = round(time.perf_counter() - start_workflow_time, 2)
    output_data = final_state.get("final_output", {})

    if "_metadata" in output_data:
        output_data["_metadata"]["total_duration_seconds"] = total_time

    # Ingest and route final output according to backend response
    dest, spool_status = load_and_spool_report(
        output_data=output_data,
        stem=resolved_pdf.stem,
        is_valid=final_state.get("is_valid", False),
        explicit_output_path=output_path,
    )

    output_data["_destination_path"] = str(dest)
    output_data["_spool_status"] = spool_status

    return output_data


def _cleanup_stale_spool_files(
    stem: str, active_dest: Path, doc_type: str = "general"
) -> None:
    """Remove any stale <stem>.json files from other output spool directories in the same domain."""
    settings = get_settings()
    for spool_dir in (
        settings.get_pending_dir(doc_type),
        settings.get_synced_dir(doc_type),
        settings.get_failed_dir(doc_type),
    ):
        candidate = spool_dir / f"{stem}.json"
        if candidate.resolve() != active_dest.resolve() and candidate.exists():
            try:
                candidate.unlink()
            except Exception as exc:
                logger.warning("Could not remove stale spool file %s: %s", candidate, exc)


def load_and_spool_report(
    output_data: dict,
    stem: str,
    is_valid: bool,
    explicit_output_path: str | Path | None = None,
    doc_type: Literal["general", "unit"] = "general",
) -> tuple[Path, str]:
    """Attempt direct backend load and determine final spool destination for general or unit domain.

    Returns:
        tuple[Path, str]: (destination_path, spool_status: 'synced' | 'pending' | 'failed' | 'explicit')
    """
    settings = get_settings()
    settings.ensure_directories()

    pending_dir = settings.get_pending_dir(doc_type)
    synced_dir = settings.get_synced_dir(doc_type)
    failed_dir = settings.get_failed_dir(doc_type)
    backend_url = (
        settings.backend_unit_bills_url
        if doc_type == "unit"
        else settings.backend_reports_url
    )

    # If caller requested an explicit path, save there directly
    if explicit_output_path is not None:
        dest = Path(explicit_output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        return dest, "explicit"

    # If extraction failed cross-validation, route to failed spool
    if not is_valid:
        dest = failed_dir / f"{stem}.json"
        output_data["_sync"] = {
            "status": "FAILED_VALIDATION",
            "errors": output_data.get("_errors", []),
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        _cleanup_stale_spool_files(stem, dest, doc_type=doc_type)
        return dest, "failed"

    # Attempt immediate backend ingestion via HTTP POST
    try:
        resp = requests.post(
            backend_url,
            json=output_data,
            timeout=5.0,
        )
        if resp.status_code in (200, 201):
            dest = synced_dir / f"{stem}.json"
            spool_status = "synced"
            body = resp.json()
            output_data["_sync"] = {
                "status": "SYNCED",
                "report_id": body.get("id"),
                "overwritten": body.get("overwritten", False),
            }
        else:
            dest = failed_dir / f"{stem}.json"
            spool_status = "failed"
            output_data["_sync"] = {
                "status": "FAILED",
                "status_code": resp.status_code,
                "error": resp.text,
            }
    except Exception as exc:
        dest = pending_dir / f"{stem}.json"
        spool_status = "pending"
        output_data["_sync"] = {
            "status": "PENDING",
            "error": str(exc),
        }

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    _cleanup_stale_spool_files(stem, dest, doc_type=doc_type)

    return dest, spool_status


# ============================================================================
# Unit Bill ("Nota de Cobro") Extraction LangGraph Workflow
# ============================================================================


def prepare_unit_document_node(state: UnitExtractionState) -> dict:
    """Read PDF bytes and extract ground-truth declared figures for a unit bill."""
    pdf_path = Path(state["pdf_path"])
    pdf_bytes = state.get("pdf_bytes") or pdf_path.read_bytes()

    gt = extract_unit_ground_truth(pdf_bytes)

    return {
        "pdf_bytes": pdf_bytes,
        "expected_period_date": gt.period_date,
        "expected_unit_code": gt.unit_code,
        "expected_alicuota_total": gt.alicuota_total,
        "expected_last_payment_date": gt.last_payment_date,
        "expected_last_payment_amount": gt.last_payment_amount,
        "declared_dept_subtotal": gt.declared_dept_subtotal,
        "declared_consumo_subtotal": gt.declared_consumo_subtotal,
        "declared_recargos_subtotal": gt.declared_recargos_subtotal,
        "declared_total_month": gt.declared_total_month,
        "retry_count": 0,
        "is_valid": False,
        "errors": [],
    }


def extract_unit_data_node(state: UnitExtractionState) -> dict:
    """Execute primary multimodal extraction for a unit bill via Gemini."""
    report, metadata = extract_unit_from_pdf(pdf_source=state["pdf_bytes"])
    return {
        "report": report,
        "metadata": metadata,
    }


def validate_unit_extraction_node(state: UnitExtractionState) -> dict:
    """Validate extracted unit bill items against ground truth figures."""
    report = state.get("report")
    metadata = state.get("metadata")

    if report is None:
        if metadata:
            metadata.validation_status = "FAILED"
        return {
            "is_valid": False,
            "errors": ["Unit bill report is None; extraction failed."],
            "metadata": metadata,
        }

    gt_data = UnitGroundTruthData(
        period_date=state.get("expected_period_date"),
        unit_code=state.get("expected_unit_code"),
        alicuota_total=state.get("expected_alicuota_total"),
        last_payment_date=state.get("expected_last_payment_date"),
        last_payment_amount=state.get("expected_last_payment_amount"),
        declared_dept_subtotal=state.get("declared_dept_subtotal"),
        declared_consumo_subtotal=state.get("declared_consumo_subtotal"),
        declared_recargos_subtotal=state.get("declared_recargos_subtotal"),
        declared_total_month=state.get("declared_total_month"),
    )

    validation_result = validate_unit_bill(report, gt_data)
    if metadata:
        metadata.validation_status = "PASSED" if validation_result.is_valid else "FAILED"

    if not validation_result.is_valid:
        logger.warning(
            "Unit bill validation discrepancy detected: retry=%d/%d, error_count=%d",
            state.get("retry_count", 0),
            state.get("max_retries", 2),
            len(validation_result.errors),
        )

    return {
        "validation_result": validation_result,
        "is_valid": validation_result.is_valid,
        "errors": validation_result.errors,
        "metadata": metadata,
    }


def self_heal_unit_node(state: UnitExtractionState) -> dict:
    """Query model with targeted error feedback to correct unit bill discrepancies."""
    current_retry = state.get("retry_count", 0) + 1
    logger.info(
        "Initiating unit bill self-healing iteration %d/%d",
        current_retry,
        state.get("max_retries", 2),
    )
    feedback = build_unit_self_heal_prompt(state.get("errors", []))

    corrected_report, metadata = extract_unit_from_pdf(
        pdf_source=state["pdf_bytes"],
        feedback_prompt=feedback,
    )
    metadata.retry_count = current_retry

    return {
        "report": corrected_report,
        "metadata": metadata,
        "retry_count": current_retry,
    }


def format_unit_output_node(state: UnitExtractionState) -> dict:
    """Format final validated unit bill report and attach operational telemetry."""
    report = state.get("report")
    metadata = state.get("metadata")
    is_valid = state.get("is_valid", False)

    if report is None:
        final_dict: dict = {
            "issue_date": "",
            "unit_code": "",
            "alicuota_total": 0.0,
            "last_payment_date": None,
            "last_payment_amount": None,
            "concepts": [],
            "consumptions": [],
            "unit_charges": [],
            "_status": "FAILED",
            "_errors": state.get("errors", []),
        }
    else:
        final_dict = {
            "issue_date": report.issue_date,
            "unit_code": report.unit_code,
            "alicuota_total": report.alicuota_total,
            "last_payment_date": report.last_payment_date,
            "last_payment_amount": report.last_payment_amount,
            "concepts": [
                {
                    "concept": c.concept,
                    "detail": c.detail,
                    "amount": c.amount,
                }
                for c in report.concepts
            ],
            "consumptions": [
                {
                    "item_name": c.item_name,
                    "previous_reading": c.previous_reading,
                    "current_reading": c.current_reading,
                    "consumption": c.consumption,
                    "unit_rate": c.unit_rate,
                    "total": c.total,
                }
                for c in report.consumptions
            ],
            "unit_charges": [
                {
                    "charge_name": c.charge_name,
                    "charge_type": c.charge_type,
                    "amount": c.amount,
                }
                for c in report.unit_charges
            ],
        }
        if not is_valid:
            final_dict["_status"] = "FAILED_VALIDATION"
            final_dict["_errors"] = state.get("errors", [])

    if metadata is not None:
        final_dict["_metadata"] = metadata.model_dump()

    return {
        "final_output": final_dict,
    }


def route_after_unit_validation(
    state: UnitExtractionState,
) -> Literal["self_heal", "format_output"]:
    """Conditional router for unit bill validation status and retry limits."""
    if state.get("is_valid", False):
        return "format_output"

    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)

    if retry_count < max_retries:
        return "self_heal"

    return "format_output"


def build_unit_extraction_graph() -> StateGraph:
    """Assemble and compile the LangGraph unit bill extraction state machine."""
    workflow = StateGraph(UnitExtractionState)

    workflow.add_node("prepare_document", prepare_unit_document_node)
    workflow.add_node("extract_data", extract_unit_data_node)
    workflow.add_node("validate_extraction", validate_unit_extraction_node)
    workflow.add_node("self_heal", self_heal_unit_node)
    workflow.add_node("format_output", format_unit_output_node)

    workflow.add_edge(START, "prepare_document")
    workflow.add_edge("prepare_document", "extract_data")
    workflow.add_edge("extract_data", "validate_extraction")

    workflow.add_conditional_edges(
        "validate_extraction",
        route_after_unit_validation,
        {
            "self_heal": "self_heal",
            "format_output": "format_output",
        },
    )

    workflow.add_edge("self_heal", "validate_extraction")
    workflow.add_edge("format_output", END)

    return workflow.compile(name="UnitBillExtractor")


def run_unit_workflow(
    pdf_path: str | Path,
    output_path: str | Path | None = None,
    max_retries: int | None = None,
) -> dict:
    """Run end-to-end unit bill extraction workflow on a PDF and spool/load output."""
    settings = get_settings()
    settings.ensure_directories()

    resolved_pdf = Path(pdf_path).resolve()
    if not resolved_pdf.exists():
        raise FileNotFoundError(f"Input PDF does not exist: {resolved_pdf}")

    retries = max_retries if max_retries is not None else settings.max_retries
    start_workflow_time = time.perf_counter()

    initial_state: UnitExtractionState = {
        "pdf_path": str(resolved_pdf),
        "pdf_bytes": resolved_pdf.read_bytes(),
        "expected_period_date": None,
        "expected_unit_code": None,
        "expected_alicuota_total": None,
        "expected_last_payment_date": None,
        "expected_last_payment_amount": None,
        "declared_dept_subtotal": None,
        "declared_consumo_subtotal": None,
        "declared_recargos_subtotal": None,
        "declared_total_month": None,
        "report": None,
        "metadata": None,
        "validation_result": None,
        "errors": [],
        "retry_count": 0,
        "max_retries": retries,
        "is_valid": False,
        "final_output": None,
    }
    graph = build_unit_extraction_graph()
    final_state = graph.invoke(
        initial_state,
        config={
            "run_name": f"Extract Unit: {resolved_pdf.name}",
            "tags": ["unit-pdf-extractor"],
            "metadata": {
                "file_name": resolved_pdf.name,
            },
        },
    )
    total_time = round(time.perf_counter() - start_workflow_time, 2)
    output_data = final_state.get("final_output", {})

    if "_metadata" in output_data:
        output_data["_metadata"]["total_duration_seconds"] = total_time

    dest, spool_status = load_and_spool_report(
        output_data=output_data,
        stem=resolved_pdf.stem,
        is_valid=final_state.get("is_valid", False),
        explicit_output_path=output_path,
        doc_type="unit",
    )

    output_data["_destination_path"] = str(dest)
    output_data["_spool_status"] = spool_status

    return output_data

