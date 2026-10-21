"""Fast-path tools for individual apartment unit statements."""

from __future__ import annotations

import logging
from typing import Any
import requests

from analytics.config import get_analytics_settings

logger = logging.getLogger("analytics.tools.unit_tools")


def get_unit_bill_details(
    unit_code: str,
    period_date: str | None = None,
    client: Any | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Retrieve structured billing snapshot for an apartment unit via backend REST API.

    Args:
        unit_code: Apartment code (e.g. 'B-504').
        period_date: Optional period formatted as 'YYYY-MM-01'. If None, fetches latest period.
        client: Optional HTTP client (requests.Session or FastAPI TestClient) for testing.
        base_url: Optional base endpoint URL override.

    Returns:
        Structured dictionary with billing snapshot or clean error dict.
    """
    norm_unit = (unit_code or "").strip().upper()
    if not norm_unit:
        return {"error": "unit_code is required"}

    settings = get_analytics_settings()
    endpoint = base_url or settings.unit_bills_api_url

    http_get = client.get if client is not None else requests.get

    try:
        # 1. Fetch list of unit bill summaries for unit_code
        res = http_get(f"{endpoint}", params={"unit_code": norm_unit})
        if res.status_code != 200:
            return {
                "error": f"Backend returned HTTP {res.status_code} when querying unit '{norm_unit}'."
            }

        bills = res.json()
        if not bills or not isinstance(bills, list):
            return {"error": f"No unit bill found for unit '{norm_unit}'."}

        # 2. Match exact period_date or pick latest (first in list)
        target_summary: dict[str, Any] | None = None
        if period_date:
            target_period = period_date.strip()
            for b in bills:
                if b.get("period_date") == target_period:
                    target_summary = b
                    break
            if not target_summary:
                return {
                    "error": f"No unit bill found for unit '{norm_unit}' in period '{period_date}'."
                }
        else:
            target_summary = bills[0]

        bill_id = target_summary.get("id")
        if not bill_id:
            return {"error": f"Invalid unit bill record for '{norm_unit}'."}

        # 3. Fetch full bill detail
        detail_res = http_get(f"{endpoint}/{bill_id}")
        if detail_res.status_code != 200:
            return {
                "error": f"Failed to retrieve bill detail for ID '{bill_id}' (HTTP {detail_res.status_code})."
            }

        detail = detail_res.json()
        return {
            "unit_code": detail.get("unit_code"),
            "period_date": detail.get("period_date"),
            "period_year": detail.get("period_year"),
            "period_month": detail.get("period_month"),
            "alicuota_total": detail.get("alicuota_total"),
            "calculated_total_month": detail.get("calculated_total_month"),
            "calculated_dept_subtotal": detail.get("calculated_dept_subtotal"),
            "calculated_consumo_total": detail.get("calculated_consumo_total"),
            "calculated_unit_subtotal": detail.get("calculated_unit_subtotal"),
            "last_payment_date": detail.get("last_payment_date"),
            "last_payment_amount": detail.get("last_payment_amount"),
            "concepts": detail.get("concepts", []),
            "consumptions": detail.get("consumptions", []),
            "unit_charges": detail.get("unit_charges", []),
        }

    except Exception as exc:
        logger.warning("Error fetching unit bill details for '%s': %s", norm_unit, exc)
        return {"error": f"Service error fetching unit bill: {exc}"}
