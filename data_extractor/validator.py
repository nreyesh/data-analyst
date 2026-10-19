"""Cross-validation engine for expense reports against ground-truth totals."""

from __future__ import annotations

import logging

from data_extractor.ground_truth import GroundTruthData
from data_extractor.models import ExpenseReport, SectionValidation, ValidationResult

logger = logging.getLogger("data_extractor.validator")


def validate_expense_report(
    report: ExpenseReport,
    ground_truth: GroundTruthData,
) -> ValidationResult:
    """Validate an extracted ExpenseReport against ground-truth totals.

    Performs three-tier validation:
    1. Granular check: sum of items in each section (including 'Fondos') == declared section subtotal.
    2. Operational check: sum across 'operational' sections == declared TOTAL GASTOS COMUNES.
    3. Holistic check: sum across all sections ('operational' + 'fondos') == declared final TOTAL.

    Args:
        report: The extracted ExpenseReport model.
        ground_truth: GroundTruthData containing declared totals from the PDF.

    Returns:
        ValidationResult containing validation status, calculations, and errors.
    """
    errors: list[str] = []
    section_validations: list[SectionValidation] = []

    # 0. Validate billing period date
    if ground_truth.period_date is not None:
        if report.issue_date.strip() != ground_truth.period_date.strip():
            errors.append(
                f"Billing period date mismatch: extracted issue_date is '{report.issue_date.strip()}', "
                f"but document declared period is '{ground_truth.period_date.strip()}'."
            )

    # Map sections by normalized general_topic name
    extracted_sections_map = {
        sec.general_topic.strip(): sec for sec in report.sections
    }

    # 1. Validate each extracted section (operational and Fondos)
    for section in report.sections:
        topic_name = section.general_topic.strip()
        calc_sum = sum(item.value for item in section.sub_topics)
        declared_subtotal = ground_truth.section_totals.get(topic_name)

        if declared_subtotal is not None:
            diff = calc_sum - declared_subtotal
            is_sec_valid = (diff == 0)
            if not is_sec_valid:
                errors.append(
                    f"Section '{topic_name}' sum mismatch: extracted items sum to "
                    f"${calc_sum:,} CLP, but expected declared subtotal is "
                    f"${declared_subtotal:,} CLP (difference: {diff:+,} CLP)."
                )
        else:
            diff = 0
            is_sec_valid = True

        section_validations.append(
            SectionValidation(
                general_topic=topic_name,
                calculated_sum=calc_sum,
                declared_subtotal=declared_subtotal,
                difference=diff,
                is_valid=is_sec_valid,
            )
        )

    # 2. Check if any declared section (including 'Fondos') was completely omitted
    for declared_topic, expected_amt in ground_truth.section_totals.items():
        if declared_topic not in extracted_sections_map:
            errors.append(
                f"Missing section '{declared_topic}': expected declared subtotal of "
                f"${expected_amt:,} CLP, but section was not found in extraction."
            )
            section_validations.append(
                SectionValidation(
                    general_topic=declared_topic,
                    calculated_sum=0,
                    declared_subtotal=expected_amt,
                    difference=-expected_amt,
                    is_valid=False,
                )
            )

    # 3. Operational & Fondos Subtotal Calculations
    calc_operational_total = sum(
        item.value
        for sec in report.sections
        if sec.expense_type == "operational"
        for item in sec.sub_topics
    )
    calc_fondos_total = sum(
        item.value
        for sec in report.sections
        if sec.expense_type == "fondos"
        for item in sec.sub_topics
    )

    op_diff = (
        calc_operational_total - ground_truth.operational_total
        if ground_truth.operational_total is not None
        else 0
    )
    if ground_truth.operational_total is not None and op_diff != 0:
        errors.append(
            f"Operational total mismatch: sum of operational sections is ${calc_operational_total:,} CLP, "
            f"but document TOTAL GASTOS COMUNES is ${ground_truth.operational_total:,} CLP "
            f"(difference: {op_diff:+,} CLP)."
        )

    fondos_diff = (
        calc_fondos_total - ground_truth.fondos_total
        if ground_truth.fondos_total is not None
        else 0
    )

    # 4. Holistic Final Grand Total validation
    calc_grand_total = calc_operational_total + calc_fondos_total
    grand_diff = (
        calc_grand_total - ground_truth.grand_total
        if ground_truth.grand_total is not None
        else 0
    )

    if ground_truth.grand_total is not None and grand_diff != 0:
        errors.append(
            f"Grand total mismatch: sum of all extracted items (operational + Fondos) is ${calc_grand_total:,} CLP, "
            f"but document final TOTAL is ${ground_truth.grand_total:,} CLP "
            f"(difference: {grand_diff:+,} CLP)."
        )

    is_overall_valid = (len(errors) == 0)

    if not is_overall_valid:
        sec_mismatches = [sv.general_topic for sv in section_validations if not sv.is_valid]
        has_grand_mismatch = (ground_truth.grand_total is not None and grand_diff != 0)
        has_period_mismatch = (
            ground_truth.period_date is not None
            and report.issue_date.strip() != ground_truth.period_date.strip()
        )
        logger.warning(
            "Expense report validation failed: section_mismatches=%s, grand_total_mismatch=%s, period_mismatch=%s",
            sec_mismatches,
            has_grand_mismatch,
            has_period_mismatch,
        )

    return ValidationResult(
        is_valid=is_overall_valid,
        calculated_operational_total=calc_operational_total,
        declared_operational_total=ground_truth.operational_total,
        operational_total_difference=op_diff,
        calculated_fondos_total=calc_fondos_total,
        declared_fondos_total=ground_truth.fondos_total,
        fondos_total_difference=fondos_diff,
        calculated_grand_total=calc_grand_total,
        declared_grand_total=ground_truth.grand_total,
        grand_total_difference=grand_diff,
        section_validations=section_validations,
        errors=errors,
    )
