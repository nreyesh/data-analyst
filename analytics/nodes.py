"""LangGraph node functions executing Planner, SQL Coder, Gateway, Visualizer, and Synthesizer."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from analytics.config import get_analytics_settings
from analytics.gateway_client import query_data_gateway
from analytics.prompt_loader import load_data_dictionary_str, load_prompt_template
from analytics.sandbox import run_plotting_sandbox
from analytics.state import DataAnalystState, ExecutionError, IntentPlan

logger = logging.getLogger("analytics.nodes")


def get_gemini_client() -> genai.Client:
    """Create and return a configured Gemini client."""
    settings = get_analytics_settings()
    api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY is not set. Please set it in your .env file or environment."
        )
    return genai.Client(api_key=api_key)


class SQLCoderOutput(BaseModel):
    """Structured output schema for SQL generation."""

    reasoning: str = Field(description="Brief explanation of the query logic")
    sql_query: str = Field(description="Valid SQLite SELECT query")


class VisualizerOutput(BaseModel):
    """Structured output schema for Python script generation."""

    reasoning: str = Field(description="Explanation of chart layout and visual choices")
    python_code: str = Field(description="Complete, executable Python Matplotlib script")


# ============================================================================
# 1. Planner Node (with Scope Guard)
# ============================================================================


def planner_node(state: DataAnalystState, client: genai.Client | None = None) -> dict[str, Any]:
    """Analyze user prompt, evaluate scope guard, and produce an IntentPlan."""
    settings = get_analytics_settings()
    gemini_client = client or get_gemini_client()
    system_instruction = load_prompt_template("planner_system.md")

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=IntentPlan,
        system_instruction=system_instruction,
        temperature=0.0,
    )

    contents = [
        f"User Query: {state['user_query']}"
    ]

    response = gemini_client.models.generate_content(
        model=settings.default_model,
        contents=contents,
        config=config,
    )

    if not response.text:
        raise RuntimeError("Planner model returned empty response.")

    plan = IntentPlan.model_validate_json(response.text)
    logger.info("Planner produced plan: authorized=%s, goal=%s", plan.is_authorized_query, plan.analysis_goal)

    return {
        "intent_plan": plan,
        "retry_count": 0,
    }


# ============================================================================
# 2. SQL Coder Node (with Self-Healing)
# ============================================================================


def sql_coder_node(state: DataAnalystState, client: genai.Client | None = None) -> dict[str, Any]:
    """Generate or correct SQLite SELECT query using IntentPlan and Data Dictionary."""
    settings = get_analytics_settings()
    gemini_client = client or get_gemini_client()
    system_instruction = load_prompt_template("sql_coder_system.md")
    data_dict_yaml = load_data_dictionary_str()

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=SQLCoderOutput,
        system_instruction=system_instruction,
        temperature=0.0,
    )

    plan = state["intent_plan"]
    plan_summary = (
        f"Goal: {plan.analysis_goal}\n"
        f"Tables: {plan.relevant_tables}\n"
        f"Time Scope: {plan.time_scope}"
    ) if plan else state["user_query"]

    contents = [
        f"=== DATABASE SCHEMA & CONVENTIONS ===\n{data_dict_yaml}\n",
        f"=== INTENT PLAN ===\n{plan_summary}\nOriginal User Prompt: {state['user_query']}",
    ]

    # Check for previous error feedback
    errors = state.get("error_history", [])
    if errors:
        last_error = errors[-1]["error_message"]
        contents.append(
            f"ATTENTION - PREVIOUS SQL ERROR DETECTED:\n{last_error}\n"
            f"Previous Failing SQL:\n{state.get('current_sql', '')}\n"
            "Please fix the SQL syntax or column name."
        )

    response = gemini_client.models.generate_content(
        model=settings.default_model,
        contents=contents,
        config=config,
    )

    if not response.text:
        raise RuntimeError("SQL Coder model returned empty response.")

    output = SQLCoderOutput.model_validate_json(response.text)
    logger.info("SQL Coder generated query: %s", output.sql_query)

    return {
        "current_sql": output.sql_query,
    }


# ============================================================================
# 3. SQL Gateway Execution Node
# ============================================================================


def sql_gateway_node(state: DataAnalystState) -> dict[str, Any]:
    """Dispatch SQL query to Data Gateway Proxy and evaluate execution result."""
    sql = state.get("current_sql") or ""
    rows, error_msg = query_data_gateway(sql=sql)

    if error_msg is not None:
        attempt = state.get("retry_count", 0) + 1
        err_record = ExecutionError(
            node="sql_gateway_node",
            error_message=error_msg,
            attempt=attempt,
        )
        logger.warning("Gateway query attempt %d failed: %s", attempt, error_msg)
        return {
            "sql_results": None,
            "error_history": [err_record],
            "retry_count": attempt,
        }

    logger.info("Gateway query succeeded: fetched %d rows", len(rows) if rows else 0)
    return {
        "sql_results": rows or [],
        "retry_count": 0,  # reset retry counter for subsequent nodes
    }


# ============================================================================
# 4. Visualizer Node
# ============================================================================


def visualizer_node(state: DataAnalystState, client: genai.Client | None = None) -> dict[str, Any]:
    """Generate standalone Matplotlib script reading from dataset.json."""
    settings = get_analytics_settings()
    gemini_client = client or get_gemini_client()
    system_instruction = load_prompt_template("visualizer_system.md")

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=VisualizerOutput,
        system_instruction=system_instruction,
        temperature=0.0,
    )

    rows = state.get("sql_results") or []
    sample_data = rows[:3]
    plan = state.get("intent_plan")

    contents = [
        f"Goal: {plan.visualization_goal or plan.analysis_goal if plan else state['user_query']}\n"
        f"Suggested Chart Type: {plan.suggested_chart_type if plan else 'auto'}\n"
        f"Sample Row Structure from dataset.json: {json.dumps(sample_data, ensure_ascii=False)}\n"
        f"Total Data Rows in dataset.json: {len(rows)}"
    ]

    errors = state.get("error_history", [])
    if errors:
        last_error = errors[-1]["error_message"]
        contents.append(
            f"ATTENTION - PREVIOUS VISUALIZATION ERROR DETECTED:\n{last_error}\n"
            f"Previous Failing Code:\n{state.get('visualization_code', '')}\n"
            "Please fix the Python script so that it runs without error and saves 'output.png'."
        )

    response = gemini_client.models.generate_content(
        model=settings.default_model,
        contents=contents,
        config=config,
    )

    if not response.text:
        raise RuntimeError("Visualizer model returned empty response.")

    output = VisualizerOutput.model_validate_json(response.text)
    logger.info("Visualizer generated script: length=%d", len(output.python_code))

    return {
        "visualization_code": output.python_code,
    }


# ============================================================================
# 5. Sandbox Runner Node
# ============================================================================


def sandbox_runner_node(state: DataAnalystState) -> dict[str, Any]:
    """Execute visualization script inside the isolated subprocess sandbox."""
    code = state.get("visualization_code") or ""
    dataset = state.get("sql_results") or []

    result = run_plotting_sandbox(
        script_code=code,
        dataset=dataset,
    )

    if not result.success:
        attempt = state.get("retry_count", 0) + 1
        err_record = ExecutionError(
            node="sandbox_runner_node",
            error_message=result.error_message or "Unknown sandbox execution error",
            attempt=attempt,
        )
        logger.warning("Sandbox run attempt %d failed: %s", attempt, result.error_message)
        return {
            "chart_file_path": None,
            "error_history": [err_record],
            "retry_count": attempt,
        }

    logger.info("Sandbox rendered chart successfully: %s", result.chart_path)
    return {
        "chart_file_path": str(result.chart_path) if result.chart_path else None,
        "retry_count": 0,
    }


# ============================================================================
# 6. Synthesizer Node (with Zero Schema Policy)
# ============================================================================


def synthesizer_node(state: DataAnalystState, client: genai.Client | None = None) -> dict[str, Any]:
    """Synthesize final executive markdown answer or institutional refusal."""
    plan = state.get("intent_plan")

    # If Scope Guard rejected the query, deliver polite institutional refusal directly
    if plan and not plan.is_authorized_query:
        refusal_text = (
            "### Aviso de Seguridad y Gobernanza de Datos\n\n"
            "Este sistema está configurado exclusivamente para realizar análisis financieros y reportería sobre los "
            "gastos comunes del edificio a nivel de datos y métricas agregadas.\n\n"
            "Por políticas de seguridad y protección de la infraestructura de la comunidad, **no está autorizado "
            "a divulgar información sobre la arquitectura interna, nombres de tablas ni esquemas técnicos de la base de datos**."
        )
        return {
            "final_markdown": refusal_text,
        }

    settings = get_analytics_settings()
    gemini_client = client or get_gemini_client()
    system_instruction = load_prompt_template("synthesizer_system.md")

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.1,
    )

    rows = state.get("sql_results") or []
    chart_path = state.get("chart_file_path")

    contents = [
        f"Original User Question: {state['user_query']}\n"
        f"Analysis Goal: {plan.analysis_goal if plan else ''}\n"
        f"Verified Database Results: {json.dumps(rows, ensure_ascii=False)}\n"
        f"Chart Image File Path: {chart_path or 'No chart generated'}"
    ]

    response = gemini_client.models.generate_content(
        model=settings.default_model,
        contents=contents,
        config=config,
    )

    markdown = response.text or "No se pudo generar la síntesis del reporte."
    logger.info("Synthesizer produced markdown summary: length=%d", len(markdown))

    return {
        "final_markdown": markdown,
    }
