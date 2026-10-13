"""Deterministic extraction of declared subtotals and grand totals from PDF text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
import fitz  # PyMuPDF


KNOWN_CATEGORIES: list[str] = [
    "Remuneraciones",
    "Consumos",
    "Mantenciones y Servicios",
    "Gas Cargo Comun al 30%",
    "Reparaciones y Repuestos",
    "Seguros",
    "Asesorías y Honorarios Profesionales",
    "Insumos",
    "Otros Gastos",
]

SPANISH_MONTH_MAP: dict[str, int] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "setiembre": 9,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


@dataclass
class GroundTruthData:
    """Declared totals and period date extracted directly from document text."""

    period_date: str | None = None
    grand_total: int | None = None
    section_totals: dict[str, int] = field(default_factory=dict)
    raw_section_totals: dict[str, int] = field(default_factory=dict)


def extract_ground_truth(
    pdf_source: str | Path | bytes,
) -> GroundTruthData:
    """Extract declared section subtotals and grand total from a digital PDF.

    Args:
        pdf_source: Path to the PDF file or raw PDF bytes.

    Returns:
        GroundTruthData with grand_total and normalized section_totals.
    """
    if isinstance(pdf_source, (str, Path)):
        doc = fitz.open(str(pdf_source))
    elif isinstance(pdf_source, bytes):
        doc = fitz.open(stream=pdf_source, filetype="pdf")
    else:
        raise TypeError(f"Unsupported pdf_source type: {type(pdf_source)}")

    try:
        full_text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()

    result = GroundTruthData()

    # 0. Extract Billing Period (Informe Gastos Comunes <Mes> <Año>)
    m_period = re.search(
        r"Informe Gastos Comunes\s*[\r\n]+\s*([A-Za-z]+)\s+(\d{4})",
        full_text,
        re.IGNORECASE,
    )
    if m_period:
        month_name = m_period.group(1).lower().strip()
        year = int(m_period.group(2))
        month_num = SPANISH_MONTH_MAP.get(month_name)
        if month_num is not None:
            result.period_date = f"01/{month_num:02d}/{year:04d}"

    # 1. Extract Grand Total (TOTAL GASTOS COMUNES)
    m_grand = re.search(r"TOTAL GASTOS COMUNES\s*[\r\n]+\s*(-?)\$(-?)([\d\.]+)", full_text)
    if m_grand:
        sign = -1 if (m_grand.group(1) == "-" or m_grand.group(2) == "-") else 1
        result.grand_total = sign * int(m_grand.group(3).replace(".", ""))

    # 2. Extract Section Subtotals by text block ordering
    positions: list[tuple[int, str]] = []
    for cat in KNOWN_CATEGORIES:
        pos = full_text.find(cat)
        if pos != -1:
            positions.append((pos, cat))

    positions.sort(key=lambda x: x[0])

    pos_end = full_text.find("TOTAL GASTOS COMUNES")
    if pos_end != -1:
        positions.append((pos_end, "TOTAL GASTOS COMUNES"))
    else:
        positions.append((len(full_text), "END_OF_DOC"))

    raw_subtotals: dict[str, int] = {}
    for i in range(len(positions) - 1):
        start_pos, cat = positions[i]
        end_pos, _ = positions[i + 1]
        chunk = full_text[start_pos:end_pos]
        amounts = []
        for m in re.finditer(r"(-?)\$(-?)([\d\.]+)", chunk):
            sign = -1 if (m.group(1) == "-" or m.group(2) == "-") else 1
            amounts.append(sign * int(m.group(3).replace(".", "")))
        if amounts:
            # The declared section subtotal is the last amount printed in that section
            raw_subtotals[cat] = amounts[-1]

    result.raw_section_totals = dict(raw_subtotals)

    # 3. Normalize: Merge "Gas Cargo Comun al 30%" into "Consumos"
    normalized_totals: dict[str, int] = dict(raw_subtotals)
    gas_amount = normalized_totals.pop("Gas Cargo Comun al 30%", None)

    if gas_amount is not None:
        if "Consumos" in normalized_totals:
            normalized_totals["Consumos"] += gas_amount
        else:
            normalized_totals["Consumos"] = gas_amount

    result.section_totals = normalized_totals

    return result
