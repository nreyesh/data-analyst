"""Cross-validation engine for expense reports against ground-truth totals."""

from __future__ import annotations

import logging

from data_extractor.ground_truth import GroundTruthData, UnitGroundTruthData
from data_extractor.models import (
    ExpenseReport,
    SectionValidation,
    UnitBillReport,
    UnitValidationResult,
    ValidationResult,
)

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


def validate_unit_bill(
    report: UnitBillReport,
    ground_truth: UnitGroundTruthData,
) -> UnitValidationResult:
    """Validate an extracted UnitBillReport against ground-truth figures from the PDF.

    Enforces 5 deterministic checks:
    1. Header fields (issue_date, unit_code, alicuota_total, last_payment_date, last_payment_amount).
    2. Tabla Conceptos sum == declared Subtotal Departamento.
    3. Tabla Consumos Generales reading delta, rate multiplication, and sum == declared Subtotal Consumo.
    4. Tabla Gastos por Unidad recargo items sum == declared Subtotal Recargos.
    5. Holistic monthly grand total == declared Total del mes.
    """
    errors: list[str] = []

    if (
        ground_truth.period_date is None
        and ground_truth.declared_total_month is None
        and ground_truth.declared_dept_subtotal is None
    ):
        errors.append(
            "Document ground truth anchors ('Nota de Cobro', 'Subtotal Departamento', 'Total del mes') "
            "were not found in the PDF. Ensure this file is a valid Unit Billing Statement."
        )

    # 1. Header validations
    if ground_truth.period_date is not None:
        if report.issue_date.strip() != ground_truth.period_date.strip():
            errors.append(
                f"Billing period date mismatch: extracted issue_date is '{report.issue_date.strip()}', "
                f"but document declared period is '{ground_truth.period_date.strip()}'."
            )

    if ground_truth.unit_code is not None:
        if report.unit_code.strip().upper() != ground_truth.unit_code.strip().upper():
            errors.append(
                f"Unit code mismatch: extracted unit_code is '{report.unit_code.strip()}', "
                f"but document declared unit is '{ground_truth.unit_code.strip()}'."
            )

    if ground_truth.alicuota_total is not None:
        if abs(report.alicuota_total - ground_truth.alicuota_total) > 1e-6:
            errors.append(
                f"Alícuota Total mismatch: extracted alicuota_total is {report.alicuota_total}, "
                f"but document declared Alícuota Total is {ground_truth.alicuota_total}."
            )

    if ground_truth.last_payment_date is not None:
        extracted_last_date = (report.last_payment_date or "").strip()
        if extracted_last_date != ground_truth.last_payment_date.strip():
            errors.append(
                f"Last payment date mismatch: extracted last_payment_date is '{extracted_last_date}', "
                f"but document declared Fecha último Pago is '{ground_truth.last_payment_date.strip()}'."
            )

    if ground_truth.last_payment_amount is not None:
        if report.last_payment_amount != ground_truth.last_payment_amount:
            errors.append(
                f"Last payment amount mismatch: extracted last_payment_amount is "
                f"${report.last_payment_amount or 0:,} CLP, but document declared Monto último Pago is "
                f"${ground_truth.last_payment_amount:,} CLP."
            )

    # 2. Tabla Conceptos check
    calc_dept_subtotal = sum(item.amount for item in report.concepts)
    dept_diff = (
        calc_dept_subtotal - ground_truth.declared_dept_subtotal
        if ground_truth.declared_dept_subtotal is not None
        else 0
    )
    if ground_truth.declared_dept_subtotal is not None and dept_diff != 0:
        errors.append(
            f"[Tabla Conceptos] Subtotal Departamento mismatch: extracted concepts sum to "
            f"${calc_dept_subtotal:,} CLP, but declared 'Subtotal Departamento' is "
            f"${ground_truth.declared_dept_subtotal:,} CLP (difference: {dept_diff:+,} CLP)."
        )

    # 3. Tabla Consumos Generales check (meter readings + rate math + subtotal)
    for item in report.consumptions:
        reading_delta = round(item.current_reading - item.previous_reading, 6)
        if abs(reading_delta - item.consumption) > 1e-4:
            errors.append(
                f"[Tabla Consumos Generales] Meter reading delta mismatch for '{item.item_name}': "
                f"current_reading ({item.current_reading}) - previous_reading ({item.previous_reading}) "
                f"= {reading_delta}, which does not match extracted consumption ({item.consumption})."
            )
        expected_line_total = round(item.consumption * item.unit_rate)
        if abs(expected_line_total - item.total) > 5:
            errors.append(
                f"[Tabla Consumos Generales] Line total calculation mismatch for '{item.item_name}': "
                f"consumption ({item.consumption}) * unit_rate ({item.unit_rate}) = ${expected_line_total:,} CLP, "
                f"which does not match extracted total (${item.total:,} CLP)."
            )

    calc_consumo_subtotal = sum(item.total for item in report.consumptions)
    consumo_diff = (
        calc_consumo_subtotal - ground_truth.declared_consumo_subtotal
        if ground_truth.declared_consumo_subtotal is not None
        else 0
    )
    if ground_truth.declared_consumo_subtotal is not None and consumo_diff != 0:
        errors.append(
            f"[Tabla Consumos Generales] Subtotal Consumo mismatch: extracted consumptions sum to "
            f"${calc_consumo_subtotal:,} CLP, but declared 'Subtotal Consumo' is "
            f"${ground_truth.declared_consumo_subtotal:,} CLP (difference: {consumo_diff:+,} CLP)."
        )

    # 4. Tabla Gastos por Unidad (Recargos check)
    calc_recargos_subtotal = sum(
        item.amount for item in report.unit_charges if item.charge_type == "recargo"
    )
    recargos_diff = (
        calc_recargos_subtotal - ground_truth.declared_recargos_subtotal
        if ground_truth.declared_recargos_subtotal is not None
        else 0
    )
    if ground_truth.declared_recargos_subtotal is not None and recargos_diff != 0:
        errors.append(
            f"[Tabla Gastos por Unidad] Subtotal Recargos mismatch: extracted 'recargo' items sum to "
            f"${calc_recargos_subtotal:,} CLP, but declared 'Subtotal Recargos' is "
            f"${ground_truth.declared_recargos_subtotal:,} CLP (difference: {recargos_diff:+,} CLP)."
        )

    # 5. Holistic Total del mes check
    calc_unit_subtotal = sum(item.amount for item in report.unit_charges)
    calc_total_month = calc_dept_subtotal + calc_consumo_subtotal + calc_unit_subtotal
    total_month_diff = (
        calc_total_month - ground_truth.declared_total_month
        if ground_truth.declared_total_month is not None
        else 0
    )
    if ground_truth.declared_total_month is not None and total_month_diff != 0:
        errors.append(
            f"[Total del mes] Grand total mismatch: sum of all tables (Conceptos ${calc_dept_subtotal:,} + "
            f"Consumos ${calc_consumo_subtotal:,} + Gastos por Unidad ${calc_unit_subtotal:,} = "
            f"${calc_total_month:,} CLP) does not match declared 'Total del mes' "
            f"${ground_truth.declared_total_month:,} CLP (difference: {total_month_diff:+,} CLP)."
        )

    is_valid = len(errors) == 0
    if not is_valid:
        logger.warning(
            "Unit bill validation failed: dept_mismatch=%s, consumo_mismatch=%s, recargos_mismatch=%s, total_mismatch=%s",
            dept_diff != 0,
            consumo_diff != 0,
            recargos_diff != 0,
            total_month_diff != 0,
        )

    return UnitValidationResult(
        is_valid=is_valid,
        calculated_dept_subtotal=calc_dept_subtotal,
        declared_dept_subtotal=ground_truth.declared_dept_subtotal,
        dept_subtotal_difference=dept_diff,
        calculated_consumo_subtotal=calc_consumo_subtotal,
        declared_consumo_subtotal=ground_truth.declared_consumo_subtotal,
        consumo_subtotal_difference=consumo_diff,
        calculated_recargos_subtotal=calc_recargos_subtotal,
        declared_recargos_subtotal=ground_truth.declared_recargos_subtotal,
        recargos_subtotal_difference=recargos_diff,
        calculated_unit_subtotal=calc_unit_subtotal,
        calculated_total_month=calc_total_month,
        declared_total_month=ground_truth.declared_total_month,
        total_month_difference=total_month_diff,
        errors=errors,
    )

