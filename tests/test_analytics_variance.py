"""Unit and integration tests for the Variance Analysis Loop & Interactive Clarification (Phase 3)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from analytics.nodes import (
    planner_node,
    sql_coder_node,
    trajectory_evaluator_node,
    trajectory_inspector_node,
)
from analytics.state import (
    AnalyticsResult,
    DataAnalystState,
    IntentPlan,
    TrajectoryEvaluation,
)
from analytics.workflow import (
    route_after_clarification,
    route_after_evaluator,
    route_after_planner,
    run_analysis,
)
from backend.app.main import app


# ============================================================================
# Task 5.1: IntentPlan Archetype & Planner Variance Detection Tests
# ============================================================================


def test_intent_plan_archetype_defaults_and_validation():
    """Verify IntentPlan supports all 5 analytical archetypes and clarification fields."""
    plan = IntentPlan(
        is_authorized_query=True,
        analysis_type="variance_driver",
        temporal_resolution="monthly",
        analysis_goal="Identificar las causas del aumento de gastos en 2026",
        relevant_tables=["expense_reports", "expense_sections"],
        time_scope="2026",
        requires_visualization=True,
        suggested_chart_type="bar",
    )
    assert plan.analysis_type == "variance_driver"
    assert plan.temporal_resolution == "monthly"
    assert plan.needs_clarification is False
    assert plan.candidate_options == []


def test_planner_node_enforces_monthly_resolution_on_variance():
    """Verify planner_node forces temporal_resolution='monthly' for variance_driver queries."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    # Even if LLM mistakenly outputs 'yearly', planner_node enforces 'monthly'
    mock_response.text = IntentPlan(
        is_authorized_query=True,
        analysis_type="variance_driver",
        temporal_resolution="yearly",
        analysis_goal="Explain expense increase in 2026",
        relevant_tables=["expense_reports", "expense_sections"],
        time_scope="2026",
    ).model_dump_json()
    mock_client.models.generate_content.return_value = mock_response

    state: DataAnalystState = {
        "messages": [],
        "user_query": "¿A qué se debe el aumento del gasto en el 2026?",
    }
    res = planner_node(state, client=mock_client)
    plan = res["intent_plan"]
    assert plan.analysis_type == "variance_driver"
    assert plan.temporal_resolution == "monthly"


def test_route_after_planner_variance_and_clarification():
    """Verify route_after_planner routes variance_driver to trajectory_inspector and ambiguity to clarification."""
    variance_state: DataAnalystState = {
        "user_query": "¿A qué se debe el aumento en 2026?",
        "intent_plan": IntentPlan(
            is_authorized_query=True,
            analysis_type="variance_driver",
            temporal_resolution="monthly",
            time_scope="2026",
        ),
    }
    assert route_after_planner(variance_state) == "trajectory_inspector"

    ambiguous_state: DataAnalystState = {
        "user_query": "¿A qué se debe el aumento?",
        "intent_plan": IntentPlan(
            is_authorized_query=True,
            analysis_type="variance_driver",
            needs_clarification=True,
            clarification_prompt="¿A qué período te refieres?",
            candidate_options=["Año 2026", "2026 vs 2025"],
        ),
    }
    assert route_after_planner(ambiguous_state) == "clarification"


# ============================================================================
# Task 5.2: Macro Trajectory Inspector & Evaluator Paradox / Inflection Tests
# ============================================================================


@pytest.fixture
def gateway_bridge(monkeypatch):
    """Route analytics.gateway_client HTTP calls through FastAPI TestClient(app)."""
    client = TestClient(app)

    def _mock_post(url: str, json: dict, timeout: float = 5.0):
        return client.post("/api/v1/analytics/execute-query", json=json)

    monkeypatch.setattr("analytics.gateway_client.requests.post", _mock_post)


def test_trajectory_inspector_fetches_2026_chronological_totals(gateway_bridge):
    """Verify trajectory_inspector_node queries expense_reports for 2026 in chronological order."""
    state: DataAnalystState = {
        "user_query": "¿A qué se debe el aumento del gasto en el 2026?",
        "intent_plan": IntentPlan(
            is_authorized_query=True,
            analysis_type="variance_driver",
            time_scope="2026",
        ),
    }
    res = trajectory_inspector_node(state)
    trajectory = res["macro_trajectory"]
    assert len(trajectory) >= 8
    dates = [row["period_date"] for row in trajectory]
    assert dates == sorted(dates)
    assert "2026-05-01" in dates
    assert "2026-07-01" in dates

    # Verify May 2026 is the annual trough ($49,387,905) and July 2026 is the winter peak ($56,907,702)
    by_date = {row["period_date"]: row["calculated_grand_total"] for row in trajectory}
    assert by_date["2026-05-01"] == 49387905
    assert by_date["2026-07-01"] == 56907702
    assert by_date["2026-01-01"] == 57063002


def test_trajectory_evaluator_detects_paradox_and_isolates_inflection_points():
    """Verify trajectory_evaluator_node isolates May->July inflection and triggers clarification on ambiguous 2026 premise."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_eval = TrajectoryEvaluation(
        premise_status="nuanced_inflection",
        evaluation_summary="El gasto cayó de $57,1M en enero a un mínimo de $49,4M en mayo, y luego repuntó a $56,9M en julio.",
        baseline_period="2026-05-01",
        peak_period="2026-07-01",
        needs_clarification=True,
        clarification_prompt="Observo que en 2026 el gasto cayó hasta mayo y subió en invierno. ¿Qué período deseas analizar?",
        candidate_options=[
            "El repunte invernal de mayo a julio 2026",
            "La disminución de enero a mayo 2026",
            "Comparar 2026 vs 2025",
        ],
    )
    mock_response.text = mock_eval.model_dump_json()
    mock_client.models.generate_content.return_value = mock_response

    state: DataAnalystState = {
        "user_query": "¿A qué se debe el aumento del gasto en el 2026?",
        "intent_plan": IntentPlan(
            is_authorized_query=True,
            analysis_type="variance_driver",
            time_scope="2026",
        ),
        "macro_trajectory": [
            {"period_date": "2026-01-01", "calculated_grand_total": 57063002},
            {"period_date": "2026-05-01", "calculated_grand_total": 49387905},
            {"period_date": "2026-07-01", "calculated_grand_total": 56907702},
        ],
    }

    res = trajectory_evaluator_node(state, client=mock_client)
    eval_out = res["trajectory_evaluation"]
    assert eval_out.premise_status == "nuanced_inflection"
    assert eval_out.baseline_period == "2026-05-01"
    assert eval_out.peak_period == "2026-07-01"
    assert eval_out.needs_clarification is True
    assert route_after_evaluator({"trajectory_evaluation": eval_out}) == "clarification"

    # Verify circuit breaker: if user_clarification_choice is already present, needs_clarification is forced False
    state_with_choice: DataAnalystState = {
        **state,
        "user_clarification_choice": "El repunte invernal de mayo a julio 2026",
    }
    res_resumed = trajectory_evaluator_node(state_with_choice, client=mock_client)
    assert res_resumed["trajectory_evaluation"].needs_clarification is False
    assert route_after_evaluator(res_resumed) == "sql_coder"


# ============================================================================
# Task 5.3: End-to-End Integration Test (Pause, Resume & Real SQLite Delta Ranking)
# ============================================================================


def test_end_to_end_variance_loop_identifies_consumos_over_static_mantenciones(gateway_bridge):
    """Verify full two-hop workflow pauses on ambiguous 2026 query, resumes with user choice,
    and executes a real SQLite CTE delta query ranking 'consumos' above static 'mantenciones y servicios'.
    """
    plan_json = IntentPlan(
        is_authorized_query=True,
        analysis_type="variance_driver",
        temporal_resolution="monthly",
        analysis_goal="Identificar las categorías que impulsaron el aumento del gasto en 2026",
        relevant_tables=["expense_reports", "expense_sections"],
        time_scope="2026",
        requires_visualization=False,
    ).model_dump_json()

    eval_pause_json = TrajectoryEvaluation(
        premise_status="nuanced_inflection",
        evaluation_summary="En 2026 el gasto disminuyó de enero ($57,1M) a mayo ($49,4M) y luego aumentó fuertemente de mayo ($49,4M) a julio ($56,9M).",
        baseline_period="2026-05-01",
        peak_period="2026-07-01",
        needs_clarification=True,
        clarification_prompt="Durante 2026 el gasto cayó entre enero y mayo, y luego repuntó en invierno (mayo a julio). ¿Qué período deseas analizar?",
        candidate_options=[
            "El repunte invernal de mayo a julio 2026",
            "La disminución de enero a mayo 2026",
        ],
    ).model_dump_json()

    eval_resumed_json = TrajectoryEvaluation(
        premise_status="validated",
        evaluation_summary="Análisis enfocado en el repunte invernal desde mayo 2026 ($49,4M) hasta julio 2026 ($56,9M).",
        baseline_period="2026-05-01",
        peak_period="2026-07-01",
        needs_clarification=False,
    ).model_dump_json()

    delta_sql = """
    WITH baseline AS (
        SELECT s.category_name, SUM(s.calculated_subtotal) AS baseline_amount
        FROM expense_sections s
        JOIN expense_reports r ON s.report_id = r.id
        WHERE r.period_date = '2026-05-01'
        GROUP BY s.category_name
    ),
    peak AS (
        SELECT s.category_name, SUM(s.calculated_subtotal) AS peak_amount
        FROM expense_sections s
        JOIN expense_reports r ON s.report_id = r.id
        WHERE r.period_date = '2026-07-01'
        GROUP BY s.category_name
    )
    SELECT 
        COALESCE(p.category_name, b.category_name) AS category_name,
        COALESCE(b.baseline_amount, 0) AS baseline_amount,
        COALESCE(p.peak_amount, 0) AS peak_amount,
        (COALESCE(p.peak_amount, 0) - COALESCE(b.baseline_amount, 0)) AS delta_clp
    FROM peak p
    LEFT JOIN baseline b ON p.category_name = b.category_name
    ORDER BY delta_clp DESC
    """
    sql_output_json = json.dumps(
        {
            "reasoning": "Calculate net delta per category between May 2026 trough and July 2026 winter peak.",
            "sql_query": delta_sql,
        }
    )

    synth_markdown = (
        "### Análisis de Variación del Gasto Común (Mayo a Julio 2026)\n\n"
        "Durante 2026 el gasto común cayó de enero ($57.063.002 CLP) a su mínimo en mayo ($49.387.905 CLP), "
        "para luego registrar un repunte invernal hasta $56.907.702 CLP en julio (+$7.519.797 CLP).\n\n"
        "Los principales causantes de este aumento fueron:\n"
        "1. **Consumos**: +$3.549.478 CLP (47,2% del incremento total).\n"
        "2. **Seguros**: +$3.063.176 CLP.\n"
        "3. **Reparaciones y Repuestos**: +$1.203.328 CLP.\n"
        "*Nota: Aunque Mantenciones y Servicios tiene un monto alto, su variación fue de solo +$435.535 CLP.*"
    )

    # Mock Gemini responses in sequence:
    # Turn 1: [1] Planner -> [2] Trajectory Evaluator (triggers interrupt)
    # Turn 2 (Resume): [3] Trajectory Evaluator (validated) -> [4] SQL Coder -> [5] Synthesizer
    responses = [
        MagicMock(text=plan_json),
        MagicMock(text=eval_pause_json),
        MagicMock(text=eval_resumed_json),
        MagicMock(text=sql_output_json),
        MagicMock(text=synth_markdown),
    ]

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = responses

    with patch("analytics.nodes.get_gemini_client", return_value=mock_client):
        # Pass 1: Initial ambiguous query pauses for clarification
        res_paused = run_analysis("¿A qué se debe el aumento del gasto en el 2026?")
        assert res_paused.needs_clarification is True
        assert res_paused.thread_id is not None
        assert len(res_paused.candidate_options) == 2
        assert "mayo a julio" in res_paused.candidate_options[0].lower()

        # Pass 2: User selects option "1" -> resumes from checkpoint and executes SQLite delta query
        res_final = run_analysis(
            query="¿A qué se debe el aumento del gasto en el 2026?",
            thread_id=res_paused.thread_id,
            resume_choice="1",
        )
        assert res_final.needs_clarification is False
        assert res_final.data is not None
        assert len(res_final.data) > 0

        # Verify actual SQLite delta calculation ranks 'consumos' #1 in delta_clp, NOT 'mantenciones y servicios'
        top_driver = res_final.data[0]
        assert top_driver["category_name"] == "consumos"
        assert top_driver["delta_clp"] == 3525456

        # Verify 'mantenciones y servicios' had a much smaller delta than 'consumos'
        by_cat = {r["category_name"]: r["delta_clp"] for r in res_final.data}
        assert by_cat["consumos"] > by_cat["mantenciones y servicios"]
        assert "Consumos" in res_final.summary


def test_api_route_returns_clarification_payload():
    """Verify POST /api/v1/analytics/query returns clarification fields when paused."""
    client = TestClient(app)
    mock_paused = AnalyticsResult(
        summary="Observo dos fases en 2026. ¿Cuál deseas analizar?",
        is_authorized=True,
        needs_clarification=True,
        clarification_prompt="Observo dos fases en 2026. ¿Cuál deseas analizar?",
        candidate_options=["Repunte mayo a julio", "Caída enero a mayo"],
        thread_id="thread_test123",
        latency_seconds=0.8,
    )

    with patch("analytics.workflow.run_analysis", return_value=mock_paused):
        resp = client.post(
            "/api/v1/analytics/query",
            json={"query": "¿A qué se debe el aumento en 2026?"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["needs_clarification"] is True
        assert body["thread_id"] == "thread_test123"
        assert len(body["candidate_options"]) == 2


# ============================================================================
# Task 4.1 (Multi-Analysis & Period Citations): End-to-End 3-Query Test
# ============================================================================


def test_end_to_end_multi_analysis_mom_yoy_and_variance_with_period_citations(gateway_bridge):
    """Verify compound prompt executes 3 SQL queries (MoM + YoY + Category Variance) through the
    HTTP Data Gateway and returns tagged datasets with explicit period provenance and [Mes Año] citations.
    """
    plan_json = IntentPlan(
        is_authorized_query=True,
        analysis_type="variance_driver",
        temporal_resolution="monthly",
        sub_analyses=[
            "Revisión mes a mes (MoM) de los gastos comunes en 2026",
            "Comparación interanual (YoY) entre 2025 y 2026 para meses equivalentes (julio a septiembre)",
            "Causantes de variación por categoría entre el mínimo de mayo 2026 y el pico de julio 2026",
        ],
        analysis_goal="Realizar revisión MoM 2026, comparación YoY 2025 vs 2026 y desglose de variación Mayo-Julio 2026",
        relevant_tables=["expense_reports", "expense_sections"],
        time_scope="2025-2026",
        requires_visualization=False,
    ).model_dump_json()

    eval_json = TrajectoryEvaluation(
        premise_status="validated",
        evaluation_summary=(
            "En [Julio 2025] el gasto fue de $48.996.124 CLP frente a $56.907.702 CLP en [Julio 2026]. "
            "Dentro de 2026, el gasto bajó de $57.063.002 CLP [Enero 2026] a $49.387.905 CLP [Mayo 2026] "
            "y subió a $56.907.702 CLP [Julio 2026]."
        ),
        baseline_period="2026-05-01",
        peak_period="2026-07-01",
        needs_clarification=False,
    ).model_dump_json()

    mom_sql = """
    SELECT 
        r.period_date,
        LAG(r.period_date) OVER (ORDER BY r.period_date) AS prev_period_date,
        r.calculated_grand_total AS current_total,
        LAG(r.calculated_grand_total) OVER (ORDER BY r.period_date) AS prev_total,
        (r.calculated_grand_total - LAG(r.calculated_grand_total) OVER (ORDER BY r.period_date)) AS delta_clp
    FROM expense_reports r
    WHERE r.period_year = 2026
    ORDER BY r.period_date ASC
    """

    yoy_sql = """
    SELECT 
        r26.period_month,
        r25.period_date AS baseline_period,
        r25.calculated_grand_total AS total_2025,
        r26.period_date AS comparison_period,
        r26.calculated_grand_total AS total_2026,
        (r26.calculated_grand_total - r25.calculated_grand_total) AS delta_clp
    FROM expense_reports r26
    JOIN expense_reports r25 ON r26.period_month = r25.period_month AND r25.period_year = 2025
    WHERE r26.period_year = 2026
    ORDER BY r26.period_month ASC
    """

    variance_sql = """
    WITH baseline AS (
        SELECT s.category_name, SUM(s.calculated_subtotal) AS baseline_amount
        FROM expense_sections s
        JOIN expense_reports r ON s.report_id = r.id
        WHERE r.period_date = '2026-05-01'
        GROUP BY s.category_name
    ),
    peak AS (
        SELECT s.category_name, SUM(s.calculated_subtotal) AS peak_amount
        FROM expense_sections s
        JOIN expense_reports r ON s.report_id = r.id
        WHERE r.period_date = '2026-07-01'
        GROUP BY s.category_name
    )
    SELECT 
        COALESCE(p.category_name, b.category_name) AS category_name,
        '2026-05-01' AS baseline_period,
        COALESCE(b.baseline_amount, 0) AS baseline_amount,
        '2026-07-01' AS peak_period,
        COALESCE(p.peak_amount, 0) AS peak_amount,
        (COALESCE(p.peak_amount, 0) - COALESCE(b.baseline_amount, 0)) AS delta_clp
    FROM peak p
    LEFT JOIN baseline b ON p.category_name = b.category_name
    ORDER BY delta_clp DESC
    """

    multi_sql_output_json = json.dumps(
        {
            "reasoning": "Generate 3 dedicated queries for MoM 2026, YoY 2025 vs 2026, and May-July 2026 category variance.",
            "queries": [
                {"query_id": "mom_2026", "title": "Revisión Mes a Mes (MoM) 2026", "sql_query": mom_sql},
                {"query_id": "yoy_2025_2026", "title": "Comparación Interanual (YoY) 2025 vs 2026", "sql_query": yoy_sql},
                {"query_id": "variance_may_jul_2026", "title": "Causantes de Variación Mayo vs Julio 2026", "sql_query": variance_sql},
            ],
            "sql_query": mom_sql,
        }
    )

    synth_markdown = (
        "### 1. Revisión Mes a Mes (MoM) [Enero – Septiembre 2026]\n"
        "El gasto común inició en **$57.063.002 CLP [Enero 2026]**, descendió a su valle en "
        "**$49.387.905 CLP [Mayo 2026]**, y repuntó a **$56.907.702 CLP [Julio 2026]** (+$7.519.797 CLP).\n\n"
        "### 2. Comparación Interanual (YoY) [Julio – Septiembre 2025 vs 2026]\n"
        "- **Julio**: **$48.996.124 CLP [Julio 2025]** vs **$56.907.702 CLP [Julio 2026]** (+$7.911.578 CLP).\n\n"
        "### 3. Causantes de Variación por Categoría ([Mayo 2026] vs [Julio 2026])\n"
        "- **Consumos**: pasó de **$6.727.492 CLP [Mayo 2026]** a **$10.252.948 CLP [Julio 2026]** (+$3.525.456 CLP)."
    )

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        MagicMock(text=plan_json),
        MagicMock(text=eval_json),
        MagicMock(text=multi_sql_output_json),
        MagicMock(text=synth_markdown),
    ]

    with patch("analytics.nodes.get_gemini_client", return_value=mock_client):
        result = run_analysis(
            "Haz una revisión mes a mes del 2026, compáralo YoY con 2025 y dime qué impulsó el alza de mayo a julio"
        )

    assert result.needs_clarification is False
    assert result.data is not None
    assert len(result.data) == 3

    # Verify each named dataset returned real rows with period provenance columns
    q_ids = [item["query_id"] for item in result.data]
    assert q_ids == ["mom_2026", "yoy_2025_2026", "variance_may_jul_2026"]

    mom_rows = result.data[0]["rows"]
    assert len(mom_rows) >= 8
    assert "period_date" in mom_rows[0]

    yoy_rows = result.data[1]["rows"]
    assert len(yoy_rows) >= 2
    assert yoy_rows[0]["baseline_period"].startswith("2025-")
    assert yoy_rows[0]["comparison_period"].startswith("2026-")

    var_rows = result.data[2]["rows"]
    assert var_rows[0]["category_name"] == "consumos"
    assert var_rows[0]["baseline_period"] == "2026-05-01"
    assert var_rows[0]["peak_period"] == "2026-07-01"

    # Verify explicit [Mes Año] citations in synthesized report
    assert "[Enero 2026]" in result.summary
    assert "[Mayo 2026]" in result.summary
    assert "[Julio 2026]" in result.summary
    assert "[Julio 2025]" in result.summary
