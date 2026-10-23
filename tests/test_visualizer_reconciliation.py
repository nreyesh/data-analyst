"""Unit tests for 4-component unit evolution chart reconciliation and exterior legend layout."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from analytics.nodes import visualizer_node, VisualizerOutput
from analytics.sandbox import run_plotting_sandbox
from analytics.state import DataAnalystState, IntentPlan


SAMPLE_UNIT_ROWS = [
    {
        "period_date": "2026-05-01",
        "calculated_dept_subtotal": 190091,
        "calculated_consumo_total": 32137,
        "calculated_unit_subtotal": 4271,
        "calculated_total_month": 226499,
    },
    {
        "period_date": "2026-06-01",
        "calculated_dept_subtotal": 190470,
        "calculated_consumo_total": 24303,
        "calculated_unit_subtotal": 0,  # Zero-value test
        "calculated_total_month": 214773,
    },
    {
        "period_date": "2026-07-01",
        "calculated_dept_subtotal": 195779,
        "calculated_consumo_total": 37445,
        "calculated_unit_subtotal": 46385,  # Peak fine test
        "calculated_total_month": 279609,
    },
]

RECONCILED_MATPLOTLIB_SCRIPT = """
import json
import matplotlib.pyplot as plt

with open("dataset.json", "r", encoding="utf-8") as f:
    data = json.load(f)

periods = [r["period_date"] for r in data]
totals = [r["calculated_total_month"] for r in data]
depto = [r["calculated_dept_subtotal"] for r in data]
consumos = [r["calculated_consumo_total"] for r in data]
unit_charges = [r["calculated_unit_subtotal"] for r in data]

fig, ax = plt.subplots(figsize=(10, 5))
ax.plot(periods, totals, label="Total Unidad", color="#e74c3c", linewidth=2.5, marker="o")
ax.plot(periods, depto, label="Gasto Común Depto", color="#34495e", linestyle="--", marker="s")
ax.plot(periods, consumos, label="Agua Caliente", color="#27ae60", linestyle=":", marker="^")
ax.plot(periods, unit_charges, label="Cargos Particulares (Multas, Seguros, otros)", color="#8e44ad", linestyle="-.", marker="D")

ax.set_ylim(bottom=0, top=max(totals) * 1.20)

for p, tot in zip(periods, totals):
    ax.annotate(f"${tot:,.0f}".replace(",", "."), (p, tot), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=8.5)

ax.set_ylabel("Monto (CLP)")
ax.set_xlabel("Período")
ax.grid(True, linestyle="--", alpha=0.4)
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=4, frameon=True, fontsize=8)

plt.tight_layout()
plt.savefig("output.png", dpi=150, bbox_inches="tight")
"""


def test_visualizer_node_prompts_reconciliation_contract():
    """Verify visualizer_node injects 4-component instructions when dataset contains unit billing rows."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = VisualizerOutput(
        reasoning="Reconciled 4-component plot",
        python_code=RECONCILED_MATPLOTLIB_SCRIPT,
    ).model_dump_json()
    mock_client.models.generate_content.return_value = mock_resp

    state: DataAnalystState = {
        "user_query": "muestrame la evolucion del gasto comun para el depto 506b",
        "sql_results": SAMPLE_UNIT_ROWS,
        "intent_plan": IntentPlan(
            is_authorized_query=True,
            knowledge_domain="unit",
            analysis_type="time_series_trend",
            analysis_goal="Evolución Depto 506B",
            suggested_chart_type="line",
            unit_tower="B",
            unit_number=506,
            target_unit="B-506",
        ),
    }

    result = visualizer_node(state, client=mock_client)
    assert "visualization_code" in result
    assert result["visualization_code"] == RECONCILED_MATPLOTLIB_SCRIPT

    # Verify call contents passed to Gemini contain the 4 required series names
    call_args = mock_client.models.generate_content.call_args
    passed_contents = call_args.kwargs.get("contents") or call_args[1].get("contents")
    joined_prompt = "\n".join(str(c) for c in passed_contents)

    assert "CRITICAL APARTMENT RECONCILIATION REQUIREMENT" in joined_prompt
    assert "Total Unidad" in joined_prompt
    assert "Gasto Común Depto" in joined_prompt
    assert "Agua Caliente" in joined_prompt
    assert "Cargos Particulares (Multas, Seguros, otros)" in joined_prompt
    assert "bbox_to_anchor=(0.5, -0.18)" in joined_prompt


def test_reconciled_script_sandbox_execution():
    """Verify that the 4-component Matplotlib script executes in sandbox and handles 0 CLP cleanly."""
    res = run_plotting_sandbox(
        script_code=RECONCILED_MATPLOTLIB_SCRIPT,
        dataset=SAMPLE_UNIT_ROWS,
        output_filename="test_reconciled_unit_chart.png",
    )
    assert res.success is True
    assert res.chart_path is not None
    assert res.chart_path.exists()
    assert res.chart_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    # Cleanup test output
    res.chart_path.unlink(missing_ok=True)
