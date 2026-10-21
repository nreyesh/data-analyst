"""Unit tests for modular data dictionary loader."""

from __future__ import annotations

import pytest
from analytics.prompt_loader import load_modular_data_dictionary


def test_load_modular_data_dictionary_building():
    """Verify building domain loads core conventions and building tables, omitting unit tables."""
    text = load_modular_data_dictionary(domain="building")
    assert "Domain Conventions & Financial Rules" in text
    assert "expense_reports" in text
    assert "expense_sections" in text
    assert "unit_bills" not in text
    assert "unit_bill_consumptions" not in text


def test_load_modular_data_dictionary_unit():
    """Verify unit domain loads core conventions and unit tables, omitting macro building tables."""
    text = load_modular_data_dictionary(domain="unit")
    assert "Domain Conventions & Financial Rules" in text
    assert "unit_bills" in text
    assert "unit_bill_consumptions" in text
    assert "expense_sections" not in text


def test_load_modular_data_dictionary_cross_domain():
    """Verify cross_domain loads core conventions, building tables, unit tables, and cross-domain bridge."""
    text = load_modular_data_dictionary(domain="cross_domain")
    assert "Domain Conventions & Financial Rules" in text
    assert "expense_reports" in text
    assert "unit_bills" in text
    assert "Prorating Relationships (Ley de Copropiedad 21.442)" in text
    assert "operational_total" in text
