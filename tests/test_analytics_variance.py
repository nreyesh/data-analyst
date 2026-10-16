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


def test_trajectory_inspector_fetches_2026_chronological_totals():
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


def test_end_to_end_variance_loop_identifies_consumos_over_static_mantenciones():
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
