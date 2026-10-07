"""LangGraph workflow orchestrating document preparation, extraction, validation, and self-healing."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from langgraph.graph import StateGraph, START, END

from data_extractor.config import get_settings
from data_extractor.extractor import extract_from_pdf
from data_extractor.ground_truth import GroundTruthData, extract_ground_truth
from data_extractor.models import ExpenseReport, ExtractionMetadata, ExtractionState, ValidationResult
from data_extractor.prompt_loader import build_self_heal_prompt
from data_extractor.validator import validate_expense_report


def prepare_document_node(state: ExtractionState) -> dict:
    """Read PDF bytes and extract ground-truth declared totals."""
    pdf_path = Path(state["pdf_path"])
    pdf_bytes = state.get("pdf_bytes") or pdf_path.read_bytes()

    gt = extract_ground_truth(pdf_bytes)

    return {
        "pdf_bytes": pdf_bytes,
        "ground_truth_totals": gt.section_totals,
        "expected_grand_total": gt.grand_total,
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
        grand_total=state.get("expected_grand_total"),
        section_totals=state.get("ground_truth_totals", {}),
    )

    validation_result = validate_expense_report(report, gt_data)
    if metadata:
        metadata.validation_status = "PASSED" if validation_result.is_valid else "FAILED"

    return {
        "validation_result": validation_result,
        "is_valid": validation_result.is_valid,
        "errors": validation_result.errors,
        "metadata": metadata,
    }


def self_heal_node(state: ExtractionState) -> dict:
    """Query model with targeted error feedback to correct discrepancies."""
    current_retry = state.get("retry_count", 0) + 1
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
    report = state["report"]
    metadata = state.get("metadata")

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
                    "sub_topics": [
                        {"name": it.name, "value": it.value}
                        for it in sec.sub_topics
                    ],
                }
                for sec in report.sections
            ],
        }

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

    return workflow.compile()


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
        "expected_grand_total": None,
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
    final_state = graph.invoke(initial_state)

    total_time = round(time.perf_counter() - start_workflow_time, 2)
    output_data = final_state.get("final_output", {})

    if "_metadata" in output_data:
        output_data["_metadata"]["total_duration_seconds"] = total_time

    # Determine save destination
    if output_path is not None:
        dest = Path(output_path)
    else:
        dest = settings.output_dir / f"{resolved_pdf.stem}.json"

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    return output_data
