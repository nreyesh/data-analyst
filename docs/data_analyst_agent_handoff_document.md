# Technical Handoff Document: Production-Grade Data Analyst Agent

## 1. System Overview & Context
This document summarizes the architectural design and technical specifications for building an autonomous, production-grade Data Analyst Agent. 

* **Target Database:** Local 3-table SQLite database, designed according to production standards.
* **Target Users:** Non-technical business stakeholders requiring high-accuracy answers and reliable visual reporting without needing to spot-check SQL syntax.
* **Action Space:**
  1. Translate natural language questions into valid SQLite queries (`SELECT`).
  2. Ingest and load flat files/data into the database (write operations).
  3. Execute Python scripts (`matplotlib` / `plotly`) to generate visual charts.
  4. Conduct system utility and health checks.

---

## 2. Architectural Decisions & Framework Selection

### Core Pattern: Bounded Agentic Workflow (Hierarchical Supervisor Pattern)
To ensure high accuracy and prevent infinite reasoning loops, the system uses a **LangGraph State Machine** featuring a top-level **Supervisor/Router** directing isolated sub-graphs.

```
                             +-------------------+
                             |   User Question   |
                             +---------+---------+
                                       |
                                       v
                       +---------------+---------------+
                       |  Supervisor / Router Node     |
                       | (Intent & Workflow Dispatcher)|
                       +-------+-------+-------+-------+
                               |       |       |
         +---------------------+       |       +---------------------+
         | (Data Load)                 | (Quick Checks)              | (Data Analysis)
         v                             v                             v
+-----------------------+     +-----------------+     +-------------------------------+
|  Loader Agent / Graph |     | System Utility  |     |   Analysis Sub-Graph          |
|                       |     | Tools Node      |     |                               |
| 1. Validate File      |     | - DB Status     |     |  +-------------------------+  |
| 2. Parse & Sanitize   |     | - Schema Info   |     |  | Planner -> SQL Coder    |  |
| 3. Execute SQLite Load|     | - Health Check  |     |  | -> SQL Critic           |  |
| 4. Update Schema Context|   +--------+--------+     |  | -> Python Visualizer    |  |
+-----------+-----------+              |              |  +------------+------------+  |
            |                          |                              |
            +-------------------+      |      +-----------------------+
                                |      |      |
                                v      v      v
                             +------------------+
                             |   State Reducer  |
                             | & Final Synthesizer|
                             +--------+---------+
                                      |
                                      v
                             +------------------+
                             |   Final Response |
                             +------------------+
```

### Key Framework Comparisons
* **LangGraph vs. Pure LangChain:** LangGraph is selected because it natively supports loops, cyclic self-correction, and state machines, whereas standard LangChain chains are strictly linear.
* **LangGraph vs. High-Level Agent Packages (e.g., Deep Agents):** Building a explicit LangGraph provides fine-grained control over execution boundaries, node-level error critics, and security sandboxing, avoiding the unpredictable nature of unconstrained autonomous agents.

---

## 3. Knowledge Base & Semantic Layer Strategy
Because the database is small (3 core tables), dynamic Vector DB (RAG) schema retrieval is deliberately omitted to prevent retrieval noise.

* **Implementation:** A static **Data Dictionary** (formatted as YAML or JSON) is directly injected into the system prompt context of the SQL Coder node.
* **Contents:**
  * Table names and descriptions.
  * Column names, data types, and representative sample values (e.g., date formats).
  * Explicit business logic rules (e.g., "Always exclude `status = 'CANCELLED'` when calculating revenue").

---

## 4. Gold-Standard Agent State Schema

To protect LLM context windows and prevent state race conditions, state variables are strictly categorized across 3 functional tiers:

```python
from typing import TypedDict, Annotated, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
import operator

class ExecutionError(TypedDict):
    node: str
    error_message: str
    attempt: int

class DataAnalystState(TypedDict):
    # 1. CONVERSATION TIER (Append-only via add_messages reducer)
    messages: Annotated[List[BaseMessage], add_messages]
    
    # 2. OPERATIONAL TIER (Overwritten per execution pass)
    db_schema: Dict[str, Any]            # Active table schemas & business rules
    intent_plan: Optional[Dict[str, Any]] # High-level workflow plan
    current_sql: Optional[str]           # SQL query under validation
    sql_results: Optional[List[Dict]]    # Raw data output from database
    visualization_code: Optional[str]    # Python script generated for chart
    chart_file_path: Optional[str]       # Path to rendered image file
    
    # 3. CONTROL & SAFETY TIER (Execution Metadata & Circuit Breakers)
    next_node: str                       # Target destination for conditional router
    retry_count: int                     # Active attempt counter
    max_retries: int                     # Hard ceiling for error loops
    error_history: Annotated[List[ExecutionError], operator.add]
```

---

## 5. Security & Observability Separation

1. **Database Safety:** SQLite connection strings for query evaluation must be enforced as **Read-Only**. Write operations are strictly delegated to the Data Ingestion node.
2. **Code Sandboxing:** Python visualization rendering runs inside an isolated, execution-capped subprocess or ephemeral Docker sandbox.
3. **Observability vs. State:** Agent State manages internal runtime data flow, while external trackers (LangSmith / OpenTelemetry) capture latency, token usage, and trace trees **out-of-band** via background event callbacks.

---

## 6. Next Steps & Suggested Skills for Subsequent Sessions

### Next Implementation Steps
1. Define the YAML Data Dictionary schema for the 3 target tables.
2. Construct the SQL Coder and SQL Critic nodes in LangGraph with automatic SQLite syntax retry loops.
3. Implement the sandboxed Python execution environment for chart rendering.

### Suggested Skills for the Next Agent
* **LangGraph Construction Skill:** To generate typed node functions, state updates, and conditional edges adhering to `LangGraph` standards.
* **YAML Schema Validator Skill:** To parse and format database metadata, sample rows, and business constraints into optimal system prompts.
* **Python Code Sandbox Skill:** To safely test and execute LLM-generated visualization scripts inside isolated environments.