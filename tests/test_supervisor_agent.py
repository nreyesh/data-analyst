"""Unit and integration tests for the Conversational Supervisor Agent."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
import pytest

from agent.state import FocusContext, SupervisorDecision, UserContext
from agent.supervisor import MAX_EPHEMERAL_MESSAGES, run_supervisor_turn
from analytics.state import AnalyticsResult
from backend.app.main import app

UNIT_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "504b_2026_08_expected.json"
REPORT_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "2026_9_expected.json"


@pytest.fixture(autouse=True)
def isolate_offline_llm_env(monkeypatch):
    """Ensure supervisor tests run offline deterministically unless a mock llm_client is passed."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    from analytics.config import get_analytics_settings
    monkeypatch.setattr(
        "agent.supervisor.get_analytics_settings",
        lambda: MagicMock(gemini_api_key=""),
    )


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


def test_supervisor_llm_first_routing(resident_b504, backend_client):
    """Verify supervisor_router_node uses Gemini LLM (SupervisorDecision) as primary router when llm_client is provided."""
    mock_llm = MagicMock()
    llm_decision = SupervisorDecision(
        route="lookup_unit_bill",
        reasoning="LLM classified explicit unit bill lookup for resident.",
        resolved_query="¿Cuánto debo pagar en mi departamento en agosto 2026?",
        knowledge_domain="unit",
        unit_code="B-504",
        period_date="2026-08-01",
    )
    mock_llm.models.generate_content.return_value = MagicMock(
        text=llm_decision.model_dump_json()
    )

    turn, msgs, focus = run_supervisor_turn(
        user_message="¿Cuánto debo pagar en agosto 2026?",
        user_context=resident_b504,
        llm_client=mock_llm,
        http_client=backend_client,
    )
    assert mock_llm.models.generate_content.called
    assert turn.route == "lookup_unit_bill"
    assert "B-504" in turn.reply
    assert focus.last_domain == "unit"
    assert focus.last_unit == "B-504"
    assert focus.last_period == "2026-08-01"


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
    """Verify building total routes to lookup_building_summary and resolves '¿Y el mes anterior?' inheriting FocusContext."""
    # Turn 1: September 2026 building report
    turn1, msgs1, focus1 = run_supervisor_turn(
        user_message="¿Cuál fue el gasto común total del edificio en septiembre 2026?",
        user_context=admin_user,
        http_client=backend_client,
    )
    assert turn1.route == "lookup_building_summary"
    assert "2026-09-01" in turn1.reply
    assert focus1.last_domain == "building"
    assert focus1.last_period == "2026-09-01"
    assert focus1.last_route == "lookup_building_summary"

    # Turn 2: Anaphora follow-up "¿Y el mes anterior?" -> inherits building domain and resolves to 2026-08-01
    turn2, msgs2, focus2 = run_supervisor_turn(
        user_message="¿Y el mes anterior?",
        user_context=admin_user,
        messages=msgs1,
        focus=focus1,
        http_client=backend_client,
    )
    assert turn2.route == "lookup_building_summary"
    assert turn2.needs_clarification is False
    assert "2026-08-01" in turn2.step_detail
    assert len(msgs2) == 4


def test_supervisor_routes_evolution_to_deep_analytics(resident_b504, backend_client):
    """Verify '¿Cómo ha evolucionado mi gasto común?' routes to run_deep_financial_analysis with chart path."""
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
    assert call_kwargs["knowledge_domain"] == "unit"


def test_turn1_scope_clarification_for_resident_and_cross_domain_selection(
    resident_b504, backend_client
):
    """Verify Turn 1 ambiguous query from resident asks 3 options (unit, building, both) and executes cross_domain when selected."""
    mock_workflow = MagicMock(
        return_value=AnalyticsResult(
            summary="El alza en B-504 se explica por el Fondo Impermeabilización del edificio [Agosto 2026].",
            is_authorized=True,
            needs_clarification=False,
        )
    )

    # Turn 1 (Cold Start): Resident asks "¿Por qué subió tanto el gasto común este año?" (no 'mi' and no 'edificio')
    turn1, msgs1, focus1 = run_supervisor_turn(
        user_message="porque el gasto comun ha subi tanto este año",
        user_context=resident_b504,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert turn1.route == "clarify_intent"
    assert turn1.needs_clarification is True
    assert len(turn1.candidate_options) == 3
    assert "Mi departamento (B-504)" in turn1.candidate_options
    assert "Gasto general del edificio" in turn1.candidate_options
    assert "Ambos (impacto del edificio en mi unidad B-504)" in turn1.candidate_options
    mock_workflow.assert_not_called()

    # Turn 2: Resident selects "Ambos (impacto del edificio en mi unidad B-504)"
    turn2, msgs2, focus2 = run_supervisor_turn(
        user_message="Ambos (impacto del edificio en mi unidad B-504)",
        user_context=resident_b504,
        messages=msgs1,
        focus=focus1,
        clarification_choice="Ambos (impacto del edificio en mi unidad B-504)",
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert turn2.route == "run_deep_financial_analysis"
    assert turn2.needs_clarification is False
    assert focus2.last_domain == "cross_domain"
    assert focus2.last_unit == "B-504"
    mock_workflow.assert_called_once()
    assert mock_workflow.call_args.kwargs["target_unit"] == "B-504"
    assert mock_workflow.call_args.kwargs["knowledge_domain"] == "cross_domain"


def test_turn1_scope_clarification_for_admin_and_unit_code_prompt(admin_user, backend_client):
    """Verify Turn 1 ambiguous query from Admin offers 2 options, and selecting specific unit prompts to type unit code."""
    mock_workflow = MagicMock()

    # Turn 1 (Cold Start): Admin asks "dame el detalle del gasto comun de agosto 2026"
    t1, m1, f1 = run_supervisor_turn(
        user_message="dame el detalle del gasto comun de agosto 2026",
        user_context=admin_user,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t1.route == "clarify_intent"
    assert t1.needs_clarification is True
    assert t1.candidate_options == [
        "Gasto general del edificio",
        "Consultar un departamento específico",
    ]

    # Turn 2: Admin clicks "Consultar un departamento específico" -> gets conversational prompt with no buttons
    t2, m2, f2 = run_supervisor_turn(
        user_message="Consultar un departamento específico",
        user_context=admin_user,
        messages=m1,
        focus=f1,
        clarification_choice="Consultar un departamento específico",
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t2.route == "clarify_intent"
    assert t2.needs_clarification is True
    assert t2.candidate_options == []
    assert "código del departamento" in t2.reply

    # Turn 3: Admin types "B-504" -> executes lookup_unit_bill for B-504 in 2026-08-01!
    t3, m3, f3 = run_supervisor_turn(
        user_message="B-504",
        user_context=admin_user,
        messages=m2,
        focus=f2,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t3.route == "lookup_unit_bill"
    assert t3.needs_clarification is False
    assert "B-504" in t3.reply
    assert "2026-08-01" in t3.reply
    assert f3.last_domain == "unit"
    assert f3.last_unit == "B-504"


def test_supervisor_missing_tower_db_clarification_and_resume(admin_user, backend_client):
    """Verify Supervisor resolves missing tower ('depto 504') via DB lookup into clickable buttons before running tool."""
    mock_workflow = MagicMock(
        return_value=AnalyticsResult(
            summary="El departamento B-504 subió por mantención de ascensores en [Agosto 2026].",
            is_authorized=True,
            needs_clarification=False,
        )
    )

    with patch(
        "agent.supervisor._lookup_towers_for_unit_number",
        return_value=["A", "B"],
    ):
        # Turn 1: Supervisor intercepts bare unit 504 and asks which tower
        turn1, msgs1, focus1 = run_supervisor_turn(
            user_message="¿Por qué subió el gasto del depto 504?",
            user_context=admin_user,
            http_client=backend_client,
            workflow_fn=mock_workflow,
        )
        assert turn1.route == "clarify_intent"
        assert turn1.needs_clarification is True
        assert turn1.candidate_options == ["Torre A (A-504)", "Torre B (B-504)"]
        mock_workflow.assert_not_called()

        # Turn 2: User clicks "Torre B (B-504)" -> Supervisor calls analytics with B-504
        turn2, msgs2, focus2 = run_supervisor_turn(
            user_message="Torre B (B-504)",
            user_context=admin_user,
            messages=msgs1,
            focus=focus1,
            clarification_choice="Torre B (B-504)",
            http_client=backend_client,
            workflow_fn=mock_workflow,
        )
        assert turn2.route == "run_deep_financial_analysis"
        assert turn2.needs_clarification is False
        assert "B-504 subió por mantención" in turn2.reply
        mock_workflow.assert_called_once()
        assert mock_workflow.call_args.kwargs["target_unit"] == "B-504"
        assert mock_workflow.call_args.kwargs["knowledge_domain"] == "unit"


def test_supervisor_hop1_analytics_trend_clarification_and_topic_switch_escape_hatch(
    admin_user, backend_client
):
    """Verify Analytics Hop 1 trend verification pauses/resumes properly AND allows switching topics if user ignores buttons."""
    mock_workflow = MagicMock(
        side_effect=[
            AnalyticsResult(
                summary="En mayo el gasto del edificio bajó (-21%). El alza ocurrió en julio (+39%). ¿Qué período deseas analizar?",
                is_authorized=True,
                needs_clarification=True,
                clarification_prompt="En mayo el gasto del edificio bajó (-21%). El alza ocurrió en julio (+39%). ¿Qué período deseas analizar?",
                candidate_options=[
                    "1. Analizar la baja de mayo 2026",
                    "2. Analizar el alza de julio 2026",
                ],
                thread_id="thread_hop1_999",
            ),
            AnalyticsResult(
                summary="El alza de julio 2026 se explica por reparaciones de caldera [Julio 2026].",
                is_authorized=True,
                needs_clarification=False,
                thread_id="thread_hop1_999",
            ),
        ]
    )

    # Turn 1: Unambiguous building variance question triggers Hop 1 trend verification inside Analytics
    t1, m1, f1 = run_supervisor_turn(
        user_message="¿Por qué subió el gasto operacional del edificio en mayo 2026?",
        user_context=admin_user,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t1.route == "run_deep_financial_analysis"
    assert t1.needs_clarification is True
    assert f1.active_analytics_thread_id == "thread_hop1_999"

    # Case A: User clicks button "2. Analizar el alza de julio 2026" -> Resumes thread_hop1_999!
    t2_resume, _, f2_resume = run_supervisor_turn(
        user_message="2. Analizar el alza de julio 2026",
        user_context=admin_user,
        messages=m1,
        focus=f1,
        clarification_choice="2. Analizar el alza de julio 2026",
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t2_resume.route == "run_deep_financial_analysis"
    assert t2_resume.needs_clarification is False
    assert f2_resume.active_analytics_thread_id is None
    assert mock_workflow.call_args_list[1].kwargs["thread_id"] == "thread_hop1_999"
    assert (
        mock_workflow.call_args_list[1].kwargs["resume_choice"]
        == "2. Analizar el alza de julio 2026"
    )

    # Case B (Escape Hatch): From f1 (where thread_hop1_999 is paused), user ignores buttons and asks system status
    t2_switch, _, f2_switch = run_supervisor_turn(
        user_message="¿Cuál es el estado del spool?",
        user_context=admin_user,
        messages=m1,
        focus=f1,
        clarification_choice=None,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t2_switch.route == "check_system_status"
    assert f2_switch.active_analytics_thread_id is None


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


def test_supervisor_turn2_inherits_focus_context_and_greeting(resident_b504, backend_client):
    """Verify Turn 2 inherits FocusContext without re-asking clarification, while pure greeting returns direct_response."""
    mock_workflow = MagicMock(
        return_value=AnalyticsResult(
            summary="El alza de este año en la unidad B-504 se explica por mantenciones en [Agosto 2026].",
            is_authorized=True,
            needs_clarification=False,
        )
    )

    # 1. Explicit unit query on Turn 1 establishes FocusContext(last_domain="unit", last_unit="B-504")
    t1, m1, f1 = run_supervisor_turn(
        user_message="dame el detalle de mi gasto comun de este mes",
        user_context=resident_b504,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t1.route == "lookup_unit_bill"
    assert f1.last_domain == "unit"
    assert f1.last_unit == "B-504"

    # 2. Turn 2 ambiguous phrasing ("porque el gasto comun ha subi tanto este año") inherits f1 (unit B-504) without asking clarification!
    t2, m2, f2 = run_supervisor_turn(
        user_message="porque el gasto comun ha subi tanto este año",
        user_context=resident_b504,
        messages=m1,
        focus=f1,
        http_client=backend_client,
        workflow_fn=mock_workflow,
    )
    assert t2.route == "run_deep_financial_analysis"
    assert t2.needs_clarification is False
    assert mock_workflow.call_args.kwargs["target_unit"] == "B-504"
    assert mock_workflow.call_args.kwargs["knowledge_domain"] == "unit"

    # 3. Pure greeting ("hola") returns direct_response
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
    assert mock_workflow.call_args_list[0].kwargs["knowledge_domain"] == "building"

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
    assert mock_workflow.call_args_list[1].kwargs["knowledge_domain"] == "building"
    assert "edificio" in mock_workflow.call_args_list[1].kwargs["query"]
