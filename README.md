# Chilean Condominium Expense Reports Pipeline

An autonomous, self-healing pipeline for extracting, validating, and persistently storing Chilean building expense reports (*"Informe Gastos Comunes"*).

This repository currently implements **Phase 1** and **Phase 2** of the project roadmap:
* **Phase 1 (PDF Extractor & Cross-Validation)**: LangGraph state machine utilizing Google Gemini multimodal vision, deterministic integer financial math validation, and self-healing error correction loops.
* **Phase 2 (Storage Layer & Spool Sync)**: Persistent FastAPI backend service and normalized 3NF SQLite database with UUIDv7 primary keys, automatic duplicate overwrites (upserts), and resilient cloud spooling.

---

## 🏗️ Architecture Overview

```
                                      PHASE 1                                                PHASE 2
┌───────────────────────┐    ┌────────────────────────────────────────┐             ┌─────────────────────────┐
│                       │    │        LangGraph Extractor             │             │     FastAPI Backend     │
│   PDF Expense Report  │───▶│  • Gemini Multimodal Vision            │             │  • REST API endpoints   │
│ (examples/2026-9.pdf) │    │  • Deterministic integer CLP validator │             │  • Lifespan management  │
│                       │    │  • Self-healing retry feedback loop    │             │  • Duplicate detection  │
└───────────────────────┘    └──────────────────┬─────────────────────┘             └────────────┬────────────┘
                                                │                                                │
                                                ▼                                                ▼
                                    ┌───────────────────────┐                       ┌─────────────────────────┐
                                    │      Spool Queue      │   HTTP Ingestion      │     SQLite Database     │
                                    │  • pending/ (offline) │──────────────────────▶│   (data/storage/        │
                                    │  • synced/  (online)  │                       │      expenses.db)       │
                                    │  • failed/  (dead)    │                       │  • 3NF relational schema│
                                    └───────────────────────┘                       └─────────────────────────┘
```

---

## 📋 Prerequisites & Setup

### 1. Environment Requirements
- Python 3.11+
- SQLite 3.35+
- Google Gemini API Key

### 2. Virtual Environment & Dependencies Setup
Create an isolated Python virtual environment and install the pinned dependencies:

```bash
# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate       # On Linux / macOS
# .venv\Scripts\activate        # On Windows

# Upgrade pip and install exact pinned dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

Verify the environment installation:
```bash
pytest
```

### 3. Configure Environment (`.env`)
Create or edit `.env` in the repository root:
```dotenv
# Required for Phase 1 Extractor
GEMINI_API_KEY=your_gemini_api_key_here

# Extractor Models (defaults)
DEFAULT_MODEL=gemini-3.5-flash-lite
FALLBACK_MODEL=gemini-3.1-flash-lite
MAX_RETRIES=2

# Backend & Persistence Service
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
BACKEND_BASE_URL=http://localhost:8000
DATABASE_FILENAME=expenses.db
```

---

## 🚀 Step-by-Step Quickstart

### Step 1: Start the Backend Service
Run the persistent FastAPI server from the project root:

```bash
# Production entrypoint
python -m backend.app.main

# Or with live reload for development:
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Verify service health:
```bash
curl -s http://localhost:8000/health
```
Expected response:
```json
{
  "status": "healthy",
  "database": "connected",
  "database_path": ".../data/storage/expenses.db"
}
```

Interactive API documentation will be accessible at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

### Step 2: Extract a PDF Expense Report

With the backend running, process a sample PDF report from `examples/`:

```bash
python -m data_extractor.cli examples/2026-9.pdf
```

**What happens:**
1. The LangGraph workflow extracts the statement data with Gemini multimodal vision.
2. The validator calculates integer subtotals deterministically and compares them against declared sums.
3. If totals match, the extractor directly posts the report to the backend (`POST /api/v1/reports`).
4. The output is archived in `data/output/synced/2026-9.json`.
5. Telemetry (model used, latency, retry counts) is printed to stdout.

---

### Step 3: Test Resilient Spooling (Offline Mode & Batch Sync)

If the backend service is temporarily offline, the extractor gracefully stages files rather than failing:

1. **Simulate Offline Ingestion:**
   Stop the backend server, then extract another report:
   ```bash
   python -m data_extractor.cli examples/2026-8.pdf
   ```
   The report will be staged inside `data/output/pending/2026-8.json`.

2. **Check Queue Status:**
   ```bash
   python -m data_extractor.cli --status
   ```
   Shows pending, synced, and failed file counts.

3. **Reconcile Pending Reports:**
   Restart the backend service, then trigger batch reconciliation:
   ```bash
   python -m data_extractor.cli --sync
   ```
   All files in `pending/` are automatically ingested into the database and moved to `synced/`.

---

### Step 4: Query the Stored Data

#### Option A: Via REST API
```bash
# List all ingested reports
curl -s http://localhost:8000/api/v1/reports | jq .

# Fetch detailed report hierarchy by date (YYYY-MM-01)
curl -s http://localhost:8000/api/v1/reports/period/2026-10-01 | jq .
```

#### Option B: Directly via SQLite CLI
> **Note:** Run queries on a single line to avoid Bash backslash whitespace issues.

```bash
# View all stored monthly reports
sqlite3 -header -column data/storage/expenses.db "SELECT id, period_date, calculated_grand_total, created_at FROM expense_reports;"

# View category breakdown with deterministic subtotals
sqlite3 -header -column data/storage/expenses.db "SELECT category_name, calculated_subtotal FROM expense_sections;"

# View expense items with lowercase normalized names for historical trend analysis
sqlite3 -header -column data/storage/expenses.db "SELECT name, normalized_name, amount FROM expense_items LIMIT 10;"
```

---

## 🧪 Running the Test Suite

The test suite contains 29 offline automated tests that execute in seconds without calling the Gemini API:

```bash
# Run all tests
python -m pytest -v

# Run Phase 1 extractor tests
python -m pytest tests/ -v

# Run Phase 2 backend & storage tests
python -m pytest backend/tests/ -v

# Run with test coverage report
python -m pytest --cov=data_extractor --cov=backend/app -v
```

---

## 📡 API Reference Summary

| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/health` | Service and SQLite database connectivity status |
| `POST` | `/api/v1/reports` | Ingest new report or overwrite existing month atomically |
| `GET` | `/api/v1/reports` | List all reports with summary totals (sorted by date DESC) |
| `GET` | `/api/v1/reports/{id}` | Get report hierarchy by UUIDv7 ID |
| `GET` | `/api/v1/reports/period/{date}` | Get report hierarchy by period date (`YYYY-MM-01`) |
| `DELETE` | `/api/v1/reports/{id}` | Delete report (cascades to all sections and items) |
| `GET` | `/api/v1/sync/status` | Telemetry on pending, synced, and failed queues |
| `POST` | `/api/v1/sync/pending` | Batch process and reconcile all files in pending spool |

---

## 📂 Project Structure

```
data-analyst/
├── AGENTS.md                   # Architectural guidelines and engineering rules
├── README.md                   # Project documentation and quickstart
├── backend/                    # Phase 2: Persistence service
│   ├── app/
│   │   ├── main.py             # FastAPI entrypoint & lifespan
│   │   ├── models.py           # SQLAlchemy 3NF models (UUIDv7, cascading delete)
│   │   ├── repository.py       # Atomic ingestion, upsert & query logic
│   │   ├── routes/             # API routes (reports.py, sync.py)
│   │   └── schemas.py          # Pydantic validation schemas
│   └── tests/                  # Integration tests for API and database
├── data/
│   ├── input/                  # Incoming PDF files
│   ├── output/
│   │   ├── pending/            # Staged reports waiting for DB sync
│   │   ├── synced/             # Successfully committed reports
│   │   └── failed/             # Dead-letter queue for rejected reports
│   └── storage/
│       └── expenses.db         # SQLite persistent database (WAL mode)
├── data_extractor/             # Phase 1: LangGraph PDF extraction
│   ├── cli.py                  # CLI tool (`--sync`, `--status`, PDF extraction)
│   ├── config.py               # Settings & environment configuration
│   ├── extractor.py            # Gemini multimodal invocation & fallback
│   ├── models.py               # Pydantic domain models
│   ├── validator.py            # Deterministic integer arithmetic checker
│   └── workflow.py             # LangGraph state machine & spool router
├── examples/                   # Real Chilean expense reports (PDF format)
└── tests/                      # Phase 1 unit and integration tests
```
