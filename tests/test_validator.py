"""Unit tests for ground-truth parser, domain models, and cross-validation."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from data_extractor.ground_truth import extract_ground_truth
from data_extractor.models import ExpenseReport, Section, SubTopic
from data_extractor.validator import validate_expense_report


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "2026_9_expected.json"
PDF_PATH = Path(__file__).parent.parent / "examples" / "2026-9.pdf"


def test_ground_truth_extraction():
    """Verify that PyMuPDF deterministic ground truth parser extracts correct totals."""
    assert PDF_PATH.exists(), f"Sample PDF not found at {PDF_PATH}"
    gt = extract_ground_truth(PDF_PATH)

    assert gt.period_date == "01/09/2026"
    assert gt.grand_total == 56454297
    assert gt.section_totals["Remuneraciones"] == 19632258
    assert gt.section_totals["Consumos"] == 13669643  # includes Gas Cargo Comun
    assert gt.section_totals["Mantenciones y Servicios"] == 17071495
    assert gt.section_totals["Reparaciones y Repuestos"] == 1965195
    assert gt.section_totals["Seguros"] == 2243977
    assert gt.section_totals["Asesorías y Honorarios Profesionales"] == 24324
    assert gt.section_totals["Insumos"] == 1713550
    assert gt.section_totals["Otros Gastos"] == 133855

    # Sum of normalized categories must exactly equal grand total
    assert sum(gt.section_totals.values()) == gt.grand_total


def test_validator_with_perfect_fixture():
    """Verify that the perfect expected fixture passes full validation with zero errors."""
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    report = ExpenseReport.model_validate(data)
    gt = extract_ground_truth(PDF_PATH)

    result = validate_expense_report(report, gt)

    assert result.is_valid is True
    assert len(result.errors) == 0
    assert result.grand_total_difference == 0
    assert result.calculated_grand_total == 56454297


def test_validator_detects_section_discrepancy():
    """Verify that section sum mismatches produce actionable, surgical errors."""
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Tamper with an item in 'Remuneraciones'
    data["sections"][0]["sub_topics"][0]["value"] -= 100000

    report = ExpenseReport.model_validate(data)
    gt = extract_ground_truth(PDF_PATH)

    result = validate_expense_report(report, gt)

    assert result.is_valid is False
    assert len(result.errors) >= 1
    # Check that Remuneraciones was named in errors
    remun_errors = [e for e in result.errors if "Remuneraciones" in e]
    assert len(remun_errors) == 1
    assert "-100,000 CLP" in remun_errors[0]


def test_validator_detects_missing_section():
    """Verify that omission of an entire section is detected and reported."""
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Remove 'Otros Gastos'
    data["sections"] = [s for s in data["sections"] if s["general_topic"] != "Otros Gastos"]

    report = ExpenseReport.model_validate(data)
    gt = extract_ground_truth(PDF_PATH)

    result = validate_expense_report(report, gt)

    assert result.is_valid is False
    missing_errors = [e for e in result.errors if "Missing section 'Otros Gastos'" in e]
    assert len(missing_errors) == 1


def test_pydantic_model_invariants():
    """Verify date formatting and negative value invariants."""
    # Valid date
    valid_report = ExpenseReport(
        issue_date="01/10/2026",
        sections=[Section(general_topic="Consumos", sub_topics=[SubTopic(name="Agua", value=100)])],
    )
    assert valid_report.issue_date == "01/10/2026"

    # Invalid date pattern
    with pytest.raises(ValidationError):
        ExpenseReport(issue_date="13-10-2026", sections=[])

    with pytest.raises(ValidationError):
        ExpenseReport(issue_date="02/10/2026", sections=[])

    # Negative integer values are permitted for credit notes and adjustments
    credit_item = SubTopic(name="Finiquitos", value=-250690)
    assert credit_item.value == -250690


def test_negative_credit_note_validation_2026_7():
    """Verify that negative line items (e.g. Finiquitos $-250.690) validate cleanly against 2026-7.pdf."""
    pdf_path = Path(__file__).parent.parent / "examples" / "2026-7.pdf"
    json_path = Path(__file__).parent.parent / "data" / "output" / "2026-7.json"
    if not json_path.exists():
        json_path = Path(__file__).parent.parent / "data" / "output" / "synced" / "2026-7.json"
    assert pdf_path.exists(), f"Sample PDF not found: {pdf_path}"
    assert json_path.exists(), f"Output JSON not found: {json_path}"

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    report = ExpenseReport.model_validate(data)
    gt = extract_ground_truth(pdf_path)

    result = validate_expense_report(report, gt)
    assert result.is_valid is True
    assert len(result.errors) == 0

    # Ensure Finiquitos is present with negative value
    rem_section = next(s for s in report.sections if s.general_topic == "Remuneraciones")
    finiquitos_item = next(it for it in rem_section.sub_topics if it.name == "Finiquitos")
    assert finiquitos_item.value == -250690


def test_validator_detects_period_date_mismatch():
    """Verify that mismatch between extracted issue_date and ground truth period triggers an error."""
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Tamper with issue_date (e.g. LLM extracted October instead of September)
    data["issue_date"] = "01/10/2026"
    report = ExpenseReport.model_validate(data)
    gt = extract_ground_truth(PDF_PATH)

    result = validate_expense_report(report, gt)
    assert result.is_valid is False
    assert any("Billing period date mismatch" in e for e in result.errors)


@pytest.mark.parametrize(
    "pdf_name, expected_period",
    [
        ("2026-7.pdf", "01/07/2026"),
        ("2026-8.pdf", "01/08/2026"),
        ("2026-9.pdf", "01/09/2026"),
    ],
)
def test_ground_truth_period_across_samples(pdf_name: str, expected_period: str):
    """Verify that PyMuPDF deterministic ground truth extracts correct period across sample PDFs."""
    path = Path(__file__).parent.parent / "examples" / pdf_name
    assert path.exists(), f"Sample PDF not found: {path}"
    gt = extract_ground_truth(path)
    assert gt.period_date == expected_period
