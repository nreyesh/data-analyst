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

    # Negative integer value
    with pytest.raises(ValidationError):
        SubTopic(name="Item", value=-500)
