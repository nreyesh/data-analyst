# Analytics & Plotting Engine (Phase 3)

Autonomous LangGraph analytical question-answering and charting engine for Chilean condominium expense reports (*"Gastos Comunes"*).

---

## 🌟 What It Does

1. **Natural Language to SQL**: Decomposes complex user questions and translates them into valid read-only SQLite queries (`expense_reports`, `expense_sections`, `expense_items`).
2. **Automated Matplotlib Charting**: Dynamically decides when a visual plot is beneficial, generates Python visualization code, and renders clean static PNG charts in `data/output/charts/`.
3. **Enterprise Data Gateway Proxy**: Decouples the AI agent from direct database access. Mediates all queries through `POST /api/v1/analytics/execute-query` with a 3.0s progress handler timeout and 500-row ceiling.
4. **3-Layer Defense Against Schema Leakage**:
   - **Scope Guard (Planner)**: Detects and rejects queries probing internal table names or infrastructure.
   - **Table Whitelist (Gateway)**: Strictly rejects `sqlite_master`, `sqlite_schema`, and `pragma_*` with HTTP 403 Forbidden.
   - **Zero Schema Exposure (Synthesizer)**: Answers exclusively in business domain terms without leaking table/column jargon.
5. **Subprocess Code Sandbox**: Executes generated plotting scripts in an isolated, headless (`MPLBACKEND=Agg`) subprocess with a 10s execution timeout.
6. **Deterministic Arithmetic**: Never allows an LLM to sum or calculate metrics; all numbers are computed by SQLite.

---

## 🚀 How to Run

### Option 1: Terminal CLI (Quickest)

Run queries directly from the command line:

```bash
# General trend query with chart
python -m analytics.cli "Muestra la evolución del gasto total mensual en 2025. Incluye un gráfico."
python -m analytics.cli "Muestrame como ha evolucionado el gasto comun historicamente"

# Category breakdown comparison
python -m analytics.cli "Compara el gasto de Mantenciones y Servicios entre 2025 y 2026"

# Output structured JSON instead of markdown
python -m analytics.cli "Resumen de gastos enero 2026" --json

# Specific and drilling question about the reason of why expences are going up
python -m analytics.cli "Como ha sido la evolucion del gasto comun durante el 2026?.A que se debe el aumento del gasto?"
python -m analytics.cli "A que se debe el aumento del gasto en el 2026?"
```

Charts are automatically saved to `data/output/charts/chart_<id>.png`.

---

### Option 2: Programmatic Python API

Import and run the workflow inside Python scripts, Jupyter notebooks, or future agent tools:

```python
from analytics.workflow import run_analysis

result = run_analysis("¿Cómo evolucionaron las mantenciones en el segundo semestre de 2025?")

print(result.summary)
if result.chart_file_path:
    print(f"Chart saved at: {result.chart_file_path}")
print(f"Query executed: {result.sql_query}")
print(f"Latency: {result.latency_seconds}s")
```

---

### Option 3: REST API (FastAPI Backend)

Start the backend server:

```bash
uvicorn backend.app.main:app --reload --port 8000
```

Then invoke the high-level analytics endpoint:

```bash
curl -X POST http://localhost:8000/api/v1/analytics/query \
     -H "Content-Type: application/json" \
     -d '{"query": "Total gastado en consumos durante 2025", "include_chart": true}'
```

---

## 🧪 How to Test

Run the automated test suite with `pytest`:

```bash
# Run all analytics unit, security, and sandbox tests
pytest tests/test_analytics_* backend/tests/test_analytics_* -v

# Run complete workspace test suite (70 tests)
pytest -v
```

### Key Test Coverage:
* `tests/test_analytics_sandbox.py`: Verifies PNG generation, 10s infinite loop timeouts, and error handling.
* `tests/test_analytics_workflow.py`: Verifies LangGraph routing, Scope Guard refusal, and self-healing retries.
* `backend/tests/test_analytics_gateway.py`: Verifies Table Whitelisting (HTTP 403 on `sqlite_master`), read-only enforcement, and 3s query timeout.
* `backend/tests/test_analytics_route.py`: Verifies the high-level `POST /api/v1/analytics/query` endpoint.
