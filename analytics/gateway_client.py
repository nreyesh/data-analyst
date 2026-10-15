"""Client adapter for dispatching queries to the Data Gateway Proxy."""

from __future__ import annotations

import logging
from typing import Any
import requests
from fastapi import HTTPException

from analytics.config import get_analytics_settings

logger = logging.getLogger("analytics.gateway_client")


def query_data_gateway(
    sql: str,
    timeout_seconds: float | None = None,
    max_rows: int | None = None,
    gateway_url: str | None = None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Execute a query through the Data Gateway Proxy with automatic local fallback.

    Args:
        sql: SQLite query string to execute.
        timeout_seconds: Optional timeout ceiling (defaults to settings).
        max_rows: Optional max rows limit (defaults to settings).
        gateway_url: Optional URL override for the gateway endpoint.

    Returns:
        tuple[list[dict] | None, error_message | None]:
            On success: (rows, None)
            On failure: (None, error_message)
    """
    settings = get_analytics_settings()
    timeout = timeout_seconds if timeout_seconds is not None else settings.query_timeout_seconds
    rows_limit = max_rows if max_rows is not None else settings.max_query_rows
    target_url = gateway_url or settings.gateway_execute_url

    payload = {
        "sql": sql,
        "timeout_seconds": timeout,
        "max_rows": rows_limit,
    }

    # 1. Attempt HTTP call to external Data Gateway
    try:
        response = requests.post(
            target_url,
            json=payload,
            timeout=timeout + 2.0,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("rows", []), None

        error_detail = response.json().get("detail", response.text) if response.headers.get("content-type") == "application/json" else response.text
        logger.warning(
            "Data Gateway returned HTTP %d: %s", response.status_code, error_detail
        )
        return None, f"DataGatewayError ({response.status_code}): {error_detail}"

    except requests.exceptions.ConnectionError:
        logger.info(
            "Data Gateway service offline at %s. Falling back to in-process gateway execution.",
            target_url,
        )
    except Exception as exc:
        logger.warning("HTTP gateway query failed (%s). Falling back to in-process.", exc)

    # 2. Local in-process fallback via backend router controller
    try:
        from backend.app.routes.analytics import ExecuteQueryRequest, execute_query

        req = ExecuteQueryRequest(
            sql=sql,
            timeout_seconds=timeout,
            max_rows=rows_limit,
        )
        resp = execute_query(req)
        return resp.rows, None

    except HTTPException as exc:
        logger.warning("In-process Data Gateway returned HTTP %d: %s", exc.status_code, exc.detail)
        return None, f"DataGatewayError ({exc.status_code}): {exc.detail}"
    except Exception as exc:
        logger.error("In-process Data Gateway execution failed: %s", exc, exc_info=True)
        return None, f"DataGatewayExecutionError: {exc}"
