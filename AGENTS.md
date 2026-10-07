# AGENTS.md — Development Guidelines & Project Architecture

Behavioral rules, coding conventions, and architectural standards for AI coding assistants and human developers working on this codebase.

---

## 1. AI Assistant Behavioral Rules

These core rules prevent common LLM pitfalls (hallucinations, over-engineering, and scope drift).

### 1.1 Think Before Coding
- **Don't assume. Don't hide confusion. Surface tradeoffs.**
- State assumptions explicitly before implementing. If uncertain, ask.
- If multiple interpretations exist, present them — do not silently pick one.
- If a simpler approach exists, say so. Push back against over-complexity.

### 1.2 Simplicity First (KISS & YAGNI)
- **Minimum code that solves the problem. Nothing speculative.**
- No features beyond what was requested.
- No single-use abstractions, generic factory layers, or speculative configurability.
- No defensive error handling for impossible internal scenarios.
- *Senior Engineer litmus test:* "Would a senior engineer consider this over-engineered?" If yes, simplify.

### 1.3 Surgical Changes
- **Touch only what you must. Clean up only your own mess.**
- Do not reformat, reorganize, or "clean up" adjacent code or comments.
- Match existing naming, module structure, and coding style.
- Clean up unused imports, variables, or functions created by your changes. Leave pre-existing dead code untouched unless asked.

### 1.4 Goal-Driven Execution
- Define verifiable success criteria before touching code.
- Multi-step tasks must follow:
  ```
  1. [Step] → verify: [command / test]
  2. [Step] → verify: [command / test]
  ```
- Write targeted automated tests or verification scripts to prove correctness before concluding.

---

## 2. Project Vision & Architecture Roadmap

This project processes Chilean building expense reports (*"Informe Gastos Comunes"*) and will expand along a 4-phase lifecycle:

```
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│     Phase 1     │       │     Phase 2     │       │     Phase 3     │       │     Phase 4     │
│  PDF Extractor  │──────▶│ Storage Layer   │──────▶│ Analytics &     │──────▶│ Conversational  │
│  • LangGraph    │       │  • SQLite DB    │       │    Queries      │       │     Agent       │
│  • Gemini PDF   │       │  • Historical   │       │  • SQL tools    │       │  • Natural chat │
│  • Validation   │       │    snapshots    │       │  • Trends       │       │  • Tool calling │
└─────────────────┘       └─────────────────┘       └─────────────────┘       └─────────────────┘
```

### Module Boundary Guidelines
Keep boundaries clean so future phases plug in without rewriting past ones:

```
data_extractor/
├── models/         # Pydantic schemas (Single Source of Truth for domain entities)
├── extractor/      # LangGraph workflow, PDF ingestion, Gemini multimodal integration
├── storage/        # Database models & repository queries (SQLite / DuckDB)
├── analytics/      # Analytical calculations, aggregations, trend queries
├── agent/          # Conversational agent, chat memory, tool dispatch
└── cli.py          # Unified CLI entry points
```

---

## 3. Core Technical Conventions

### 3.1 Schema-First (Pydantic v2 as Single Source of Truth)
- Define domain structures once in `models/`.
- Use the same Pydantic models for:
  1. LLM structured output schemas.
  2. In-memory validation and data transfer.
  3. Database serialization/deserialization.
  4. Tool input/output schemas for the conversational agent.

### 3.2 Deterministic Financial Calculations
- **Never allow an LLM to perform arithmetic or sum totals.**
- The LLM's only job is transcription and semantic categorization.
- All totals, differences, and sanity checks must be calculated in pure Python code.
- Use integer representations for Chilean Pesos (CLP) to avoid floating-point errors (e.g. `13314915`, not `13314915.0` or `"$13.314.915"`).

### 3.3 LangGraph Self-Healing Workflows
- State definitions must be strictly typed using `TypedDict` or `BaseModel`.
- Separate extraction from validation into dedicated graph nodes.
- Validation nodes must produce actionable, surgical error messages (naming the exact section and amount difference).
- Limit self-healing retry iterations (maximum 2 retries) to avoid endless loops and token waste.

### 3.4 Modern Python Standards
- Python 3.11+ syntax (`str | None`, `list[dict]`, match-case where appropriate).
- Strict type annotations on all public functions, classes, and methods.
- Explicit imports (no `from module import *`).
- Configuration via environment variables (`.env` loaded through `python-dotenv` or `pydantic-settings`). Never commit hardcoded API keys.

### 3.5 4-Tier Configuration & Asset Separation
Maintain strict boundaries between secrets, settings, prompts, and storage:
1. **Secrets (`.env`)**: Sensitive credentials only (`GEMINI_API_KEY`). Strictly git-ignored.
2. **App Settings (`config.py`)**: Operational defaults (`DEFAULT_MODEL`, `MAX_RETRIES`, `OUTPUT_DIR`) managed via `pydantic-settings`.
3. **Prompt Assets (`prompts/`)**: System instructions and feedback templates stored in dedicated text/markdown template files, never hardcoded in Python logic.
4. **Data & Storage Paths (`data/`)**: Clear directory structure for incoming PDFs (`data/input/`), extracted JSONs (`data/output/`), and embedded database (`data/storage/`).

### 3.6 Database Practices (Future-Proofing for Phase 2 & 3)
- Use embedded relational storage (SQLite) with standard tables + JSON column support for flexible metadata.
- Separate write paths (ingestion of a validated `ExpenseReport`) from read paths (analytical queries).
- Queries exposed to the LLM agent must be parameterized to prevent SQL injection and hallucinations.

---

## 4. Verification & Testing Standards

- **Unit tests for models & validation:** Test date normalization, sum checks, and negative cases (e.g. mismatched subtotals triggering expected errors).
- **Offline fixtures:** Maintain a mocked extraction fixture (sample JSON from `2026-9.pdf`) so tests and agent components can be developed and run without incurring API costs.
- **End-to-end smoke tests:** A command like `pytest tests/` or running a test script against `examples/2026-9.pdf` should confirm end-to-end functionality.