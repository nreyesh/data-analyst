"""Cross-validation engine for expense reports against ground-truth totals."""

from __future__ import annotations

from data_extractor.ground_truth import GroundTruthData
from data_extractor.models import ExpenseReport, SectionValidation, ValidationResult


def validate_expense_report(
    report: ExpenseReport,
    ground_truth: GroundTruthData,
) -> ValidationResult:
    """Validate an extracted ExpenseReport against ground-truth totals.

    Performs two-tier validation:
    1. Granular check: sum of items in each section == declared section subtotal.
    2. Holistic check: sum across all sections == declared TOTAL GASTOS COMUNES.

    Args:
        report: The extracted ExpenseReport model.
        ground_truth: GroundTruthData containing declared totals from the PDF.

    Returns:
        ValidationResult containing validation status, calculations, and errors.
    """
    errors: list[str] = []
    section_validations: list[SectionValidation] = []

    # Map sections by normalized general_topic name
    extracted_sections_map = {
        sec.general_topic.strip(): sec for sec in report.sections
    }

    # 1. Validate each extracted section
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

    # 2. Check if any declared section was completely omitted
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

    # 3. Holistic Grand Total validation
    calc_grand_total = sum(
        item.value for sec in report.sections for item in sec.sub_topics
    )
    grand_diff = (
        calc_grand_total - ground_truth.grand_total
        if ground_truth.grand_total is not None
        else 0
    )

    if ground_truth.grand_total is not None and grand_diff != 0:
        errors.append(
            f"Grand total mismatch: sum of all extracted items is ${calc_grand_total:,} CLP, "
            f"but document TOTAL GASTOS COMUNES is ${ground_truth.grand_total:,} CLP "
            f"(difference: {grand_diff:+,} CLP)."
        )

    is_overall_valid = (len(errors) == 0)

    return ValidationResult(
        is_valid=is_overall_valid,
        calculated_grand_total=calc_grand_total,
        declared_grand_total=ground_truth.grand_total,
        grand_total_difference=grand_diff,
        section_validations=section_validations,
        errors=errors,
    )
