# Expense Reports Backend & Storage Service

FastAPI persistent backend service and normalized SQLite relational storage engine for Chilean condominium expense reports (*"Informe Gastos Comunes"*).

This service implements **Phase 2 (Storage Layer)** of the project roadmap, serving as the persistent persistence hub between the extraction pipeline (**Phase 1**) and the analytical/conversational agent (**Phase 3 & Phase 4**).

---

## 🌟 What It Does

1. **Persistent Web Service**: Runs continuously on a dedicated port (default `8000`), listening for incoming ingestion requests and analytical queries.
2. **Verbatim Ingestion (Zero Glue Code)**: Directly accepts JSON files produced by `data_extractor` (`data/output/*.json`) without transformation.
3. **Normalized Relational Storage (SQLite 3NF)**: Organizes reports into 3 linked tables (`expense_reports` ➔ `expense_sections` ➔ `expense_items`) for native SQL querying in Phase 3.
4. **Duplicate Prevention (Overwrite & Indicate)**: Enforces unique monthly periods (`YYYY-MM-01`). If a report for the same period already exists, it atomically replaces it and explicitly signals that it was overwritten.
5. **UUIDv7 Identifiers**: Uses cryptographically secure, time-ordered UUIDv7 strings for all primary keys to prevent IDOR vulnerabilities and eliminate SQLite B-tree page fragmentation.
6. **Financial Precision**: Guarantees pure integer Chilean Pesos (CLP) for all amounts and pre-computes deterministic section subtotals and grand totals.
7. **Cross-Month Item Normalization**: Stores a lowercase-normalized column (`normalized_name`) for every expense item to enable instant trend queries across historical months.

---

## 🏗️ Database Architecture

The SQLite database is located at `data/storage/expenses.db` with Write-Ahead Logging (`WAL`) and cascade deletions enabled.

```
┌────────────────────────────────────────────────────────┐
│                    expense_reports                     │
├────────────────────────────────────────────────────────┤
│ PK  id                      TEXT (UUIDv7)              │
│ UQ  period_date             TEXT NOT NULL (YYYY-MM-01) │ ◀── 1 report per month
│     period_year             INTEGER NOT NULL           │
│     period_month            INTEGER NOT NULL           │
│     calculated_grand_total  INTEGER NOT NULL (CLP)     │
│     telemetry_metadata      TEXT (JSON string)         │
│     created_at / updated_at TEXT (ISO-8601)            │
└───────────────────────────┬────────────────────────────┘
                            │ 1 : N (ON DELETE CASCADE)
                            ▼
┌────────────────────────────────────────────────────────┐
│                    expense_sections                    │
├────────────────────────────────────────────────────────┤
│ PK  id                      TEXT (UUIDv7)              │
│ FK  report_id               TEXT NOT NULL              │
│     category_name           TEXT NOT NULL              │
│     calculated_subtotal     INTEGER NOT NULL (CLP)     │
│     display_order           INTEGER DEFAULT 0          │
└───────────────────────────┬────────────────────────────┘
                            │ 1 : N (ON DELETE CASCADE)
                            ▼
┌────────────────────────────────────────────────────────┐
│                     expense_items                      │
├────────────────────────────────────────────────────────┤
│ PK  id                      TEXT (UUIDv7)              │
│ FK  section_id              TEXT NOT NULL              │
│     name                    TEXT NOT NULL              │
│     normalized_name         TEXT NOT NULL (lowercase)  │ ◀── Cross-month trends
│     amount                  INTEGER NOT NULL CHECK >= 0│ ◀── Integer CLP
│     display_order           INTEGER DEFAULT 0          │
└────────────────────────────────────────────────────────┘
```

---

## 🚀 How to Run the Service

### 1. Start the Server

From the project root:

```bash
# Using python module entrypoint
python -m backend.app.main

# Or directly with uvicorn (with hot-reload for development)
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

The database file `data/storage/expenses.db` and all required tables are automatically created on initial startup.

### 2. Verify Service Health

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "healthy",
  "database": "connected",
  "database_path": "/path/to/data/storage/expenses.db"
}
```

### 3. Interactive API Documentation

Open your browser to:
- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## 📡 API Endpoints Reference

| Method | Endpoint | Description | Status Code |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Service and database connectivity health check | `200 OK` |
| `POST` | `/api/v1/reports` | Ingest or overwrite an extracted expense report | `201 Created` / `200 OK` |
| `GET` | `/api/v1/reports` | List all reports (overview summaries sorted by date desc) | `200 OK` |
| `GET` | `/api/v1/reports/{id}` | Get full hierarchical report detail by UUIDv7 | `200 OK` / `404` |
| `GET` | `/api/v1/reports/period/{period_date}` | Lookup full report by ISO period (e.g. `2026-10-01`) | `200 OK` / `404` |
| `DELETE`| `/api/v1/reports/{id}` | Delete report (cascades and deletes sections and items) | `204 No Content` |

---

## 📥 Ingestion & Usage Examples

### Ingesting an Extracted Report via `curl`

Send an output file generated by the extractor directly:

```bash
curl -X POST http://localhost:8000/api/v1/reports \
     -H "Content-Type: application/json" \
     -d @data/output/2026-9.json
```

**New Record Response (`HTTP 201 Created`):**
```json
{
  "id": "01a11c54-42a8-7290-a15c-f83396bfe850",
  "period_date": "2026-10-01",
  "status": "created",
  "overwritten": false,
  "calculated_grand_total": 45890000,
  "sections_count": 8,
  "items_count": 34,
  "message": "Report for period 2026-10-01 created successfully."
}
```

**Duplicate Overwrite Response (`HTTP 200 OK`):**
If you send the same month again, the system replaces all prior sections and items atomically:
```json
{
  "id": "01a11c56-91bf-7489-b98a-ca0199182312",
  "period_date": "2026-10-01",
  "status": "overwritten",
  "overwritten": true,
  "calculated_grand_total": 45890000,
  "sections_count": 8,
  "items_count": 34,
  "message": "Report for period 2026-10-01 already existed and was successfully overwritten."
}
```

### Batch Ingesting All Historical Reports

Populate your database with all extracted reports using a single bash command:

```bash
for file in data/output/*.json; do
  echo "Ingesting $file..."
  curl -s -X POST http://localhost:8000/api/v1/reports \
       -H "Content-Type: application/json" \
       -d @"$file"
  echo ""
done
```

---

## ⚙️ Configuration & Environment Variables

Settings are managed via `backend/app/config.py` with `.env` support:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `BACKEND_HOST` | `0.0.0.0` | Server host bind address |
| `BACKEND_PORT` | `8000` | Server listening port |
| `BACKEND_DEBUG` | `false` | Enable SQL query logging and debug mode |
| `DATABASE_FILENAME`| `expenses.db` | SQLite filename stored inside `data/storage/` |

---

## 🧪 Running Automated Tests

Run the complete backend test suite:

```bash
# Run all backend tests
python -m pytest backend/tests/ -v

# Run with test coverage
python -m pytest backend/tests/ --cov=backend/app -v
```
