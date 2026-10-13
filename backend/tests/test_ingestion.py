"""Tests for expense report ingestion endpoint and retrieval."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_ingest_sample_report_success(client: TestClient, sample_payload_2026_9: dict):
    """Verify successful ingestion of sample 2026-9.json."""
    response = client.post("/api/v1/reports", json=sample_payload_2026_9)
    assert response.status_code == 201

    data = response.json()
    assert data["status"] == "created"
    assert data["overwritten"] is False
    assert data["period_date"] == "2026-09-01"
    assert data["sections_count"] == len(sample_payload_2026_9["sections"])
    assert data["calculated_grand_total"] > 0
    assert "id" in data
    assert len(data["id"]) > 0


def test_list_reports_after_ingestion(client: TestClient, sample_payload_2026_9: dict):
    """Verify GET /api/v1/reports returns stored summaries."""
    # Ingest report
    client.post("/api/v1/reports", json=sample_payload_2026_9)

    # List reports
    response = client.get("/api/v1/reports")
    assert response.status_code == 200
    reports = response.json()
    assert len(reports) == 1
    assert reports[0]["period_date"] == "2026-09-01"
    assert reports[0]["period_year"] == 2026
    assert reports[0]["period_month"] == 9


def test_get_report_by_id_and_period(client: TestClient, sample_payload_2026_9: dict):
    """Verify hierarchical detail retrieval by UUID and period date."""
    ingest_res = client.post("/api/v1/reports", json=sample_payload_2026_9)
    report_id = ingest_res.json()["id"]

    # Fetch by ID
    by_id_res = client.get(f"/api/v1/reports/{report_id}")
    assert by_id_res.status_code == 200
    detail = by_id_res.json()
    assert detail["id"] == report_id
    assert len(detail["sections"]) == len(sample_payload_2026_9["sections"])
    assert detail["telemetry_metadata"] is not None
    assert detail["telemetry_metadata"]["model_used"] == sample_payload_2026_9.get("_metadata", {}).get("model_used")

    # Fetch by Period
    by_period_res = client.get("/api/v1/reports/period/2026-09-01")
    assert by_period_res.status_code == 200
    assert by_period_res.json()["id"] == report_id


def test_lowercase_normalization(client: TestClient, sample_payload_2026_9: dict):
    """Verify line items have their names normalized to lowercase with accents stripped."""
    from backend.app.utils import normalize_item_name

    ingest_res = client.post("/api/v1/reports", json=sample_payload_2026_9)
    report_id = ingest_res.json()["id"]

    detail = client.get(f"/api/v1/reports/{report_id}").json()
    for section in detail["sections"]:
        for item in section["items"]:
            assert item["normalized_name"] == normalize_item_name(item["name"])


def test_accent_stripping_and_category_normalization(
    client: TestClient, sample_payload_2026_9: dict
):
    """Verify line items and categories have accents stripped while preserving 'ñ'."""
    payload = {
        **sample_payload_2026_9,
        "sections": [
            *sample_payload_2026_9["sections"],
            {
                "general_topic": "Mantención e Instalación",
                "sub_topics": [
                    {"name": "Reparación de Baño", "value": 50000},
                    {"name": "Diseño Eléctrico", "value": 75000},
                ],
            },
        ],
    }
    ingest_res = client.post("/api/v1/reports", json=payload)
    report_id = ingest_res.json()["id"]

    detail = client.get(f"/api/v1/reports/{report_id}").json()

    # Verify categories are normalized
    cat_names = [sec["category_name"] for sec in detail["sections"]]
    assert "asesorias y honorarios profesionales" in cat_names
    assert "mantencion e instalacion" in cat_names

    # Find the custom section and verify items
    custom_sec = next(
        s for s in detail["sections"] if s["category_name"] == "mantencion e instalacion"
    )
    items_map = {item["name"]: item["normalized_name"] for item in custom_sec["items"]}
    assert items_map["Reparación de Baño"] == "reparacion de baño"  # 'ñ' preserved!
    assert items_map["Diseño Eléctrico"] == "diseño electrico"  # 'ñ' preserved!

    # Verify all items in the report have no accented vowels
    for section in detail["sections"]:
        for item in section["items"]:
            for accented in "áéíóúÁÉÍÓÚüÜ":
                assert accented not in item["normalized_name"]


def test_ingest_report_with_negative_items(client: TestClient):
    """Verify ingestion of a report containing negative items (credit notes) like 2026-7."""
    payload = {
        "issue_date": "01/07/2026",
        "sections": [
            {
                "general_topic": "Remuneraciones",
                "sub_topics": [
                    {"name": "Sueldos", "value": 12739274},
                    {"name": "Servicios de Administración", "value": 2585802},
                    {"name": "Cotizaciones Previsionales", "value": 2906811},
                    {"name": "Finiquitos", "value": -250690},
                ],
            }
        ],
    }
    response = client.post("/api/v1/reports", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["calculated_grand_total"] == 17981197

    detail = client.get(f"/api/v1/reports/{data['id']}").json()
    assert detail["sections"][0]["calculated_subtotal"] == 17981197
    finiquitos = next(it for it in detail["sections"][0]["items"] if it["name"] == "Finiquitos")
    assert finiquitos["amount"] == -250690
