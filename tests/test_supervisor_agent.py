"""Unit and integration tests for the Conversational Supervisor Agent."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
import pytest

from agent.state import FocusContext, UserContext
from agent.supervisor import MAX_EPHEMERAL_MESSAGES, run_supervisor_turn
from analytics.state import AnalyticsResult
from backend.app.main import app

UNIT_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "504b_2026_08_expected.json"
REPORT_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "2026_9_expected.json"


@pytest.fixture
def backend_client():
    """FastAPI TestClient with unit and building fixtures pre-loaded."""
    client = TestClient(app)
    if UNIT_FIXTURE_PATH.exists():
        payload = json.loads(UNIT_FIXTURE_PATH.read_text(encoding="utf-8"))
        client.post("/api/v1/unit-bills", json=payload)
    if REPORT_FIXTURE_PATH.exists():
        payload = json.loads(REPORT_FIXTURE_PATH.read_text(encoding="utf-8"))
        client.post("/api/v1/reports", json=payload)
    return client


@pytest.fixture
def resident_b504() -> UserContext:
    return UserContext(
        uid="uid_res_504b",
        email="residente504b@condominio.cl",
        role="resident",
        unit_code="B-504",
    )


@pytest.fixture
def admin_user() -> UserContext:
    return UserContext(
        uid="uid_admin_001",
        email="admin@condominio.cl",
        role="admin",
        unit_code=None,
    )


def test_supervisor_routes_unit_fastpath(resident_b504, backend_client):
    """Verify unit balance lookup routes to lookup_unit_bill fast-path without calling analytics workflow."""
    mock_workflow = MagicMock()
    turn, msgs, focus = run_supervisor_turn(
        user_message="¿Cuánto debo pagar en agosto 2026?",
        user_context=resident_b504,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert turn.route == "lookup_unit_bill"
    assert "B-504" in turn.reply
    assert "2026-08-01" in turn.reply
    assert "None:" not in turn.reply
    assert "= **$0 CLP**" not in turn.reply
    assert "Gasto Común" in turn.reply
    assert focus.last_period == "2026-08-01"
    assert focus.last_unit == "B-504"
    assert len(msgs) == 2
    mock_workflow.assert_not_called()


def test_supervisor_routes_building_fastpath_and_anaphora_followup(admin_user, backend_client):
    """Verify building total routes to lookup_building_summary and resolves '¿Y el mes anterior?'."""
    # Turn 1: September 2026 building report
    turn1, msgs1, focus1 = run_supervisor_turn(
        user_message="¿Cuál fue el gasto común total del edificio en septiembre 2026?",
        user_context=admin_user,
        http_client=backend_client,
    )
    assert turn1.route == "lookup_building_summary"
    assert "2026-09-01" in turn1.reply
    assert focus1.last_period == "2026-09-01"
    assert focus1.last_route == "lookup_building_summary"

    # Turn 2: Anaphora follow-up "¿Y el mes anterior?" -> resolves to 2026-08-01
    turn2, msgs2, focus2 = run_supervisor_turn(
        user_message="¿Y el mes anterior?",
        user_context=admin_user,
        messages=msgs1,
        focus=focus1,
        http_client=backend_client,
    )
    assert turn2.route == "lookup_building_summary"
    assert "2026-08-01" in turn2.step_detail
    assert len(msgs2) == 4


def test_supervisor_routes_evolution_to_deep_analytics(resident_b504, backend_client):
    """Verify '¿Cómo ha evolucionado el gasto?' routes to run_deep_financial_analysis with chart path."""
    mock_workflow = MagicMock(
        return_value=AnalyticsResult(
            summary="El gasto evolucionó al alza en [Agosto 2026].",
            is_authorized=True,
            needs_clarification=False,
            chart_file_path="data/output/charts/trend_test.png",
            sql_query="SELECT period_date, calculated_total_month FROM unit_bills;",
        )
    )
    turn, msgs, focus = run_supervisor_turn(
        user_message="¿Cómo ha evolucionado mi gasto común?",
        user_context=resident_b504,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert turn.route == "run_deep_financial_analysis"
    assert turn.chart_file_path == "data/output/charts/trend_test.png"
    assert "Agosto 2026" in turn.reply
    mock_workflow.assert_called_once()
    call_kwargs = mock_workflow.call_args.kwargs
    assert call_kwargs["target_unit"] == "B-504"


def test_supervisor_clarification_interrupt_and_resume(admin_user, backend_client):
    """Verify clarification pause stores thread_id in focus and resumes on button click."""
    mock_workflow = MagicMock(
        side_effect=[
            AnalyticsResult(
                summary="¿A qué torre corresponde el departamento 504?",
                is_authorized=True,
                needs_clarification=True,
                clarification_prompt="¿A qué torre corresponde el departamento 504?",
                candidate_options=["Torre A (A-504)", "Torre B (B-504)"],
                thread_id="thread_pause_123",
            ),
            AnalyticsResult(
                summary="El departamento B-504 subió por mantención de ascensores en [Agosto 2026].",
                is_authorized=True,
                needs_clarification=False,
                thread_id="thread_pause_123",
            ),
        ]
    )

    # Turn 1: Triggers clarification interrupt
    turn1, msgs1, focus1 = run_supervisor_turn(
        user_message="¿Por qué subió el gasto del depto 504?",
        user_context=admin_user,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert turn1.needs_clarification is True
    assert turn1.candidate_options == ["Torre A (A-504)", "Torre B (B-504)"]
    assert focus1.active_analytics_thread_id == "thread_pause_123"

    # Turn 2: User clicks "Torre B (B-504)" button
    turn2, msgs2, focus2 = run_supervisor_turn(
        user_message="Torre B (B-504)",
        user_context=admin_user,
        messages=msgs1,
        focus=focus1,
        clarification_choice="Torre B (B-504)",
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert turn2.needs_clarification is False
    assert "B-504 subió por mantención" in turn2.reply
    assert focus2.active_analytics_thread_id is None
    assert mock_workflow.call_args_list[1].kwargs["thread_id"] == "thread_pause_123"
    assert mock_workflow.call_args_list[1].kwargs["resume_choice"] == "Torre B (B-504)"


def test_supervisor_routes_system_status_and_trims_ephemeral_window(admin_user, backend_client):
    """Verify system status routing for admin and 8-message ephemeral window cap."""
    seed_messages = [
        HumanMessage(content=f"Pregunta {i}") if i % 2 == 0 else AIMessage(content=f"Respuesta {i}")
        for i in range(10)
    ]
    turn, msgs, focus = run_supervisor_turn(
        user_message="¿Qué meses están cargados y cuál es el estado del spool?",
        user_context=admin_user,
        messages=seed_messages,
        http_client=backend_client,
    )
    assert turn.route == "check_system_status"
    assert "Diagnóstico del Sistema" in turn.reply
    assert len(msgs) == MAX_EPHEMERAL_MESSAGES


def test_supervisor_natural_spanish_and_typos_regression(resident_b504, admin_user, backend_client):
    """Verify real-world natural Spanish queries and typos route properly instead of returning greeting."""
    mock_workflow = MagicMock(
        return_value=AnalyticsResult(
            summary="El alza de este año en la unidad B-504 se explica por mantenciones en [Agosto 2026].",
            is_authorized=True,
            needs_clarification=False,
        )
    )

    # 1. "dame el detalle del gasto comun de este mes" (Resident -> lookup_unit_bill)
    t1, _, _ = run_supervisor_turn(
        user_message="dame el detalle del gasto comun de este mes",
        user_context=resident_b504,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t1.route == "lookup_unit_bill"
    assert "B-504" in t1.reply
    assert "¡Hola! Soy el asistente" not in t1.reply

    # 2. "dame el detalle del gasto comun de este mes" (Admin -> lookup_building_summary)
    t1_admin, _, _ = run_supervisor_turn(
        user_message="dame el detalle del gasto comun de este mes",
        user_context=admin_user,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t1_admin.route == "lookup_building_summary"
    assert "Resumen de Gastos Comunes del Edificio" in t1_admin.reply

    # 3. "cuel fue el gasto comun de este mes" (with typo 'cuel' -> lookup_unit_bill for resident)
    t2, _, _ = run_supervisor_turn(
        user_message="cuel fue el gasto comun de este mes",
        user_context=resident_b504,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t2.route == "lookup_unit_bill"
    assert "B-504" in t2.reply
    assert "¡Hola! Soy el asistente" not in t2.reply

    # 4. "porque el gasto comun ha subi tanto este año" (with 'porque' + 'ha subi' -> run_deep_financial_analysis)
    t3, _, _ = run_supervisor_turn(
        user_message="porque el gasto comun ha subi tanto este año",
        user_context=resident_b504,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t3.route == "run_deep_financial_analysis"
    assert "B-504" in t3.reply
    mock_workflow.assert_called_once()
    assert mock_workflow.call_args.kwargs["target_unit"] == "B-504"

    # 5. Pure greeting ("hola") still returns direct_response
    t_greet, _, _ = run_supervisor_turn(
        user_message="hola",
        user_context=resident_b504,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t_greet.route == "direct_response"
    assert "¡Hola! Soy el asistente" in t_greet.reply


def test_supervisor_building_variance_and_followup_concept_fondo(resident_b504, backend_client):
    """Verify resident asking about building rise ('edificio de abril a septiembre') and follow-up ('detalla que vario entre estas fechas para el concepto fondo') both route to run_deep_financial_analysis with building scope (target_unit=None)."""
    mock_workflow = MagicMock(
        side_effect=[
            AnalyticsResult(
                summary="El gasto del edificio aumentó entre [Abril 2026] y [Septiembre 2026].",
                is_authorized=True,
                needs_clarification=False,
            ),
            AnalyticsResult(
                summary="El concepto Fondos varió por la incorporación del Fondo Impermeabilización en [Mayo 2026].",
                is_authorized=True,
                needs_clarification=False,
            ),
        ]
    )

    # Turn 1: Resident asks why building expenses rose from April to September
    t1, msgs1, focus1 = run_supervisor_turn(
        user_message="si, pero no solo de mayo. poruqe a subido tanto el gasto comun del edificio de abril a septiembre.",
        user_context=resident_b504,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t1.route == "run_deep_financial_analysis"
    assert focus1.last_domain == "building"
    assert focus1.last_unit is None
    assert mock_workflow.call_args_list[0].kwargs["target_unit"] is None

    # Turn 2: Follow-up "detalla que vario entre estas fechas para el concepto fondo."
    t2, msgs2, focus2 = run_supervisor_turn(
        user_message="detalla que vario entre estas fechas para el concepto fondo.",
        user_context=resident_b504,
        messages=msgs1,
        focus=focus1,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t2.route == "run_deep_financial_analysis"
    assert focus2.last_domain == "building"
    assert mock_workflow.call_args_list[1].kwargs["target_unit"] is None
    assert "edificio" in mock_workflow.call_args_list[1].kwargs["query"]
