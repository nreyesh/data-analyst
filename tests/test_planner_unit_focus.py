"""Unit tests for Unit-First Planner Focus Guard."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from analytics.nodes import planner_node
from analytics.state import DataAnalystState, IntentPlan


def test_planner_unit_first_focus_on_apartment_inquiry():
    """Verify that any query referencing an apartment defaults to single-analysis unit domain."""
    mock_client = MagicMock()
    # Simulate LLM returning cross_domain with 2 sub-analyses mistakenly
    mock_resp = MagicMock()
    mock_resp.text = IntentPlan(
        is_authorized_query=True,
        knowledge_domain="cross_domain",
        analysis_goal="Evolución gasto común depto 506b",
        sub_analyses=[
            "Evolución macro de gastos operacionales del edificio",
            "Evolución y variación de la unidad B-506",
        ],
        target_unit="506B",
        unit_tower="B",
        unit_number=506,
    ).model_dump_json()
    mock_client.models.generate_content.return_value = mock_resp

    state: DataAnalystState = {
        "user_query": "muestrame la evolucion del gasto comun para el depto 506b"
    }
    result = planner_node(state, client=mock_client)
    plan = result["intent_plan"]

    assert plan.knowledge_domain == "unit"
    assert plan.target_unit == "B-506"
    assert len(plan.sub_analyses) == 1
    assert "506" in plan.sub_analyses[0] or "unidad" in plan.sub_analyses[0].lower()


def test_planner_preserves_cross_domain_when_explicitly_requested():
    """Verify that an explicit inquiry about the whole building comparison preserves cross_domain."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = IntentPlan(
        is_authorized_query=True,
        knowledge_domain="cross_domain",
        analysis_goal="Comparación depto 506b vs edificio",
        sub_analyses=[
            "Desglose de boleta unidad B-506",
            "Atribución de causas macro en el gasto general del edificio",
        ],
        target_unit="B-506",
        unit_tower="B",
        unit_number=506,
    ).model_dump_json()
    mock_client.models.generate_content.return_value = mock_resp

    state: DataAnalystState = {
        "user_query": "¿Por qué subió el gasto de mi depto 506b en relación al edificio?"
    }
    result = planner_node(state, client=mock_client)
    plan = result["intent_plan"]

    assert plan.knowledge_domain == "cross_domain"
    assert len(plan.sub_analyses) == 2


def test_planner_honors_pre_verified_cross_domain_from_supervisor():
    """Verify planner_node honors knowledge_domain='cross_domain' passed by the Supervisor even when LLM returns 1 sub-analysis."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = IntentPlan(
        is_authorized_query=True,
        knowledge_domain="unit",
        analysis_goal="Analizar alza de gasto común en unidad B-504",
        sub_analyses=["Variación mensual de la unidad B-504"],
        target_unit="B-504",
        unit_tower="B",
        unit_number=504,
    ).model_dump_json()
    mock_client.models.generate_content.return_value = mock_resp

    state: DataAnalystState = {
        "user_query": "¿Por qué subió el gasto común este año? (Analizar impacto del edificio en la unidad B-504)",
        "target_unit": "B-504",
        "knowledge_domain": "cross_domain",
    }
    result = planner_node(state, client=mock_client)
    plan = result["intent_plan"]

    assert plan.knowledge_domain == "cross_domain"
    assert plan.target_unit == "B-504"
    assert len(plan.sub_analyses) == 2

