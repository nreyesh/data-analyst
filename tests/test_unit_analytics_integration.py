"""Integration tests for unit-level analytics, two-part causal decomposition, and multi-unit comparisons."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from analytics.nodes import SQLCoderOutput, VisualizerOutput
from analytics.state import IntentPlan, SQLSubQuery, TrajectoryEvaluation
from analytics.workflow import run_analysis
from backend.app.main import app


@pytest.fixture
def gateway_bridge(monkeypatch):
    """Route analytics.gateway_client HTTP calls through FastAPI TestClient(app)."""
    client = TestClient(app)

    def _mock_post(url: str, json: dict, timeout: float = 5.0):
        return client.post("/api/v1/analytics/execute-query", json=json)

    monkeypatch.setattr("analytics.gateway_client.requests.post", _mock_post)


# ============================================================================
# Task 5.1: Unit Time-Series Trend & Stacked Bar Chart Generation
# ============================================================================


def test_unit_time_series_trend_and_stacked_bar_chart(gateway_bridge, tmp_path):
    """Verify unit-level query generates time series data and executes Recipe A in sandbox."""
    plan = IntentPlan(
        is_authorized_query=True,
        analysis_type="time_series_trend",
        temporal_resolution="monthly",
        target_unit="B-504",
        knowledge_domain="unit",
        analysis_goal="Evolución mensual de gastos de la unidad B-504 en 2026",
        relevant_tables=["unit_bills"],
        time_scope="2026",
        requires_visualization=True,
        suggested_chart_type="stacked_bar",
    )

    sql_output = SQLCoderOutput(
        reasoning="Query monthly component breakdown for unit B-504 in 2026",
        sql_query=(
            "SELECT period_date, "
            "calculated_dept_subtotal AS base_gasto_comun, "
            "calculated_consumo_total AS consumos_total, "
            "calculated_unit_subtotal AS fondos_recargos, "
            "calculated_total_month AS total "
            "FROM unit_bills "
            "WHERE unit_code = 'B-504' AND period_year = 2026 "
            "ORDER BY period_date ASC"
        ),
    )

    recipe_a_code = """
import json
import matplotlib.pyplot as plt

with open("dataset.json", "r", encoding="utf-8") as f:
    data = json.load(f)

periods = [r["period_date"] for r in data]
base = [r["base_gasto_comun"] for r in data]
consumos = [r["consumos_total"] for r in data]
fondos = [r["fondos_recargos"] for r in data]
totals = [r["total"] for r in data]

fig, ax = plt.subplots(figsize=(8, 4))
b1 = ax.bar(periods, base, label="Base Prorrateo", color="#3498db")
b2 = ax.bar(periods, consumos, bottom=base, label="Consumos", color="#2ecc71")
bottom_for_fondos = [b + c for b, c in zip(base, consumos)]
b3 = ax.bar(periods, fondos, bottom=bottom_for_fondos, label="Gastos Unidad", color="#e74c3c")
ax.legend(loc="upper left")
plt.tight_layout()
plt.savefig("output.png")
"""

    viz_output = VisualizerOutput(
        reasoning="Stacked bar chart adhering to Recipe A",
        python_code=recipe_a_code,
    )

    synth_text = "El gasto total de la unidad B-504 promedió $110.000 CLP en 2026 [Enero 2026 - Septiembre 2026]."

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        MagicMock(text=plan.model_dump_json()),
        MagicMock(text=sql_output.model_dump_json()),
        MagicMock(text=viz_output.model_dump_json()),
        MagicMock(text=synth_text),
    ]

    with patch("analytics.nodes.get_gemini_client", return_value=mock_client):
        res = run_analysis(
            query="Evolución de gastos de la unidad B-504 en 2026. Con gráfico.",
            chart_dir=tmp_path,
        )

        assert res.is_authorized is True
        assert res.data is not None
        assert len(res.data) == 9  # 9 months of records for B-504
        assert res.chart_file_path is not None
        assert res.chart_file_path.endswith(".png")
        assert "B-504" in res.summary


# ============================================================================
# Task 5.2: Two-Part Causal Decomposition
# ============================================================================


def test_two_part_causal_decomposition_and_synthesis(gateway_bridge):
    """Verify unit bill increase query decomposes into unit delta and building variance queries."""
    plan = IntentPlan(
        is_authorized_query=True,
        analysis_type="variance_driver",
        temporal_resolution="monthly",
        target_unit="B-504",
        knowledge_domain="cross_domain",
        sub_analyses=[
            "Desglose y variación MoM de la boleta de la unidad B-504",
            "Atribución de causas macro en el gasto general y caldera del edificio",
        ],
        analysis_goal="Explicar las causas del alza en la boleta de agosto del depto B-504",
        relevant_tables=["unit_bills", "expense_reports", "expense_sections"],
        time_scope="2026",
        requires_visualization=False,
    )

    eval_res = TrajectoryEvaluation(
        premise_status="validated",
        evaluation_summary="La unidad B-504 muestra un alza entre julio y agosto de 2026.",
        baseline_period="2026-07-01",
        peak_period="2026-08-01",
        needs_clarification=False,
    )

    sql_output = SQLCoderOutput(
        reasoning="Two-part query: Unit component differences + building operational changes",
        queries=[
            SQLSubQuery(
                query_id="unit_delta",
                title="Variación Componentes Unidad B-504",
                sql_query=(
                    "SELECT period_date, calculated_dept_subtotal, calculated_consumo_total, "
                    "calculated_unit_subtotal, calculated_total_month "
                    "FROM unit_bills "
                    "WHERE unit_code = 'B-504' AND period_date IN ('2026-07-01', '2026-08-01') "
                    "ORDER BY period_date ASC"
                ),
            ),
            SQLSubQuery(
                query_id="building_variance",
                title="Causas Macro Edificio",
                sql_query=(
                    "SELECT r.period_date, s.category_name, s.calculated_subtotal "
                    "FROM expense_sections s "
                    "JOIN expense_reports r ON s.report_id = r.id "
                    "WHERE r.period_date IN ('2026-07-01', '2026-08-01') "
                    "ORDER BY r.period_date ASC, s.calculated_subtotal DESC"
                ),
            ),
        ],
    )

    synth_text = (
        "En agosto 2026, tu gasto en el depto B-504 aumentó debido al nuevo Fondo de Impermeabilización "
        "y un alza en el gasto operacional del edificio [Agosto 2026]."
    )

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        MagicMock(text=plan.model_dump_json()),
        MagicMock(text=eval_res.model_dump_json()),
        MagicMock(text=sql_output.model_dump_json()),
        MagicMock(text=synth_text),
    ]

    with patch("analytics.nodes.get_gemini_client", return_value=mock_client):
        res = run_analysis("¿Por qué subió tanto mi gasto común de agosto en el depto B-504?")

        assert res.is_authorized is True
        assert res.needs_clarification is False
        assert res.data is not None
        assert "B-504" in res.summary
        assert "Agosto 2026" in res.summary


# ============================================================================
# Task 5.3: Admin Multi-Unit Comparison
# ============================================================================


def test_admin_multi_unit_comparison(gateway_bridge):
    """Verify administrator query comparing multiple units generates valid SQL and metrics."""
    plan = IntentPlan(
        is_authorized_query=True,
        analysis_type="comparative_cohort",
        temporal_resolution="monthly",
        target_unit="B-504",
        knowledge_domain="unit",
        analysis_goal="Comparar el gasto común entre los departamentos B-504 y B-506 en 2026",
        relevant_tables=["unit_bills"],
        time_scope="2026",
        requires_visualization=False,
    )

    sql_output = SQLCoderOutput(
        reasoning="Compare monthly total for units B-504 and B-506",
        sql_query=(
            "SELECT unit_code, period_date, calculated_total_month "
            "FROM unit_bills "
            "WHERE unit_code IN ('B-504', 'B-506') AND period_year = 2026 "
            "ORDER BY period_date ASC, unit_code ASC"
        ),
    )

    eval_res = TrajectoryEvaluation(
        premise_status="validated",
        evaluation_summary="Ambas unidades cuentan con registros para 2026.",
        baseline_period="2026-01-01",
        peak_period="2026-09-01",
        needs_clarification=False,
    )

    synth_text = (
        "Comparación 2026: La unidad B-504 pagó un promedio de $110.000 CLP mientras que B-506 pagó $108.000 CLP [2026]."
    )

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        MagicMock(text=plan.model_dump_json()),
        MagicMock(text=eval_res.model_dump_json()),
        MagicMock(text=sql_output.model_dump_json()),
        MagicMock(text=synth_text),
    ]

    with patch("analytics.nodes.get_gemini_client", return_value=mock_client):
        res = run_analysis("Compara el gasto común entre el depto B-504 y el B-506 en 2026")

        assert res.is_authorized is True
        assert res.data is not None
        assert len(res.data) == 18  # 9 months * 2 units
        unit_codes = {r["unit_code"] for r in res.data}
        assert unit_codes == {"B-504", "B-506"}
        assert "B-504" in res.summary
        assert "B-506" in res.summary
