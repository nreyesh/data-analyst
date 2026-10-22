"""Integration tests for 4-table unit bill persistence, overwrite isolation, cascade delete, sync, and Data Gateway access."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.models import (
    UnitBillChargeModel,
    UnitBillConceptModel,
    UnitBillConsumptionModel,
    UnitBillModel,
)
from backend.app.repository import (
    find_report_by_period,
    find_unit_bill_by_unit_and_period,
    list_towers_for_unit_number,
)
from backend.app.routes.analytics import validate_query_security


def test_ingest_unit_bill_4_table_persistence_and_subtotals(
    client: TestClient,
    db_session: Session,
    sample_unit_payload_504b_2026_08: dict,
):
    """Verify POST /api/v1/unit-bills persists all 4 tables, normalizes names, and calculates deterministic CLP totals."""
    res = client.post("/api/v1/unit-bills", json=sample_unit_payload_504b_2026_08)
    assert res.status_code == 201
    data = res.json()

    assert data["unit_code"] == "B-504"
    assert data["period_date"] == "2026-08-01"
    assert data["status"] == "created"
    assert data["overwritten"] is False
    assert data["calculated_dept_subtotal"] == 108776
    assert data["calculated_consumo_total"] == 14189
    assert data["calculated_unit_subtotal"] == 2500
    assert data["calculated_total_month"] == 125465
    assert data["concepts_count"] == 3
    assert data["consumptions_count"] == 1
    assert data["unit_charges_count"] == 1

    # Verify DB persistence and normalized names
    bill = find_unit_bill_by_unit_and_period(db_session, "B-504", "2026-08-01")
    assert bill is not None
    assert bill.last_payment_date == "2026-08-31"
    assert bill.last_payment_amount == 125204
    assert bill.concepts[0].normalized_name == "gasto comun"
    assert bill.consumptions[0].normalized_name == "torre b"
    assert bill.unit_charges[0].normalized_name == "fondo de indemnizacion"


def test_composite_duplicate_overwrite_and_multi_unit_coexistence(
    client: TestClient,
    db_session: Session,
    sample_unit_payload_504b_2026_08: dict,
    sample_payload_2026_9: dict,
):
    """Verify overwriting (unit_code, period_date) is atomic and independent from other units and general reports."""
    # 1. Ingest general report for 2026-08
    gen_payload_aug = copy.deepcopy(sample_payload_2026_9)
    gen_payload_aug["issue_date"] = "01/08/2026"
    r_gen = client.post("/api/v1/reports", json=gen_payload_aug)
    assert r_gen.status_code == 201

    # 2. Ingest B-504 and A-102 for 2026-08
    r1 = client.post("/api/v1/unit-bills", json=sample_unit_payload_504b_2026_08)
    assert r1.status_code == 201

    payload_a102 = copy.deepcopy(sample_unit_payload_504b_2026_08)
    payload_a102["unit_code"] = "A-102"
    r2 = client.post("/api/v1/unit-bills", json=payload_a102)
    assert r2.status_code == 201

    # 3. Overwrite B-504 with an added fine (recargo: 50000)
    updated_504b = copy.deepcopy(sample_unit_payload_504b_2026_08)
    updated_504b["unit_charges"].insert(
        0,
        {"charge_name": "Multa Ruidos Molestos", "charge_type": "recargo", "amount": 50000},
    )
    r3 = client.post("/api/v1/unit-bills", json=updated_504b)
    assert r3.status_code == 200
    assert r3.json()["overwritten"] is True
    assert r3.json()["calculated_unit_subtotal"] == 52500
    assert r3.json()["calculated_total_month"] == 175465

    # Verify A-102 and general report are untouched
    bill_a102 = find_unit_bill_by_unit_and_period(db_session, "A-102", "2026-08-01")
    assert bill_a102 is not None
    assert bill_a102.calculated_total_month == 125465

    # 4. Re-ingest general report for 2026-08 and verify unit bills still exist
    r_gen_overwrite = client.post("/api/v1/reports", json=gen_payload_aug)
    assert r_gen_overwrite.status_code == 200
    assert find_unit_bill_by_unit_and_period(db_session, "B-504", "2026-08-01") is not None

    # 5. Test GET filtering by unit_code
    list_res = client.get("/api/v1/unit-bills?unit_code=B-504")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) == 1
    assert items[0]["unit_code"] == "B-504"


def test_unit_bill_cascade_deletion(
    client: TestClient,
    db_session: Session,
    sample_unit_payload_504b_2026_08: dict,
):
    """Verify deleting a unit bill cascades to concepts, consumptions, and unit_charges."""
    res = client.post("/api/v1/unit-bills", json=sample_unit_payload_504b_2026_08)
    bill_id = res.json()["id"]

    assert db_session.query(UnitBillConceptModel).count() == 3
    assert db_session.query(UnitBillConsumptionModel).count() == 1
    assert db_session.query(UnitBillChargeModel).count() == 1

    del_res = client.delete(f"/api/v1/unit-bills/{bill_id}")
    assert del_res.status_code == 204

    assert db_session.query(UnitBillModel).count() == 0
    assert db_session.query(UnitBillConceptModel).count() == 0
    assert db_session.query(UnitBillConsumptionModel).count() == 0
    assert db_session.query(UnitBillChargeModel).count() == 0


def test_dual_domain_sync_reconciles_general_and_unit_queues(
    client: TestClient,
    db_session: Session,
    sample_payload_2026_9: dict,
    sample_unit_payload_504b_2026_08: dict,
    tmp_path: Path,
    monkeypatch,
):
    """Verify POST /api/v1/sync/pending reconciles both general/pending/ and unit/pending/."""
    settings = get_settings()
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    settings.ensure_directories()

    gen_file = settings.pending_dir / "2026-9.json"
    unit_file = settings.unit_pending_dir / "504b-2026-08.json"
    gen_file.write_text(json.dumps(sample_payload_2026_9), encoding="utf-8")
    unit_file.write_text(json.dumps(sample_unit_payload_504b_2026_08), encoding="utf-8")

    res = client.post("/api/v1/sync/pending")
    assert res.status_code == 200
    data = res.json()
    assert data["total_processed"] == 2
    assert data["synced_count"] == 2
    assert data["failed_count"] == 0

    assert (settings.synced_dir / "2026-9.json").exists()
    assert (settings.unit_synced_dir / "504b-2026-08.json").exists()
    assert find_report_by_period(db_session, "2026-09-01") is not None
    assert find_unit_bill_by_unit_and_period(db_session, "B-504", "2026-08-01") is not None


def test_data_gateway_allows_unit_bill_tables():
    """Verify Data Gateway security validator allows read-only queries joining unit_bills and expense_reports."""
    sql = """
    SELECT u.unit_code, u.period_date, u.calculated_total_month, r.operational_total
    FROM unit_bills u
    JOIN expense_reports r ON u.period_date = r.period_date
    LEFT JOIN unit_bill_concepts c ON c.unit_bill_id = u.id
    LEFT JOIN unit_bill_consumptions m ON m.unit_bill_id = u.id
    LEFT JOIN unit_bill_charges g ON g.unit_bill_id = u.id
    WHERE u.unit_code = 'B-504'
    """
    # Should not raise HTTPException
    validate_query_security(sql)


def test_decoupled_unit_filtering_and_tower_lookup(
    client: TestClient,
    db_session: Session,
    sample_unit_payload_504b_2026_08: dict,
):
    """Verify filtering by tower and unit_number, suffix unit_code format, and dynamic tower lookup."""
    # 1. Ingest B-504
    r1 = client.post("/api/v1/unit-bills", json=sample_unit_payload_504b_2026_08)
    assert r1.status_code == 201

    # 2. Ingest A-504
    payload_a504 = copy.deepcopy(sample_unit_payload_504b_2026_08)
    payload_a504["unit_code"] = "A-504"
    r2 = client.post("/api/v1/unit-bills", json=payload_a504)
    assert r2.status_code == 201

    # Filter by tower and unit_number
    res_b = client.get("/api/v1/unit-bills?tower=B&unit_number=504")
    assert res_b.status_code == 200
    bills_b = res_b.json()
    assert len(bills_b) == 1
    assert bills_b[0]["tower"] == "B"
    assert bills_b[0]["unit_number"] == 504

    # Filter by suffix unit_code "504B"
    res_suffix = client.get("/api/v1/unit-bills?unit_code=504B")
    assert res_suffix.status_code == 200
    bills_suffix = res_suffix.json()
    assert len(bills_suffix) == 1
    assert bills_suffix[0]["unit_code"] == "B-504"

    # Tower lookup for 504
    towers = list_towers_for_unit_number(db_session, 504)
    assert sorted(towers) == ["A", "B"]

