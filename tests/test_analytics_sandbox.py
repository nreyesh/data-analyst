"""Unit tests for subprocess visualization sandbox."""

from __future__ import annotations

import pytest
from pathlib import Path

from analytics.sandbox import run_plotting_sandbox


SAMPLE_DATASET = [
    {"period_date": "2025-07-01", "subtotal": 12450000},
    {"period_date": "2025-08-01", "subtotal": 14200000},
    {"period_date": "2025-09-01", "subtotal": 13900000},
]

VALID_MATPLOTLIB_SCRIPT = """
import json
import matplotlib.pyplot as plt

with open("dataset.json", "r", encoding="utf-8") as f:
    data = json.load(f)

dates = [row["period_date"] for row in data]
amounts = [row["subtotal"] / 1000000 for row in data]

plt.figure(figsize=(6, 3))
plt.plot(dates, amounts, marker="o")
plt.title("Test Chart")
plt.tight_layout()
plt.savefig("output.png")
"""


def test_sandbox_generates_valid_png():
    """Verify that valid Matplotlib code creates a real PNG file in charts dir."""
    result = run_plotting_sandbox(
        script_code=VALID_MATPLOTLIB_SCRIPT,
        dataset=SAMPLE_DATASET,
        output_filename="test_valid_chart.png",
    )
    assert result.success is True
    assert result.chart_path is not None
    assert result.chart_path.exists()
    assert result.error_message is None

    # Verify PNG magic number
    header = result.chart_path.read_bytes()[:8]
    assert header == b"\x89PNG\r\n\x1a\n"

    # Cleanup test output
    result.chart_path.unlink(missing_ok=True)


def test_sandbox_terminates_infinite_loop():
    """Verify that an infinite loop is killed within timeout ceiling."""
    infinite_script = """
import time
while True:
    time.sleep(0.1)
"""
    result = run_plotting_sandbox(
        script_code=infinite_script,
        dataset=SAMPLE_DATASET,
        timeout_seconds=1,
    )
    assert result.success is False
    assert result.chart_path is None
    assert "TimeoutError" in (result.error_message or "")
    assert result.duration_seconds >= 1.0


def test_sandbox_captures_stderr_traceback():
    """Verify script exception traceback is returned for self-healing feedback."""
    broken_script = """
import json
with open("dataset.json") as f:
    data = json.load(f)
# Key error
val = data[0]["non_existent_key"]
"""
    result = run_plotting_sandbox(
        script_code=broken_script,
        dataset=SAMPLE_DATASET,
    )
    assert result.success is False
    assert "KeyError" in (result.error_message or "")
    assert "SubprocessError" in (result.error_message or "")


def test_sandbox_detects_missing_output_image():
    """Verify failure if script exits code 0 but forgets to save output.png."""
    noop_script = """
print("I ran successfully but forgot to save output.png")
"""
    result = run_plotting_sandbox(
        script_code=noop_script,
        dataset=SAMPLE_DATASET,
    )
    assert result.success is False
    assert "output.png" in (result.error_message or "")


def test_sandbox_executes_recipe_a_stacked_bars():
    """Verify Recipe A (unit stacked component evolution) renders valid PNG in sandbox."""
    unit_dataset = [
        {"period_date": "2026-06-01", "base_gasto_comun": 85000, "consumos_total": 12000, "fondos_recargos": 5000, "total": 102000},
        {"period_date": "2026-07-01", "base_gasto_comun": 88000, "consumos_total": 13000, "fondos_recargos": 5000, "total": 106000},
        {"period_date": "2026-08-01", "base_gasto_comun": 93236, "consumos_total": 14189, "fondos_recargos": 15540, "total": 122965},
    ]

    recipe_a_script = """
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
b3 = ax.bar(periods, fondos, bottom=bottom_for_fondos, label="Gastos Unidad / Fondos", color="#e74c3c")

for i, tot in enumerate(totals):
    ax.text(i, tot + 1500, f"${tot:,.0f}".replace(",", "."), ha="center", va="bottom", fontsize=8)

ax.set_title("Evolución de Gastos Unidad B-504")
ax.legend(loc="upper left")
plt.tight_layout()
plt.savefig("output.png")
"""
    result = run_plotting_sandbox(
        script_code=recipe_a_script,
        dataset=unit_dataset,
        output_filename="test_recipe_a.png",
    )
    assert result.success is True
    assert result.chart_path is not None
    assert result.chart_path.exists()
    assert result.chart_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    result.chart_path.unlink(missing_ok=True)


def test_sandbox_executes_recipe_b_dual_axis_tariff():
    """Verify Recipe B (tariff vs volume dual axis) renders valid PNG in sandbox."""
    water_dataset = [
        {"period_date": "2026-06-01", "consumption": 1.65, "unit_rate": 5120.0},
        {"period_date": "2026-07-01", "consumption": 1.72, "unit_rate": 5250.0},
        {"period_date": "2026-08-01", "consumption": 1.70, "unit_rate": 8340.24},
    ]

    recipe_b_script = """
import json
import matplotlib.pyplot as plt

with open("dataset.json", "r", encoding="utf-8") as f:
    data = json.load(f)

periods = [r["period_date"] for r in data]
m3_vals = [r["consumption"] for r in data]
tariffs = [r["unit_rate"] for r in data]

fig, ax1 = plt.subplots(figsize=(8, 4))
ax2 = ax1.twinx()

ax1.bar(periods, m3_vals, width=0.35, color="#bdc3c7", alpha=0.6, label="Consumo (m³)")
ax1.set_ylabel("Consumo (m³)", color="#7f8c8d")

ax2.plot(periods, tariffs, color="#e67e22", marker="o", linewidth=2, label="Tarifa ($/m³)")
ax2.set_ylabel("Tarifa ($/m³)", color="#d35400")

for p, t in zip(periods, tariffs):
    ax2.annotate(f"${t:,.0f}".replace(",", "."), (p, t), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=8)

ax1.set_title("Agua Caliente: Consumo vs Tarifa")
plt.tight_layout()
plt.savefig("output.png")
"""
    result = run_plotting_sandbox(
        script_code=recipe_b_script,
        dataset=water_dataset,
        output_filename="test_recipe_b.png",
    )
    assert result.success is True
    assert result.chart_path is not None
    assert result.chart_path.exists()
    assert result.chart_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    result.chart_path.unlink(missing_ok=True)
