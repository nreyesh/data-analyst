"""Fast-path tools for macro building expense reports and system diagnostics."""

from __future__ import annotations

import logging
import re
from typing import Any
import requests

from analytics.config import get_analytics_settings

logger = logging.getLogger("analytics.tools.building_tools")


def _normalize_period_date(period_date: str | None) -> str | None:
    """Normalize 'YYYY-MM' or 'YYYY-MM-01' into 'YYYY-MM-01'."""
    if not period_date:
        return None
    cleaned = period_date.strip()
    if re.match(r"^\d{4}-\d{2}$", cleaned):
        return f"{cleaned}-01"
    return cleaned


def get_building_summary(
    period_date: str | None = None,
    client: Any | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Retrieve structured macro building expense snapshot via backend REST API.

    Args:
        period_date: Optional period formatted as 'YYYY-MM-01' or 'YYYY-MM'. If None, fetches latest period.
        client: Optional HTTP client (requests.Session or FastAPI TestClient) for testing.
        base_url: Optional base endpoint URL override.

    Returns:
        Structured dictionary with building report snapshot or clean error dict.
    """
    settings = get_analytics_settings()
    endpoint = (base_url or settings.reports_api_url).rstrip("/")
    http_get = client.get if client is not None else requests.get
    norm_period = _normalize_period_date(period_date)

    try:
        if norm_period:
            detail_res = http_get(f"{endpoint}/period/{norm_period}")
            if detail_res.status_code == 404:
                return {
                    "error": f"No building expense report found for period '{norm_period}'."
                }
            if detail_res.status_code != 200:
                return {
                    "error": f"Backend returned HTTP {detail_res.status_code} when querying period '{norm_period}'."
                }
            detail = detail_res.json()
        else:
            list_res = http_get(endpoint)
            if list_res.status_code != 200:
                return {
                    "error": f"Backend returned HTTP {list_res.status_code} when listing building reports."
                }
            reports = list_res.json()
            if not reports or not isinstance(reports, list):
                return {"error": "No building expense reports found in database."}

            latest = reports[0]
            report_id = latest.get("id")
            if not report_id:
                return {"error": "Invalid building report record."}

            detail_res = http_get(f"{endpoint}/{report_id}")
            if detail_res.status_code != 200:
                detail = latest
            else:
                detail = detail_res.json()

        sections_raw = detail.get("sections", [])
        sections_summary = [
            {
                "category_name": s.get("category_name"),
                "expense_type": s.get("expense_type"),
                "calculated_subtotal": s.get("calculated_subtotal"),
            }
            for s in sections_raw
        ]

        return {
            "id": detail.get("id"),
            "period_date": detail.get("period_date"),
            "period_year": detail.get("period_year"),
            "period_month": detail.get("period_month"),
            "operational_total": detail.get("operational_total", 0),
            "fondos_total": detail.get("fondos_total", 0),
            "calculated_grand_total": detail.get("calculated_grand_total", 0),
            "sections": sections_summary,
        }

    except Exception as exc:
        logger.warning("Error fetching building summary for period '%s': %s", norm_period, exc)
        return {"error": f"Service error fetching building report: {exc}"}


def get_system_diagnostics(
    client: Any | None = None,
    reports_url: str | None = None,
    sync_url: str | None = None,
) -> dict[str, Any]:
    """Retrieve loaded report periods and spool synchronization status via backend REST API.

    Args:
        client: Optional HTTP client (requests.Session or FastAPI TestClient) for testing.
        reports_url: Optional reports endpoint URL override.
        sync_url: Optional sync status endpoint URL override.

    Returns:
        Structured dictionary with loaded periods and spool queue counts or clean error dict.
    """
    settings = get_analytics_settings()
    rep_endpoint = (reports_url or settings.reports_api_url).rstrip("/")
    queue_endpoint = (sync_url or settings.sync_queue_status_url).rstrip("/")
    http_get = client.get if client is not None else requests.get

    try:
        rep_res = http_get(rep_endpoint)
        if rep_res.status_code != 200:
            return {
                "error": f"Backend returned HTTP {rep_res.status_code} when querying reports status."
            }
        reports = rep_res.json() if isinstance(rep_res.json(), list) else []
        loaded_periods = [r.get("period_date") for r in reports if r.get("period_date")]

        queue_res = http_get(queue_endpoint)
        if queue_res.status_code != 200:
            return {
                "error": f"Backend returned HTTP {queue_res.status_code} when querying spool queue status."
            }
        queue_data = queue_res.json()

        return {
            "status": "healthy",
            "loaded_reports_count": len(loaded_periods),
            "loaded_periods": loaded_periods,
            "latest_period": loaded_periods[0] if loaded_periods else None,
            "pending_count": queue_data.get("pending_count", 0),
            "synced_count": queue_data.get("synced_count", 0),
            "failed_count": queue_data.get("failed_count", 0),
            "pending_files": queue_data.get("pending", []),
            "failed_files": queue_data.get("failed", []),
        }

    except Exception as exc:
        logger.warning("Error fetching system diagnostics: %s", exc)
        return {"error": f"Service error fetching system diagnostics: {exc}"}
