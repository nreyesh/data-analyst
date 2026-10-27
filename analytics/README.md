# Analytics & Plotting Engine (Phase 3)

Autonomous LangGraph analytical question-answering and charting engine for Chilean condominium expense reports (*"Gastos Comunes"*).

---

## 🌟 What It Does

1. **Natural Language to SQL**: Decomposes complex user questions and translates them into valid read-only SQLite queries (`expense_reports`, `expense_sections`, `expense_items`).
2. **Automated Matplotlib Charting**: Dynamically decides when a visual plot is beneficial, generates Python visualization code, and renders clean static PNG charts in `data/output/charts/`.
3. **Strict HTTP Data Gateway Isolation & Circuit Breaker**:
   - **Zero In-Process DB Access**: The `analytics` package never imports `backend` internals or opens `data/storage/expenses.db` directly. All queries are mediated over HTTP (`POST /api/v1/analytics/execute-query`) with a 3.0s progress handler timeout and 500-row ceiling.
   - **Offline Circuit Breaker**: If the FastAPI backend is unreachable, the workflow immediately trips a circuit breaker (`gateway_unavailable=True`), skipping SQL self-healing retries and LLM synthesis to return a deterministic Spanish service-unavailable notice (`### ⚠️ Servicio de Datos No Disponible`).
4. **3-Layer Defense Against Schema Leakage**:
   - **Scope Guard (Planner)**: Detects and rejects queries probing internal table names or infrastructure.
   - **Table Whitelist (Gateway)**: Strictly rejects `sqlite_master`, `sqlite_schema`, and `pragma_*` with HTTP 403 Forbidden.
   - **Zero Schema Exposure (Synthesizer)**: Answers exclusively in business domain terms without leaking table/column jargon.
5. **Two-Hop Variance Analysis & Hop 1 Trend Verification**: Fact-checks user premises against chronological monthly trajectories across all years in SQLite (`trajectory_inspector_node` -> `trajectory_evaluator_node` in Hop 1), pauses via `interrupt()` (`clarification_node`) when a query premise is contradicted by the database numbers or spans multiple inflection phases, and computes category delta attributions (`delta_clp`) via SQLite CTEs (`sql_coder_node` in Hop 2). When invoked from the Phase 4 Conversational Supervisor, the Supervisor stores `active_analytics_thread_id` in `FocusContext` and either resumes the paused Hop 1 checkpoint directly into `sql_coder_node` on user selection or cancels the thread if the user switches topics.
6. **Multi-Analysis Orchestration (MoM + YoY + Variance) & Multi-Panel Subplots**: Decomposes compound prompts into 1–3 named analytical sub-tasks (`sub_analyses`), generates and executes 1–3 read-only SQL queries sequentially through the HTTP Data Gateway Proxy, and renders vertically stacked multi-panel Matplotlib figures (`plt.subplots(nrows, 1)`) when multiple datasets are returned.
7. **Mandatory `[Mes Año]` Period Citations**: Enforces explicit period provenance columns (`period_date`, `baseline_period`, `peak_period`, or `start_period`/`end_period`/`months_count`) so every monetary figure and delta in the executive report cites its exact source month and year (e.g., `$49.387.905 CLP [Mayo 2026]` vs `$56.907.702 CLP [Julio 2026]`).
8. **Subprocess Code Sandbox**: Executes generated plotting scripts in an isolated, headless (`MPLBACKEND=Agg`) subprocess with a 10s execution timeout.
9. **Deterministic Arithmetic**: Never allows an LLM to sum or calculate metrics; all numbers are computed by SQLite.
10. **Pre-Verified Supervisor Scope & Flexible Unit Disambiguation**:
    - Accepts pre-verified `knowledge_domain` (`"building"`, `"unit"`, or `"cross_domain"`) and `target_unit` directly from the Phase 4 Conversational Supervisor (which handles Turn 1 Building vs. Unit scope clarification, Admin missing-unit prompts, and DB-backed missing-tower resolution upfront across all tools).
    - When `knowledge_domain="cross_domain"` is passed (e.g., when a resident selects `"Ambos (impacto del edificio en mi unidad)"`), `planner_node` automatically enforces a 2-part causal decomposition (unit bill component delta + macro building variance).
    - Retains standalone CLI support for natural Chilean apartment syntax variations (`"506b"`, `"B-506"`, `"depto 506 torre b"`) into decoupled relational dimensions (`tower="B"`, `unit_number=506`), dynamic DB-backed tower lookup (`SELECT DISTINCT tower FROM unit_bills WHERE unit_number = :num`), and the tower-only Granularity Guard.

---

## 🚀 How to Run

> **Prerequisite:** Because `analytics` enforces strict service isolation, ensure the FastAPI backend (Data Gateway) is running before executing live queries:
> ```bash
> uvicorn backend.app.main:app --reload --port 8000
> ```

### Option 1: Terminal CLI (Quickest)

Run queries directly from the command line:

```bash
# General trend query with chart
python -m analytics.cli "Muestra la evolución del gasto total mensual en 2025. Incluye un gráfico."
python -m analytics.cli "Muestrame como ha evolucionado el gasto comun historicamente"

# Category breakdown comparison
python -m analytics.cli "Compara el gasto de Mantenciones y Servicios entre 2025 y 2026"

# Compound Multi-Analysis (MoM + YoY + Category Variance in a single run)
python -m analytics.cli "Haz una revisión mes a mes del 2026, compáralo interanualmente (YoY) con 2025 y explica qué categorías impulsaron el último aumento"

# Output structured JSON instead of markdown
python -m analytics.cli "Resumen de gastos enero 2026" --json

# Specific and drilling question about the reason why expenses are going up (Two-Hop Variance Loop)
python -m analytics.cli "Como ha sido la evolucion del gasto comun durante el 2026?.A que se debe el aumento del gasto?"
python -m analytics.cli "A que se debe el aumento del gasto en el 2026?"

# Individual unit queries with flexible syntax (e.g. 506b or depto 506)
python -m analytics.cli "Muestrame la evolucion del gasto comun para el depto 506b"
python -m analytics.cli "Muestrame la evolucion del gasto comun para el depto 506"  # Triggers interactive clarification if tower is omitted
```

Charts are automatically saved to `data/output/charts/chart_<id>.png`.

---

### Option 2: Programmatic Python API

Import and run the workflow inside Python scripts, Jupyter notebooks, or future agent tools:

```python
from analytics.workflow import run_analysis

# Standard query (auto-detects domain)
result = run_analysis("¿Cómo evolucionaron las mantenciones en el segundo semestre de 2025?")

# Or with pre-verified domain & unit from the Phase 4 Conversational Supervisor
result_cross = run_analysis(
    query="¿Por qué subió el gasto común este año?",
    target_unit="B-504",
    knowledge_domain="cross_domain",
)

print(result.summary)
if result.chart_file_path:
    print(f"Chart saved at: {result.chart_file_path}")
print(f"Query executed: {result.sql_query}")
print(f"Latency: {result.latency_seconds}s")
```

---

### Option 3: REST API (FastAPI Backend)

With the backend server running on port `8000`, invoke the high-level analytics endpoint:

```bash
curl -X POST http://localhost:8000/api/v1/analytics/query \
     -H "Content-Type: application/json" \
     -d '{"query": "Total gastado en consumos durante 2025", "include_chart": true}'
```

---

## 🧪 How to Test

Run the automated test suite with `pytest`:

```bash
# Run all analytics unit, variance, security, and sandbox tests
pytest tests/test_analytics_* backend/tests/test_analytics_* -v

# Run complete workspace test suite (145 tests)
pytest -v
```

### Key Test Coverage:
* `tests/test_analytics_sandbox.py`: Verifies PNG generation, 10s infinite loop timeouts, and error handling.
* `tests/test_analytics_workflow.py`: Verifies LangGraph routing, Scope Guard refusal, self-healing retries, multi-query execution/retry, multi-panel sandbox plotting, strict HTTP gateway isolation, and the offline circuit breaker.
* `tests/test_analytics_variance.py`: Verifies Two-Hop variance analysis, `interrupt()` clarification pause/resume, 3-query MoM + YoY + Variance orchestration with `[Mes Año]` citations, and CTE delta attribution via `TestClient` gateway fixtures.
* `tests/test_planner_unit_focus.py`: Verifies Unit-First Focus Guard, `cross_domain` preservation, and pre-verified `knowledge_domain` propagation from the Conversational Supervisor.
* `tests/test_supervisor_agent.py`: Verifies LLM-first Supervisor routing, Turn 1 Building vs. Unit scope clarification, Turn 2+ `FocusContext` inheritance, DB-backed missing-tower clarification, and Hop 1 trend verification resumption/topic-switching.
* `backend/tests/test_analytics_gateway.py`: Verifies Table Whitelisting (HTTP 403 on `sqlite_master`), read-only enforcement, and 3s query timeout.
* `backend/tests/test_analytics_route.py`: Verifies the high-level `POST /api/v1/analytics/query` endpoint.
