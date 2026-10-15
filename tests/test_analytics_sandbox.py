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
