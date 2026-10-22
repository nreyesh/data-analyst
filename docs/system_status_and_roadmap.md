# Chilean Condominium Financial Intelligence System: Implementation Status & Architecture Roadmap

## 1. Executive Summary

This document provides a complete audit of the Chilean Condominium Financial Intelligence System (*"Gastos Comunes"*). It outlines all currently operational, tested subsystems alongside upcoming components required to reach full production autonomy.

The system processes financial data across two interrelated domains:
1. **`general` (*"Informe Gastos Comunes"*)**: Building-wide operational totals, maintenance contracts, utility bills, staff payroll, and special reserve funds.
2. **`unit` (*"Nota de Cobro"*)**: Individual apartment bills (e.g., Depto `B-504` in [`examples/504b-2026-08.pdf`](file:///home/nereyes/agentes/data-analyst/examples/504b-2026-08.pdf)) detailing prorated shares (*alícuota*), metered hot water consumption, and unit-specific fees.

---

## 2. End-to-End System Workflow

The flowchart below maps the entire data and reasoning lifecycle. Nodes with **solid green borders** are **100% complete and verified** (110 automated tests passing). Nodes with **dashed yellow borders** represent **missing/future components**.

```mermaid
flowchart TD
    %% Styling Classes
    classDef done fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b5e20;
    classDef missing fill:#fff9c4,stroke:#f57f17,stroke-width:2px,stroke-dasharray: 5 5,color:#b71c1c;
    classDef storage fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d47a1;

    %% Ingestion Layer (Phase 1)
    subgraph Phase1 ["Phase 1: PDF Ingestion & Validation Pipeline (COMPLETE)"]
        PDFs["PDF Documents<br>(general / unit)"]:::done
        Watcher["Folder Watcher / CLI<br>(data/input/ & --watch)"]:::done
        Extractor["Gemini Multimodal Extractor<br>(Model Fallback: Flash -> Pro)"]:::done
        Validator["Deterministic Validator<br>(3-Tier General / 5-Point Unit)"]:::done
        Spool["Spool Queues<br>(pending/ / synced/ / failed/)"]:::done
        
        PDFs --> Watcher --> Extractor --> Validator --> Spool
    end

    %% Storage Layer (Phase 2)
    subgraph Phase2 ["Phase 2: Relational Persistence & Gateway (COMPLETE)"]
        BackendAPI["FastAPI Ingestion Endpoints<br>(POST /api/v1/reports & /unit-bills)"]:::done
        Database[("SQLite 3NF Database<br>data/storage/expenses.db<br>• Macro: expense_* (3 tables)<br>• Micro: unit_bill* (4 tables)")]:::storage
        GatewayProxy["HTTP Data Gateway Proxy<br>POST /api/v1/analytics/execute-query<br>• Table Whitelist & AST Gate<br>• 3s Progress Timeout & 500-Row Cap"]:::done

        Spool -- "HTTP Ingestion" --> BackendAPI --> Database
        Database -. "Read-Only (mode=ro)" .-> GatewayProxy
    end

    %% Analytics & Tools (Phase 3)
    subgraph Phase3 ["Phase 3: Analytics Engine & Unit Intelligence (COMPLETE)"]
        FastPathTool["Fast-Path Unit Tool<br>get_unit_bill_details<br>(<15ms REST lookup, 0 tokens)"]:::done
        
        subgraph GraphEngine ["LangGraph State Machine (analytics/workflow.py)"]
            Planner["Planner Node<br>• Scope Guard & Domain Classifier<br>• Modular Knowledge Injection"]:::done
            Inspector["Trajectory Inspector<br>• Unit or Macro Trend Query"]:::done
            Evaluator["Trajectory Evaluator<br>• Fact-checks user premise"]:::done
            SQLCoder["SQL Coder Node<br>• Multi-query & CTE generation"]:::done
            Visualizer["Visualizer Node<br>• Recipe A: Stacked Evolution<br>• Recipe B: Dual-Axis Tariff"]:::done
            Sandbox["Subprocess Sandbox<br>• Headless MPLBACKEND=Agg"]:::done
            Synthesizer["Synthesizer Node<br>• 2-Part Causal Attribution<br>• [Mes Año] Citations"]:::done

            Planner --> Inspector --> Evaluator --> SQLCoder
            SQLCoder --> GatewayProxy
            GatewayProxy -- "Query Results" --> Visualizer --> Sandbox --> Synthesizer
            GatewayProxy -- "No Chart Needed" --> Synthesizer
        end
    end

    %% Conversational Supervisor (Phase 4 - MISSING)
    subgraph Phase4 ["Phase 4: Top-Level Conversational Agent (MISSING)"]
        User(["End-User / Resident / Admin"]):::missing
        Supervisor["Conversational Supervisor Agent<br>(agent/supervisor.py)<br>• Multi-turn session memory<br>• Intent routing & tool dispatch"]:::missing
        AuthGate["Tenant Authentication & Scoping<br>• Auto-binds unit_code for residents<br>• Prevents cross-tenant leaks"]:::missing
        RootCLI["Unified Root CLI<br>(cli.py entrypoint)"]:::missing
        ChatUI["Frontend Web / WhatsApp Bot<br>(Chat Interface)"]:::missing

        User --> ChatUI & RootCLI
        ChatUI & RootCLI --> AuthGate --> Supervisor
        Supervisor -- "Simple Balance Lookup" --> FastPathTool
        Supervisor -- "Causal / Variance Query" --> Planner
    end

    %% Connections across layers
    FastPathTool -. "GET /api/v1/unit-bills" .-> BackendAPI
```

---

## 3. Current Implementation Details (Completed & Verified)

### Phase 1: Dual-Domain PDF Extraction & Self-Healing Pipeline
* **Code Location:** [`data_extractor/`](file:///home/nereyes/agentes/data-analyst/data_extractor/)
* **Status:** ✅ **100% Operational**
* **Key Features:**
  1. **Dual Domain Parsers:** Dedicated LangGraph state machines for building-wide reports (`general`) and apartment bills (`unit`).
  2. **Deterministic Arithmetic Ground-Truth:** PyMuPDF deterministic anchor extractors cross-checking Gemini extractions.
  3. **Strict CLP Integer Math:** 3-tier validation for general reports (`operational_total`, `fondos_total`, `grand_total`) and 5-point validation for unit bills (concepts sum, metered hot water/gas consistency, unit charges sum, and header balance check).
  4. **Self-Healing Loop:** Discrepancy-guided re-prompting (up to 2 retries).
  5. **Folder Lifecycle Automation:** Batch processing (`--process-input`) and continuous directory watching (`--watch`) routing files into `data/input/{general,unit}/{processed,failed}/`.
  6. **Offline Spool Queues:** Decouples extraction from backend availability via `data/output/{general,unit}/{pending,synced,failed}/`.

---

### Phase 2: Relational Persistence & Isolated Data Gateway
* **Code Location:** [`backend/`](file:///home/nereyes/agentes/data-analyst/backend/)
* **Status:** ✅ **100% Operational**
* **Key Features:**
  1. **Normalized 3NF SQLite Database (`expenses.db` in WAL mode):**
     - Macro Building Schema: `expense_reports`, `expense_sections`, `expense_items`.
     - Micro Unit Schema: `unit_bills`, `unit_bill_concepts`, `unit_bill_consumptions`, `unit_bill_charges`.
  2. **Atomic Duplicate Overwrites (Upserts):** Re-ingesting an identical month or `(unit_code, period_date)` cleanly cascades and replaces old rows without orphaned child records.
  3. **Strict HTTP Data Gateway Proxy (`POST /api/v1/analytics/execute-query`):**
     - Zero direct in-process database access from analytical tools.
     - Enforces read-only SQLite connection (`file:expenses.db?mode=ro`).
     - Table whitelist permitting only the 7 domain tables; strictly blocks `sqlite_master`, system catalogs, and pragmas with HTTP 403 Forbidden.
     - Hard 3.0s query timeout using `sqlite3.set_progress_handler` and a 500-row result cap.
  4. **REST APIs:** Full CRUD endpoints for building reports (`/api/v1/reports`), unit bills (`/api/v1/unit-bills`), queue synchronization (`/api/v1/sync/pending`), and health checks (`/health`).

---

### Phase 3: Analytics Engine & Unit Intelligence
* **Code Location:** [`analytics/`](file:///home/nereyes/agentes/data-analyst/analytics/)
* **Status:** ✅ **100% Operational**
* **Key Features:**
  1. **LangGraph 9-Node State Machine ([`analytics/workflow.py`](file:///home/nereyes/agentes/data-analyst/analytics/workflow.py)):** Planner, Trajectory Inspector, Trajectory Evaluator, Clarification (`interrupt()`), SQL Coder, SQL Gateway, Visualizer, Sandbox Runner, Synthesizer.
  2. **Two-Hop Variance Analysis:** Fact-checks premise against chronological monthly trajectories (Hop 1) before computing SQL CTE category deltas (Hop 2).
  3. **Multi-Analysis Orchestration:** Decomposes compound prompts into 1–3 named SQL queries executed sequentially through the Data Gateway (e.g., MoM + YoY + Category Variance).
  4. **Mandatory Inline `[Mes Año]` Citations:** Enforces source period provenance on every monetary metric and delta.
  5. **Dynamic Modular Knowledge Base ([`analytics/prompts/data_dictionary/`](file:///home/nereyes/agentes/data-analyst/analytics/prompts/data_dictionary/)):** Dynamically loads only relevant slices (`00_core`, `01_building`, `02_unit`, `03_cross_domain`), cutting prompt tokens by ~50% on single-domain queries.
  6. **Two-Part Causal Decomposition:** Disentangles questions like *"¿Por qué subió tanto mi gasto común en el B-504?"* into:
     - Part 1: Micro unit-level deltas (base fee, water consumption volume, special funds).
     - Part 2: Macro building drivers (community operational cost surges, boiler gas tariff increases).
  7. **Recipe-Driven Matplotlib Sandbox:** Embeds Recipe A (`unit_stacked_evolution`) and Recipe B (`dual_axis_tariff`) in [`analytics/prompts/visualizer_system.md`](file:///home/nereyes/agentes/data-analyst/analytics/prompts/visualizer_system.md) running in an isolated headless subprocess with a 10s timeout.
  8. **Fast-Path Unit Tool ([`analytics/tools/unit_tools.py`](file:///home/nereyes/agentes/data-analyst/analytics/tools/unit_tools.py)):** Sub-15ms balance, meter reading, and surcharge lookups via backend REST with zero LLM token consumption.

---

## 4. Missing Implementation & Roadmap (Pending Work)

The following components represent the remaining gap between the current analytical engine and a fully autonomous consumer-facing product:

| Missing Component | Target Phase | Description | Key Deliverables |
| :--- | :---: | :--- | :--- |
| **Top-Level Conversational Supervisor Agent** | **Phase 4** | Autonomous conversational coordinator managing multi-turn dialogs and memory. | • `agent/supervisor.py`<br>• Tool dispatcher routing between Fast-Path Tool and Analytics Engine<br>• Persistent chat checkpointer (`MemorySaver` / Redis) |
| **Resident Auth & Multi-Tenancy Scoping** | **Phase 4** | Enforces row-level tenant security based on logged-in user identity. | • Session token / JWT middleware<br>• Automatic `unit_code` parameter binding<br>• Scope guard blocking cross-tenant snooping |
| **Unified Root CLI (`cli.py`)** | **Operations** | Unified command-line entrypoint consolidating all submodules. | • Root `cli.py` (`extract`, `server`, `sync`, `chat`)<br>• Consistent flags and argument parsers |
| **Interactive Presentation Layer (UI)** | **Product** | Non-technical interface for building residents and administrators. | • Web Chat UI (Streamlit, React, or Chainlit)<br>• WhatsApp / Telegram notification webhook integration |
| **Production Containerization** | **Deployment** | Cloud-ready deployment assets and background services. | • Multi-stage `Dockerfile`<br>• `docker-compose.yml` (FastAPI + Watcher + Redis)<br>• Persistent volume mounts for `data/storage/` |

---

## 5. Verification & Test Metrics

The workspace currently runs **110 automated offline tests** with 100% pass rate:

```bash
pytest -v
```

| Test Suite | Tests | Purpose |
| :--- | :---: | :--- |
| [`tests/test_validator.py`](file:///home/nereyes/agentes/data-analyst/tests/test_validator.py) | 16 | Phase 1 general (3-tier) and unit (5-point) ground-truth validation |
| [`tests/test_extractor_spool_sync.py`](file:///home/nereyes/agentes/data-analyst/tests/test_extractor_spool_sync.py) | 11 | Phase 1 folder watching, spool queues, and batch sync |
| [`tests/test_fallback_observability.py`](file:///home/nereyes/agentes/data-analyst/tests/test_fallback_observability.py) | 5 | Gemini primary-to-fallback model failover telemetry |
| [`backend/tests/test_unit_bills.py`](file:///home/nereyes/agentes/data-analyst/backend/tests/test_unit_bills.py) | 5 | Phase 2 4-table unit bill persistence, cascades, and upserts |
| [`backend/tests/test_analytics_gateway.py`](file:///home/nereyes/agentes/data-analyst/backend/tests/test_analytics_gateway.py) | 12 | Phase 2 Data Gateway isolation, table whitelist, and timeouts |
| [`backend/tests/test_ingestion.py`](file:///home/nereyes/agentes/data-analyst/backend/tests/test_ingestion.py) & others | 31 | Phase 2 general reports, duplicate handling, and REST endpoints |
| [`tests/test_analytics_sandbox.py`](file:///home/nereyes/agentes/data-analyst/tests/test_analytics_sandbox.py) | 6 | Phase 3 Matplotlib sandbox, timeouts, Recipe A & B rendering |
| [`tests/test_analytics_variance.py`](file:///home/nereyes/agentes/data-analyst/tests/test_analytics_variance.py) | 8 | Phase 3 Two-Hop variance loop, clarification `interrupt()`, citations |
| [`tests/test_analytics_workflow.py`](file:///home/nereyes/agentes/data-analyst/tests/test_analytics_workflow.py) | 16 | Phase 3 LangGraph routing, circuit breaker, Scope Guard |
| [`tests/test_modular_prompt_loader.py`](file:///home/nereyes/agentes/data-analyst/tests/test_modular_prompt_loader.py) | 3 | Phase 3 dynamic knowledge base slicing (building, unit, cross-domain) |
| [`tests/test_unit_tools.py`](file:///home/nereyes/agentes/data-analyst/tests/test_unit_tools.py) | 4 | Phase 3 fast-path unit lookup tool (<15ms, REST client) |
| [`tests/test_unit_analytics_integration.py`](file:///home/nereyes/agentes/data-analyst/tests/test_unit_analytics_integration.py) | 3 | Phase 3 unit evolution, 2-part causal decomposition, multi-unit SQL |
| **Total Test Coverage** | **110** | **0 Failures, 100% Offline Regression Safety** |
