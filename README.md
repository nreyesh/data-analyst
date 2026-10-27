# Chilean Condominium Expense Reports Pipeline

An autonomous, self-healing pipeline for extracting, validating, and persistently storing Chilean condominium financial documents across two complementary domains:
* **`general` (*"Informe Gastos Comunes"*)**: Building-wide monthly operational and reserve fund expense reports.
* **`unit` (*"Nota de Cobro"*)**: Individual apartment billing statements detailing unit proration (*alícuota*), billed concepts, metered utility readings (*agua caliente*, *calefacción*), and unit-specific charges/fines.

This repository implements **Phase 1**, **Phase 2**, **Phase 3**, and **Phase 4** of the project roadmap:
* **Phase 1 (Dual-Domain PDF Extractors, Cross-Validation & Input Watcher)**: Dedicated LangGraph state machines utilizing Google Gemini multimodal vision for both `general` building reports (3-tier integer validation: `operational_total`, `fondos_total`, `grand_total`) and `unit` apartment bills (5-point deterministic integer validation: concepts table sum, hot water & heating utility cross-checks, unit charges sum, and `total_to_pay_amount` balance check), self-healing error correction loops, and zero-cost folder-routed batch/watch processing (`data/input/general/` and `data/input/unit/`).
* **Phase 2 (Storage Layer & Domain-Partitioned Spool Sync)**: Persistent FastAPI backend service and normalized 3NF SQLite database (`expenses.db`) storing building-wide reports (`expense_reports`, `expense_sections`, `expense_items`) alongside a 4-table unit billing schema (`unit_bills`, `unit_bill_concepts`, `unit_bill_consumptions`, `unit_bill_charges`) linked via `period_date` (`YYYY-MM-01`), with UUIDv7 primary keys, atomic duplicate overwrites (upserts), and resilient domain-partitioned cloud spooling (`data/output/{general,unit}/`).
* **Phase 3 (Analytics & Plotting Engine with Unit Intelligence)**: Autonomous LangGraph SQL-and-Matplotlib analytical agent featuring multi-analysis orchestration (1–3 SQL queries per request for MoM, YoY, and category variance), mandatory `[Mes Año]` period citations, a two-hop variance analysis loop, 3-layer schema privacy defense, strict HTTP-only Data Gateway isolation (whitelisting both building-wide and unit-level tables with zero direct DB access), an offline circuit breaker, and **Unit-Level Expense Intelligence** (a <15ms fast-path REST tool `get_unit_bill_details`, a dynamic modular knowledge base cutting prompt token overhead by ~50%, two-part causal decomposition bridging unit bills with building-wide cost drivers, and recipe-driven Matplotlib charting for stacked component evolutions and dual-axis utility tariffs).
* **Phase 4 (Conversational Supervisor Agent & Ephemeral Chainlit Web UI)**: Top-level LangGraph Conversational Supervisor (`agent/`) using the Tool-Calling Concierge pattern (`lookup_unit_bill`, `lookup_building_summary`, `run_deep_financial_analysis`, `check_system_status`) with ephemeral in-RAM multi-turn memory (8-message rolling window + `FocusContext` anaphora resolution), deterministic Python-level Role-Based Access Control (`admin` vs `resident` bound to `unit_code`), and an interactive **Chainlit Web Interface** (`frontend/`) featuring Firebase Auth (with offline dev fallback), clickable `cl.Action` clarification buttons, collapsible `cl.Step` transparency, and inline `cl.Image` chart rendering.

---

## 🏗️ Architecture Overview

```
                                      PHASE 1                                                PHASE 2
┌───────────────────────┐    ┌────────────────────────────────────────┐             ┌─────────────────────────┐
│  PDF Financial Docs   │    │       LangGraph Dual Extractors        │             │     FastAPI Backend     │
│  • Single PDF CLI     │───▶│  • General: Operational vs. Fondos     │             │  • /api/v1/reports      │
│    (--type general|   │    │    + 3-tier integer CLP validator      │             │  • /api/v1/unit-bills   │
│     unit)             │    │  • Unit: Concepts, Metered Utilities   │             │  • /api/v1/sync/status  │
│  • data/input/ Watch  │    │    & Charges + 5-point CLP validator   │             └────────────┬────────────┘
│    ├─ general/        │    └──────────────────┬─────────────────────┘                          │
│    └─ unit/           │                       │                                                │
└───────────────────────┘                       ▼                                                ▼
                                    ┌───────────────────────┐      HTTP Ingestion   ┌─────────────────────────┐
                                    │   Spool Queues        │──────────────────────▶│     SQLite Database     │
                                    │ data/output/{domain}/ │                       │   (data/storage/        │
                                    │  • pending/ (offline) │   HTTP Data Gateway   │      expenses.db)       │
                                    │  • synced/  (online)  │◀──────────────────────│  • Macro: expense_*     │
                                    │  • failed/  (dead)    │          │            │  • Micro: unit_bill*    │
                                    └───────────────────────┘          ▼            └─────────────────────────┘
                                                            ┌───────────────────────┐
                                                            │        PHASE 3        │
                                                            │  Data Analyst Engine  │
                                                            │  • Fast-Path REST     │
                                                            │    (Unit & Building)  │
                                                            │  • Dynamic Knowledge  │
                                                            │  • Two-Hop Variance   │
                                                            │  • Recipe A/B Sandbox │
                                                            └──────────▲────────────┘
                                                                       │ Tool Dispatch + RBAC Gate
┌───────────────────────┐                                   ┌──────────┴────────────┐
│        PHASE 4        │  WebSocket (Ephemeral RAM)        │        PHASE 4        │
│   Chainlit Web UI     │◀─────────────────────────────────▶│Supervisor Orchestrator│
│  • Firebase/Dev Auth  │                                   │  • Role Enforcement   │
│  • cl.Action Buttons  │                                   │    (Admin vs Resident)│
│  • cl.Step & cl.Image │                                   │  • Focus Anaphora     │
└───────────────────────┘                                   └───────────────────────┘
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

# Phase 4: Chainlit & Authentication (Optional Firebase Auth / Local Dev Auth)
CHAINLIT_AUTH_SECRET=replace_with_generated_chainlit_secret
FIREBASE_API_KEY=                   # Leave empty for local dev accounts, or set Firebase Web API Key
DEV_ADMIN_PASSWORD=admin123
DEV_RESIDENT_PASSWORD=residente123
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

### Step 2: Extract PDF Documents (Single File, Batch, or Folder Watcher)

With the backend running, you can extract a single PDF (`general` building report or `unit` apartment bill) or automate batch/watch processing from `data/input/general/` and `data/input/unit/`:

```bash
# Option A1: Extract a single building-wide report ("Informe Gastos Comunes", default --type general)
python -m data_extractor.cli examples/2026-9.pdf

# Option A2: Extract a single individual apartment bill ("Nota de Cobro", --type unit)
python -m data_extractor.cli examples/504b-2026-08.pdf --type unit

# Option B: One-shot batch process all PDFs in data/input/general/ and data/input/unit/
python -m data_extractor.cli --process-input

# Option C: Continuously watch data/input/general/ and data/input/unit/ for new PDFs
python -m data_extractor.cli --watch --interval 5
```

**What happens:**
1. **Domain-Specific Extraction**:
   - **`general` workflow**: Extracts operational categories (`expense_type: "operational"`) and reserve funds/surcharges (`general_topic: "Fondos"`, `expense_type: "fondos"`) with Gemini multimodal vision.
   - **`unit` workflow**: Extracts header metadata (`unit_code`, `period`, `alicuota_total`, `last_payment_date`, `last_payment_amount`, `total_to_pay_amount`) and the 3 core tables (*Conceptos*, *Consumos Generales*, and *Gastos por Unidad*).
2. **Deterministic Integer Validation & Self-Healing**:
   - **`general` validator**: Verifies 3-tier integer arithmetic (`operational_total`, `fondos_total`, and `grand_total`) against the PDF summary table (`TOTAL GASTOS COMUNES` and `TOTAL`).
   - **`unit` validator**: Verifies 5 deterministic rules (concepts table sum vs. `total_a_pagar_tabla_conceptos`, `AGUA CALIENTE` & `CALEFACCION` concept rows vs. metered utility totals, unit charges sum vs. `total_cargos_unidad`, and header `total_to_pay_amount` vs. sum of concepts). Any discrepancy triggers surgical retry prompts (up to `MAX_RETRIES=2`).
3. **Persistence & Spooling**: Validated payloads are posted directly to the backend (`POST /api/v1/reports` or `POST /api/v1/unit-bills`) and archived in `data/output/{general,unit}/synced/` (or staged in `data/output/{general,unit}/pending/` if the backend is offline).
4. **Input Lifecycle Management**: When using `--process-input` or `--watch`, validated source PDFs in `data/input/{general,unit}/` are moved to `processed/`, while unrecoverable PDFs are moved to `failed/`.
5. **Telemetry**: Model used, latency, and retry counts are printed to stdout.

---

### Step 3: Test Resilient Spooling (Offline Mode & Batch Sync)

If the backend service is temporarily offline, the extractor gracefully stages files in domain-specific pending queues rather than failing:

1. **Simulate Offline Ingestion:**
   Stop the backend server, then extract a building report or unit bill:
   ```bash
   python -m data_extractor.cli examples/2026-8.pdf
   python -m data_extractor.cli examples/504b-2026-08.pdf --type unit
   ```
   The extracted JSON payloads will be staged inside `data/output/general/pending/2026-8.json` and `data/output/unit/pending/504b-2026-08.json`.

2. **Check Queue Status:**
   ```bash
   python -m data_extractor.cli --status
   ```
   Shows pending, synced, and failed file counts across both `general` and `unit` queues.

3. **Reconcile Pending Documents:**
   Restart the backend service, then trigger batch reconciliation:
   ```bash
   python -m data_extractor.cli --sync
   ```
   All files in `data/output/general/pending/` and `data/output/unit/pending/` are automatically ingested into their respective SQLite tables and moved to `synced/`.

---

### Step 4: Query & Analyze the Stored Data

#### Option A: Via the Phase 3 Data Analyst Agent (Natural Language & Charts)
> **Note:** The Data Analyst agent enforces strict service isolation and queries the database exclusively over HTTP via the Backend Data Gateway (`POST /api/v1/analytics/execute-query`). Ensure the FastAPI server (`Step 1`) is running. If the backend server is offline, the agent trips a circuit breaker and returns a user-friendly service-unavailable notice without accessing SQLite directly.

```bash
# Run natural language trend & plotting queries via CLI (Building Domain)
python -m analytics.cli "Muestra la evolución del gasto total mensual en 2025. Incluye un gráfico."

# Run unit-level expense evolution and stacked component chart (Unit Domain - Recipe A)
python -m analytics.cli "Evolución de gastos de la unidad B-504 en 2026. Con gráfico."

# Run two-part causal investigation bridging unit bills with building-wide drivers (Cross-Domain)
python -m analytics.cli "¿Por qué subió tanto mi gasto común de agosto en el depto B-504?"

# Run admin multi-unit comparison natively in SQL
python -m analytics.cli "Compara el gasto común entre el depto B-504 y el B-506 en 2026"

# Run two-hop variance attribution (with interactive clarification if multi-phase)
python -m analytics.cli "¿A qué se debe el aumento del gasto en el 2026?"

# Run compound multi-analysis (MoM + YoY + Category Variance with [Mes Año] citations & multi-panel subplots)
python -m analytics.cli "Haz una revisión mes a mes del 2026, compáralo YoY con 2025 y dime qué categorías explican el último aumento"

# Fast-path unit snapshot lookup in Python (<15ms, zero token cost)
python -c "from analytics.tools import get_unit_bill_details; print(get_unit_bill_details('B-504'))"

# Or query via the high-level REST endpoint
curl -s -X POST http://localhost:8000/api/v1/analytics/query \
     -H "Content-Type: application/json" \
     -d '{"query": "Total gastado en consumos durante 2025", "include_chart": true}' | jq .
```

#### Option B: Via Persistence REST API
```bash
# List all ingested building-wide reports
curl -s http://localhost:8000/api/v1/reports | jq .

# Fetch detailed building report hierarchy by date (YYYY-MM-01)
curl -s http://localhost:8000/api/v1/reports/period/2026-09-01 | jq .

# List ingested unit bills (optionally filtered by unit_code or period_date)
curl -s "http://localhost:8000/api/v1/unit-bills?unit_code=B-504" | jq .
```

#### Option C: Directly via SQLite CLI
> **Note:** Run queries on a single line to avoid Bash backslash whitespace issues.

```bash
# View all stored monthly building reports with operational, reserve funds (fondos), and grand totals
sqlite3 -header -column data/storage/expenses.db "SELECT id, period_date, operational_total, fondos_total, calculated_grand_total, created_at FROM expense_reports;"

# View category breakdown with expense_type ('operational' vs 'fondos') and deterministic subtotals
sqlite3 -header -column data/storage/expenses.db "SELECT category_name, expense_type, calculated_subtotal FROM expense_sections;"

# View individual apartment bills (unit_code, tower, unit_number, period_date, alicuota_total, and calculated_total_month)
sqlite3 -header -column data/storage/expenses.db "SELECT id, unit_code, tower, unit_number, period_date, alicuota_total, calculated_total_month AS total_to_pay, calculated_dept_subtotal, calculated_consumo_total, calculated_unit_subtotal FROM unit_bills;"

# View billed concepts, metered utilities, and unit-specific charges/fines
sqlite3 -header -column data/storage/expenses.db "SELECT concept_name, normalized_name, detail, amount FROM unit_bill_concepts;"
sqlite3 -header -column data/storage/expenses.db "SELECT item_name, normalized_name, previous_reading, current_reading, consumption, unit_rate, total_amount FROM unit_bill_consumptions;"
sqlite3 -header -column data/storage/expenses.db "SELECT charge_name, normalized_name, charge_type, amount FROM unit_bill_charges;"
```

---

### Step 5: Launch the Phase 4 Conversational Supervisor & Chainlit Web UI

Ensure the FastAPI backend (`Step 1`) is running, then launch the interactive Chainlit web frontend:

```bash
# 1. Generate a Chainlit JWT signing secret (if not already set in .env)
chainlit create-secret

# 2. Launch the Chainlit web interface (default port 8501)
#    Tip: Use --headless (and omit -w) so writing chart PNGs into data/output/charts/
#    does not trigger mid-turn file-watcher reloads or X11 $DISPLAY warnings.
chainlit run frontend/app.py --headless --port 8501
```

Open **[http://localhost:8501](http://localhost:8501)** in your browser:

1. **Login & Role-Based Access Control (RBAC)**:
   - **Firebase Mode**: If `FIREBASE_API_KEY` is configured in `.env`, credentials are verified against the Firebase Identity Toolkit REST API (`accounts:signInWithPassword`) and mapped to server-side claims (`role` and `unit_code`).
   - **Local Development Mode** (when `FIREBASE_API_KEY` is empty): Sign in with the built-in server-side test profiles (or override via `DEV_USERS_JSON`):
     - **Admin Profile**: `admin@condominio.cl` / `admin123` (`role="admin"`, `unit_code=None`) — unrestricted access to macro building totals, any apartment unit, multi-unit cohorts, and system diagnostics (`/api/v1/sync/status`).
     - **Resident Profile (B-504)**: `residente504b@condominio.cl` / `residente123` (`role="resident"`, `unit_code="B-504"`) — automatically bound to apartment `B-504` at the Python tool execution layer (immune to prompt-injection privilege escalation) plus public building-wide totals.
     - **Resident Profile (A-102)**: `residente102a@condominio.cl` / `residente123` (`role="resident"`, `unit_code="A-102"`).

2. **Ephemeral Multi-Turn Chat & Interactive Features**:
   - **Zero Long-Term Storage**: Conversation history (`MAX_EPHEMERAL_MESSAGES=8`) and sticky entity context (`FocusContext`: `last_period`, `last_unit`, `last_route`) live strictly in volatile RAM during the active WebSocket session and are wiped when the tab closes.
   - **Fast-Path vs. Deep Analytics Dispatch**:
     - Single-period unit questions (*"¿Cuánto debo pagar este mes?"*) route to `lookup_unit_bill` (<15ms, 0 tokens).
     - Single-period building questions (*"¿Cuál fue el gasto común total del edificio en septiembre 2026?"*) and follow-ups (*"¿Y el mes anterior?"*) route to `lookup_building_summary` (<15ms, 0 tokens).
     - Time-series trends, variance drivers, and causal questions (*"¿Cómo ha evolucionado mi gasto?"*, *"¿Por qué subió en agosto?"*) route to `run_deep_financial_analysis`, rendering Matplotlib charts once via native `cl.Image(display="inline", size="large")` (with automatic sanitization of raw `![...](...)` markdown image links to prevent broken 404 placeholders).
   - **Live Progress Indicator & Role-Aware Step Details**: While a query or clarification resumption executes, a single `cl.Step` updates its title in real time with human-friendly Spanish stage labels (*"Analizando tu consulta..."* $\rightarrow$ *"Revisando el historial mensual de gastos..."* $\rightarrow$ *"Preparando el gráfico comparativo..."* $\rightarrow$ *"Ajustando el formato del gráfico..."* $\rightarrow$ *"Redactando el informe y conclusiones..."*). Expanding the completed step shows full SQL/REST telemetry for `admin` users and a non-technical verification summary for `resident` users (enforcing Zero Schema Exposure).
   - **Interactive Clarification Buttons**: When a query is ambiguous (e.g., an admin asking *"¿Cuánto debe el depto 504?"* across multiple towers, or multi-phase variance inflection points), Chainlit renders bordered outline buttons via `cl.AskActionMessage`, preserves the trajectory explanation alongside the selected choice, and automatically resumes the paused LangGraph `MemorySaver` checkpoint (`JsonPlusSerializer` with registered state models) on click.

3. **Programmatic Python Invocation (Without Web UI)**:
   ```bash
   python -c "
   from agent import UserContext, run_supervisor_turn
   ctx = UserContext(uid='u1', email='residente504b@condominio.cl', role='resident', unit_code='B-504')
   turn, msgs, focus = run_supervisor_turn('¿Cuánto debo pagar en agosto 2026?', user_context=ctx)
   print('Route:', turn.route)
   print(turn.reply)
   "
   ```

---

## 🧪 Running the Test Suite

The test suite contains 139 offline automated tests (covering Phase 1 `general` and `unit` extraction/validation/input watcher, Phase 2 persistence/spooling across both domains, Phase 3 analytics/multi-query/isolation/unit intelligence, and Phase 4 conversational supervisor routing, deterministic tenant RBAC, and Chainlit handlers) that execute in seconds without calling the live Gemini API:

```bash
# Run all 139 tests
python -m pytest -v

# Run Phase 1, Phase 3 & Phase 4 workflow/unit/supervisor tests
python -m pytest tests/ -v

# Run Phase 2 & Phase 3 backend/gateway tests
python -m pytest backend/tests/ -v

# Run with test coverage report
python -m pytest --cov=data_extractor --cov=analytics --cov=agent --cov=frontend --cov=backend/app -v
```

---

## 📡 API Reference Summary

| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/health` | Service and SQLite database connectivity status |
| `POST` | `/api/v1/reports` | Ingest new building report or overwrite existing month atomically |
| `GET` | `/api/v1/reports` | List all building reports with summary totals (sorted by date DESC) |
| `GET` | `/api/v1/reports/{id}` | Get building report hierarchy by UUIDv7 ID |
| `GET` | `/api/v1/reports/period/{date}` | Get building report hierarchy by period date (`YYYY-MM-01`) |
| `DELETE` | `/api/v1/reports/{id}` | Delete building report (cascades to all sections and items) |
| `POST` | `/api/v1/unit-bills` | Ingest new apartment bill or overwrite existing `(unit_code, period_date)` atomically |
| `GET` | `/api/v1/unit-bills` | List apartment bills (optional `?unit_code=`, `?tower=`, `?unit_number=`, and `?period_date=` filters) |
| `GET` | `/api/v1/unit-bills/{bill_id}` | Get full apartment bill hierarchy (concepts, consumptions, charges) by UUIDv7 ID |
| `DELETE` | `/api/v1/unit-bills/{bill_id}` | Delete apartment bill (cascades to concepts, consumptions, and charges) |
| `GET` | `/api/v1/sync/status` | Telemetry on pending, synced, and failed queues across `general` and `unit` domains |
| `POST` | `/api/v1/sync/pending` | Batch process and reconcile all files in `general` and `unit` pending spools |
| `POST` | `/api/v1/analytics/execute-query` | Read-only SQLite Data Gateway Proxy (table whitelist, 3s timeout, 500-row cap) |
| `POST` | `/api/v1/analytics/query` | High-level natural language analytics & charting endpoint |

---

## 📂 Project Structure

```
data-analyst/
├── AGENTS.md                   # Architectural guidelines and engineering rules
├── README.md                   # Project documentation and quickstart
├── agent/                      # Phase 4: Conversational Supervisor Agent & RBAC Orchestration
│   ├── prompts/
│   │   └── supervisor_system.md# Routing criteria, Chilean condo persona & anaphora instructions
│   ├── state.py                # UserContext, FocusContext, SupervisorDecision & SupervisorState
│   ├── supervisor.py           # LangGraph supervisor router, 8-message ephemeral window & tool execution
│   └── tools.py                # Deterministic RBAC tool wrappers (unit, building, deep analytics, status)
├── analytics/                  # Phase 3: LangGraph Analytics & Plotting Engine with Unit Intelligence
│   ├── cli.py                  # CLI entry point for natural language queries & charts
│   ├── config.py               # Analytics settings & environment configuration
│   ├── gateway_client.py       # Strict HTTP-only client for the Data Gateway Proxy
│   ├── nodes.py                # Planner, Trajectory Inspector/Evaluator, SQL Coder, Visualizer, Synthesizer
│   ├── prompt_loader.py        # Dynamic modular prompt loader assembling domain slices
│   ├── prompts/                # System instructions & modular data dictionary slices
│   │   └── data_dictionary/   # Modular slices (core, building, unit, cross_domain)
│   ├── sandbox.py              # Isolated headless subprocess runner for Matplotlib (Recipe A/B)
│   ├── state.py                # TypedDict & Pydantic state/output contracts (unit & domain aware)
│   ├── tools/                  # Fast-path REST tools (unit_tools.py & building_tools.py <15ms snapshots)
│   └── workflow.py             # LangGraph state machine, checkpointing & circuit breaker
├── backend/                    # Phase 2: Persistence & Data Gateway service
│   ├── app/
│   │   ├── main.py             # FastAPI entrypoint & lifespan
│   │   ├── models.py           # SQLAlchemy 3NF models (macro expense_* & micro unit_bill* tables)
│   │   ├── repository.py       # Atomic ingestion, upsert & query logic for reports and unit bills
│   │   ├── routes/             # API routes (reports.py, unit_bills.py, sync.py, analytics.py)
│   │   └── schemas.py          # Pydantic validation schemas
│   └── tests/                  # Integration tests for API, Data Gateway, and database
├── data/
│   ├── input/                  # Domain-partitioned incoming PDF folders for --process-input / --watch
│   │   ├── general/            # Building-wide "Informe Gastos Comunes" PDFs (processed/ & failed/)
│   │   └── unit/               # Apartment-level "Nota de Cobro" PDFs (processed/ & failed/)
│   ├── output/
│   │   ├── charts/             # Rendered analytical PNG charts
│   │   ├── general/            # Spool queues for building reports (pending/, synced/, failed/)
│   │   └── unit/               # Spool queues for apartment bills (pending/, synced/, failed/)
│   └── storage/
│       └── expenses.db         # SQLite persistent database (WAL mode)
├── data_extractor/             # Phase 1: LangGraph PDF extractors & input watcher
│   ├── cli.py                  # CLI tool (`--type {general,unit}`, `--process-input`, `--watch`, `--sync`, `--status`)
│   ├── config.py               # Settings & domain directory path helpers
│   ├── extractor.py            # Gemini multimodal extractors (`GeminiExtractor` & `UnitBillExtractor`)
│   ├── ground_truth.py         # Deterministic PDF text parser for 3-tier ground-truth totals
│   ├── models.py               # Pydantic domain models (`ExpenseReport`, `UnitBillReport`, etc.)
│   ├── prompts/                # Externalized system & retry prompt templates (general & unit)
│   ├── validator.py            # Deterministic integer arithmetic checkers (general 3-tier & unit 5-point)
│   └── workflow.py             # LangGraph state machines (`run_workflow` & `run_unit_workflow`) & spool routers
├── examples/                   # Sample Chilean condominium PDFs (building reports & unit bills)
├── frontend/                   # Phase 4: Ephemeral Chainlit Web Interface & Firebase Auth
│   ├── app.py                  # Chainlit callbacks (auth, chat start, message, action buttons, charts)
│   ├── auth.py                 # Firebase Identity Toolkit REST verification & local dev credential map
│   └── chainlit.md             # Chainlit welcome screen markdown
└── tests/                      # Phase 1, Phase 3 & Phase 4 unit and integration tests
```


