"""API router for analytics queries and isolated Data Gateway execution."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Any
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from backend.app.config import get_settings

logger = logging.getLogger("backend.app.routes.analytics")

router = APIRouter(prefix="/analytics", tags=["analytics"])

ALLOWED_TABLES = {"expense_reports", "expense_sections", "expense_items"}
FORBIDDEN_CATALOGS_PATTERN = re.compile(
    r"\b(sqlite_master|sqlite_schema|sqlite_temp_master|pragma[a-zA-Z0-9_]*)\b",
    re.IGNORECASE,
)
FORBIDDEN_MUTATIONS_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|REPLACE)\b",
    re.IGNORECASE,
)


class ExecuteQueryRequest(BaseModel):
    """Payload for low-level query execution via Data Gateway Proxy."""

    model_config = ConfigDict(extra="ignore")

    sql: str = Field(..., min_length=1, description="Read-only SQLite query to execute")
    timeout_seconds: float = Field(
        default=3.0, ge=0.1, le=30.0, description="Execution timeout ceiling in seconds"
    )
    max_rows: int = Field(
        default=500, ge=1, le=5000, description="Maximum number of rows to return"
    )


class ExecuteQueryResponse(BaseModel):
    """Response payload returned by Data Gateway Proxy."""

    columns: list[str] = Field(default_factory=list, description="Column names returned")
    rows: list[dict[str, Any]] = Field(
        default_factory=list, description="Array of row dictionaries"
    )
    row_count: int = Field(..., description="Number of rows returned")
    truncated: bool = Field(
        default=False, description="True if results were capped by max_rows limit"
    )
    execution_time_ms: float = Field(
        ..., description="Server execution time in milliseconds"
    )


def strip_sql_comments(sql: str) -> str:
    """Remove single-line and multi-line SQL comments."""
    # Remove multi-line comments /* ... */
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Remove single-line comments -- ...
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql.strip()


def extract_cte_names(sql: str) -> set[str]:
    """Extract Common Table Expression (CTE) names declared in WITH clauses."""
    ctes: set[str] = set()
    # Match patterns like: WITH name AS ( or WITH name(col1, col2) AS (
    matches = re.findall(
        r"(?:WITH\s+(?:RECURSIVE\s+)?|,)\s*([a-zA-Z_0-9]+)(?:\s*\([^)]*\))?\s+AS\s*\(",
        sql,
        re.IGNORECASE,
    )
    for m in matches:
        ctes.add(m.lower())
    return ctes


def extract_table_names(sql: str) -> set[str]:
    """Extract table identifiers referenced after FROM or JOIN clauses."""
    tables: set[str] = set()
    # Match patterns: FROM <table>, JOIN <table>
    matches = re.findall(
        r"(?:FROM|JOIN)\s+([a-zA-Z_0-9]+)", sql, re.IGNORECASE
    )
    for m in matches:
        tables.add(m.lower())
    return tables


def validate_query_security(sql: str) -> None:
    """Perform AST / regex security validation against the query.

    Raises:
        HTTPException(400): If query is not read-only or contains syntax mutations.
        HTTPException(403): If query probes internal schemas, catalogs, or non-whitelisted tables.
    """
    cleaned_sql = strip_sql_comments(sql)
    if not cleaned_sql:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SQL query cannot be empty.",
        )

    # 1. Anti-introspection: reject any reference to system catalogs or PRAGMA statements
    if FORBIDDEN_CATALOGS_PATTERN.search(cleaned_sql):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Security Exception: Access to system catalogs, metadata schemas, and pragmas is forbidden.",
        )

    # 2. Prevent multi-statement attacks (e.g. SELECT 1; DROP TABLE ...)
    statements = [s.strip() for s in cleaned_sql.split(";") if s.strip()]
    if len(statements) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Security Exception: Multiple semicolon-separated statements are not permitted.",
        )

    single_stmt = statements[0] if statements else cleaned_sql

    # 3. Must begin with SELECT or WITH
    if not re.match(r"^(SELECT|WITH)\b", single_stmt, re.IGNORECASE):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Security Exception: Only read-only queries beginning with SELECT or WITH are permitted.",
        )

    # 4. Reject explicit mutation keywords
    if FORBIDDEN_MUTATIONS_PATTERN.search(single_stmt):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Security Exception: Data mutation statements (INSERT/UPDATE/DELETE/DROP/ALTER) are forbidden.",
        )

    # 5. Table Whitelisting
    cte_names = extract_cte_names(single_stmt)
    referenced_tables = extract_table_names(single_stmt)

    for table in referenced_tables:
        if table not in ALLOWED_TABLES and table not in cte_names:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Security Exception: Table '{table}' is not in the authorized table whitelist.",
            )


@router.post(
    "/execute-query",
    response_model=ExecuteQueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute validated read-only SQLite query",
    description="Internal Data Gateway proxy executing vetted read-only analytical queries under strict timeouts and row ceilings.",
)
def execute_query(payload: ExecuteQueryRequest) -> ExecuteQueryResponse:
    """Execute a query against SQLite in read-only mode with progress handler timeout."""
    validate_query_security(payload.sql)

    settings = get_settings()
    db_path = settings.database_path
    if not db_path.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database file not found at {db_path}.",
        )

    start_time = time.perf_counter()
    interrupted_by_timeout = False

    def progress_callback() -> int:
        nonlocal interrupted_by_timeout
        if time.perf_counter() - start_time > payload.timeout_seconds:
            interrupted_by_timeout = True
            return 1  # non-zero interrupts SQLite VM
        return 0

    # Open connection strictly in read-only mode
    uri_path = f"file:{db_path.resolve()}?mode=ro"
    try:
        conn = sqlite3.connect(uri_path, uri=True, timeout=1.0)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to connect to database in read-only mode: {exc}",
        ) from exc

    conn.row_factory = sqlite3.Row
    # Call progress handler every 1000 instructions
    conn.set_progress_handler(progress_callback, 1000)

    try:
        cursor = conn.cursor()
        cursor.execute(payload.sql)

        columns = [d[0] for d in cursor.description] if cursor.description else []
        # Fetch up to max_rows + 1 to detect truncation
        fetched_rows = cursor.fetchmany(payload.max_rows + 1)
        truncated = len(fetched_rows) > payload.max_rows
        rows_to_return = fetched_rows[: payload.max_rows]

        dict_rows = [dict(row) for row in rows_to_return]
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

        return ExecuteQueryResponse(
            columns=columns,
            rows=dict_rows,
            row_count=len(dict_rows),
            truncated=truncated,
            execution_time_ms=elapsed_ms,
        )

    except sqlite3.OperationalError as exc:
        if interrupted_by_timeout or "interrupted" in str(exc).lower():
            logger.warning(
                "Query execution timed out after %.2fs: %s",
                payload.timeout_seconds,
                payload.sql,
            )
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail=f"Query execution exceeded {payload.timeout_seconds:.1f}s timeout ceiling.",
            ) from exc
        logger.warning("SQLite operational error on query: %s -> %s", payload.sql, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SQLite Operational Error: {exc}",
        ) from exc
    except Exception as exc:
        logger.error("Unexpected error executing query: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected query execution error: {exc}",
        ) from exc
    finally:
        conn.close()


# ============================================================================
# High-Level Consumer Analytics API
# ============================================================================


class AnalyticsQueryRequest(BaseModel):
    """Consumer payload requesting financial analytics and optional visualization."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(..., min_length=1, description="Natural language question")
    include_chart: bool = Field(default=True, description="Whether to generate an analytical chart")
    max_retries: int = Field(default=2, ge=0, le=5, description="Maximum self-healing retries")
    thread_id: str | None = Field(default=None, description="Optional thread ID for resuming paused clarification")
    resume_choice: str | None = Field(default=None, description="Optional user clarification choice when resuming")


class AnalyticsQueryResponse(BaseModel):
    """Final answer returned to consumer client."""

    summary: str = Field(..., description="Executive markdown answer")
    is_authorized: bool = Field(default=True, description="False if query violated scope guard")
    needs_clarification: bool = Field(
        default=False, description="True if workflow paused awaiting user clarification"
    )
    clarification_prompt: str | None = Field(
        default=None, description="Clarification prompt when needs_clarification is True"
    )
    candidate_options: list[str] = Field(
        default_factory=list, description="Selectable options when needs_clarification is True"
    )
    thread_id: str | None = Field(
        default=None, description="Session thread ID for resuming paused execution"
    )
    sql_query: str | None = Field(default=None, description="Executed SQL query")
    data: list[dict[str, Any]] | None = Field(default=None, description="Fetched database records")
    chart_file_path: str | None = Field(default=None, description="Path to generated chart PNG")
    error_history: list[dict[str, Any]] = Field(default_factory=list, description="Telemetry errors")
    latency_seconds: float = Field(..., description="Total execution duration in seconds")


@router.post(
    "/query",
    response_model=AnalyticsQueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Run high-level natural language analytics query",
    description="Executes the full LangGraph analytics state machine and returns markdown answer, data, chart link, or clarification options.",
)
def run_analytics_query_endpoint(payload: AnalyticsQueryRequest) -> AnalyticsQueryResponse:
    """Execute end-to-end natural language analytics query."""
    from analytics.workflow import run_analysis

    try:
        if payload.thread_id is not None or payload.resume_choice is not None:
            result = run_analysis(
                query=payload.query,
                max_retries=payload.max_retries,
                thread_id=payload.thread_id,
                resume_choice=payload.resume_choice,
            )
        else:
            result = run_analysis(
                query=payload.query,
                max_retries=payload.max_retries,
            )
        return AnalyticsQueryResponse(
            summary=result.summary,
            is_authorized=result.is_authorized,
            needs_clarification=result.needs_clarification,
            clarification_prompt=result.clarification_prompt,
            candidate_options=result.candidate_options,
            thread_id=result.thread_id,
            sql_query=result.sql_query,
            data=result.data,
            chart_file_path=result.chart_file_path,
            error_history=result.error_history,
            latency_seconds=result.latency_seconds,
        )
    except Exception as exc:
        logger.error("Analytics query execution failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analytics workflow execution failed: {exc}",
        ) from exc


