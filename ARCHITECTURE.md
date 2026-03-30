# Architecture

## System Overview

AgentOS is a local-first autonomous coding agent. It accepts structured tasks, decomposes them into a DAG of atomic steps, and executes each step inside a sandboxed environment using locally-hosted LLMs.

```
┌─────────────────────────────────────────────────────────────────────┐
│                          NextJS Frontend                            │
│     TaskBuilder UI ──── ReactFlow DAG ──── Real-time Logs (WS)     │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │  REST + WebSocket
┌─────────────────────────────────▼───────────────────────────────────┐
│                          FastAPI Backend                             │
│                                                                     │
│  ┌──────────┐  ┌──────────────┐  ┌───────────┐  ┌───────────────┐ │
│  │ Task     │  │ Rate Limiter │  │ Constraint│  │ Backpressure  │ │
│  │ Router   │→ │ (per-IP RPM) │→ │ Validator │→ │ Guard         │ │
│  └──────────┘  └──────────────┘  └───────────┘  └───────┬───────┘ │
│                                                          │         │
│                                           Celery task.apply_async  │
└──────────────────────────────────────────────────┬──────────────────┘
                                                   │
┌──────────────────────────────────────────────────▼──────────────────┐
│                         Celery Worker                               │
│                                                                     │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │ Planner    │   │ Agent        │   │ DAGOrchestrator          │  │
│  │ Agent      │──→│ Selector     │──→│ (ThreadPoolExecutor)     │  │
│  │ (LLM call) │   │ (confidence) │   │                          │  │
│  └────────────┘   └──────────────┘   │  ┌─────┐ ┌─────┐ ┌────┐ │  │
│                                      │  │Node │ │Node │ │Node│ │  │
│                                      │  │ 1   │ │ 2   │ │ 3  │ │  │
│                                      │  └──┬──┘ └──┬──┘ └──┬─┘ │  │
│                                      └─────┼──────┼──────┼──┘  │
│                                            │      │      │      │
│                              ┌─────────────▼──────▼──────▼──┐   │
│                              │    Execution Sandbox         │   │
│                              │    (Docker │ Subprocess)     │   │
│                              │    CPU/mem/time limits        │   │
│                              └──────────────────────────────┘   │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
               ┌───────────────────┼───────────────────┐
               ▼                   ▼                   ▼
        ┌────────────┐     ┌────────────┐      ┌────────────┐
        │  SQLite    │     │   Redis    │      │  ChromaDB  │
        │  (tasks,   │     │  (broker,  │      │  (semantic │
        │   logs,    │     │   pub/sub, │      │   memory,  │
        │   files)   │     │   cache)   │      │   recall)  │
        └────────────┘     └────────────┘      └────────────┘
```

---

## Component Inventory

| Component | Path | Responsibility |
|---|---|---|
| **API Router** | `backend/api/routers/task.py` | Task CRUD, backpressure, rate limiting |
| **Config** | `backend/config.py` | `pydantic-settings` with `.env` loading |
| **SQL Models** | `backend/models/sql_models.py` | TaskRecord, LogRecord, enums, indexes |
| **Celery Worker** | `backend/workers/tasks.py` | Task execution, retry, DB state machine |
| **Agent Loader** | `agent/loader.py` | Markdown → Pydantic `AgentManifest` |
| **Agent Selector** | `agent/selector.py` | Confidence scoring, fallback routing |
| **Planner** | `agent/planner/planner.py` | LLM-driven task → DAG decomposition |
| **DAG Executor** | `agent/planner/executor.py` | ThreadPool graph execution with callbacks |
| **Task Graph** | `agent/planner/graph.py` | DAG data model (`StepNode`, `TaskGraph`) |
| **Memory Engine** | `agent/memory/engine.py` | ChromaDB semantic recall, `@lru_cache` factory |
| **LLM Client** | `agent/llm.py` | Ollama streaming, JSON extraction, warmup |
| **Sandbox** | `agent/sandbox/` | Docker/subprocess execution isolation |
| **Tool Registry** | `agent/utils/tools.py` | Schema-validated tool abstraction |

---

## Design Decisions

### 1. Local-first, no cloud APIs
All LLM inference runs via Ollama on the local machine. No OpenAI, no Anthropic. This means zero API costs, full data privacy, and offline operation. Trade-off: model quality is limited to open-source models.

### 2. SQLite for task storage, not PostgreSQL
SQLite is the default because it requires zero infrastructure setup. A single file-based DB is sufficient for a local coding agent that processes tasks sequentially. PostgreSQL is supported via `DATABASE_URL` for scaled deployments.

### 3. @lru_cache for memory engine, not process-global singleton
The previous global singleton caused state pollution between Celery worker threads. `@lru_cache(maxsize=32)` keyed on `workspace_dir` ensures one engine per workspace, thread-safe by Python's GIL, and no shared mutable state.

### 4. Uppercase string enums for task status
`TaskStatus(str, Enum)` ensures that `TaskStatus.CREATED == "CREATED"` — the string comparison is always correct. This eliminated the backpressure bug where lowercase `"pending"` never matched the DB's uppercase `"CREATED"`.

### 5. Two-arg node_callback, three-arg log_callback
The executor uses separate callback signatures: `node_callback(node_id, status)` for state machine transitions and `log_callback(node_id, log_type, content)` for structured logging. This separation prevents the previous signature mismatch bug.

### 6. Celery over asyncio for task execution
Celery provides true process-level isolation, automatic retries with configurable backoff, dead letter queues, and monitoring via Flower. asyncio would require custom implementation of all these features.

### 7. Docker sandbox with subprocess fallback
Docker provides filesystem, network, and resource isolation. If Docker is unavailable (CI, lightweight dev machines), the system falls back to restricted subprocess execution with `ulimit` enforcement.

---

## Data Flow

```
User Input (TaskInput)
        │
        ▼
  _build_planner_prompt()     ← deterministic transform
        │
        ▼
  Planner Agent (LLM)        ← generates JSON DAG
        │
        ▼
  TaskGraph (validated)       ← Pydantic StepNode models
        │
        ▼
  DAGOrchestrator.run_graph() ← ThreadPoolExecutor
        │
   ┌────┼────┐
   ▼    ▼    ▼
 Node  Node  Node             ← each runs in sandbox
   │    │    │
   ▼    ▼    ▼
 node_callback(id, status)    ← DB update + WebSocket broadcast
        │
        ▼
  TaskRecord.status = COMPLETED
```

---

## Security Model

| Layer | Protection |
|---|---|
| **API** | Rate limiting (per-IP RPM), CORS allowlist, Pydantic input validation |
| **Agent Loader** | CSP-style content policy, URL/shell injection rejection |
| **Sandbox** | Docker container isolation, CPU/memory/time limits |
| **Database** | ORM parameterized queries (no raw SQL), no secret exposure in errors |
| **CI/CD** | Bandit SAST, pip-audit dependency scan, Trivy container scan, detect-secrets pre-commit |
| **Docker** | Non-root user (UID 1000), no shell, multi-stage build (no build tools in runtime) |
