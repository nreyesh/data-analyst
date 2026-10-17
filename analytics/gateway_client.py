"""Client adapter for dispatching queries to the Data Gateway Proxy over HTTP."""

from __future__ import annotations

import logging
from typing import Any
import requests

from analytics.config import get_analytics_settings

logger = logging.getLogger("analytics.gateway_client")

GATEWAY_UNAVAILABLE_PREFIX = "DataGatewayUnavailableError:"


def is_gateway_unavailable_error(error_msg: str | None) -> bool:
    """Return True if the error message indicates the Data Gateway service is unreachable/offline."""
    return bool(error_msg and error_msg.startswith(GATEWAY_UNAVAILABLE_PREFIX))


def query_data_gateway(
    sql: str,
    timeout_seconds: float | None = None,
    max_rows: int | None = None,
    gateway_url: str | None = None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Execute a query strictly through the remote Data Gateway Proxy HTTP endpoint.

    Args:
        sql: SQLite query string to execute.
        timeout_seconds: Optional timeout ceiling (defaults to settings).
        max_rows: Optional max rows limit (defaults to settings).
        gateway_url: Optional URL override for the gateway endpoint.

    Returns:
        tuple[list[dict] | None, error_message | None]:
            On success: (rows, None)
            On query error (HTTP 4xx): (None, "DataGatewayError (<status>): <detail>")
            On gateway outage (ConnectionError/Timeout/HTTP 502-504):
                (None, "DataGatewayUnavailableError: <detail>")
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

    try:
        response = requests.post(
            target_url,
            json=payload,
            timeout=timeout + 2.0,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("rows", []), None

        content_type = response.headers.get("content-type", "")
        error_detail = (
            response.json().get("detail", response.text)
            if "application/json" in content_type
            else response.text
        )

        if response.status_code in (502, 503, 504):
            logger.error(
                "Data Gateway service unavailable (HTTP %d) at %s: %s",
                response.status_code,
                target_url,
                error_detail,
            )
            return (
                None,
                f"{GATEWAY_UNAVAILABLE_PREFIX} Service unavailable (HTTP {response.status_code}) at {target_url}: {error_detail}",
            )

        logger.warning(
            "Data Gateway returned HTTP %d: %s", response.status_code, error_detail
        )
        return None, f"DataGatewayError ({response.status_code}): {error_detail}"

    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        logger.error(
            "Data Gateway service unreachable at %s: %s",
            target_url,
            exc,
        )
        return (
            None,
            f"{GATEWAY_UNAVAILABLE_PREFIX} Cannot connect to Data Gateway at {target_url} ({exc})",
        )
    except Exception as exc:
        logger.error("Unexpected HTTP gateway failure at %s: %s", target_url, exc)
        return (
            None,
            f"{GATEWAY_UNAVAILABLE_PREFIX} Unexpected error communicating with Data Gateway at {target_url} ({exc})",
        )
