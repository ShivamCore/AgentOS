# Architecture

This describes the code as it exists after the October 2026 overhaul. File paths are authoritative; if this document and the code disagree, the code wins and this file is out of date.

## Components

| Component | Path | Responsibility |
|---|---|---|
| Lifecycle model | `agent/lifecycle.py` | The only definition of node and run states and their legal transitions |
| Plan model + validation | `agent/planner/graph.py` | `StepNode`/`TaskGraph`, `parse_planner_dag`, `validate_plan`, `PlanPolicy` |
| Scheduler | `agent/planner/scheduler.py` | Dependency ordering, bounded concurrency, retries, timeouts, cancellation, failure propagation |
| Orchestrator | `agent/planner/executor.py` | Wires the scheduler to the LLM step runner and to persistence callbacks; maps `risk_level` to tools |
| Agent selection / prompting | `agent/selector.py` | Role → Markdown manifest, planner call with bounded retries, step call |
| Step executor | `agent/executor.py` | Parses untrusted step JSON into a `StepAction`, applies it via the tool registry, rolls back on failure |
| Tool registry | `agent/tools/` | `file_read`, `file_write`, `terminal`; strict input schemas; authorization check |
| Sandbox | `agent/sandbox.py` | Docker container per command; fail-closed; explicit opt-in unsafe host executor |
| Ollama client | `agent/llm.py` | Typed errors, bounded retries, timeouts, streaming, metrics from Ollama counters |
| API | `backend/api/` | Task CRUD, cancel, retry, logs, result, health, optional API key |
| Worker | `backend/workers/tasks.py` | Claim → plan → execute → persist; cancellation watcher; crash recovery |
| Persistence | `backend/models/sql_models.py`, `backend/db/` | SQLite (default) via SQLAlchemy; additive startup migration |
| CLI | `cli/` | `agentos run/status/result/logs/retry/cancel/doctor/demo` over the HTTP API |

Removed in the overhaul (moved to the git-ignored `.attic/` for reference): `agent/core.py`, `agent/orchestrator.py`, `agent/task_graph.py`, `agent/agent_pool.py`, `agent/utils/tools.py`, `agent/utils/validator.py`, `agent/utils/state_tracker.py`, `main.py`. They were a second, broken execution path (import errors at load time) with their own status strings and direct host subprocess execution.

## End-to-end flow

```mermaid
sequenceDiagram
    participant U as CLI / HTTP client
    participant A as FastAPI
    participant DB as SQLite
    participant Q as Redis (Celery broker)
    participant W as Worker
    participant L as Ollama
    participant S as Docker sandbox
    U->>A: POST /tasks/create (validated TaskInput)
    A->>DB: insert task CREATED
    A->>Q: apply_async(task_id)  (503 + FAILED if broker down)
    Q->>W: deliver
    W->>DB: conditional UPDATE CREATED→RUNNING (claim)
    W->>L: planner prompt (JSON mode)
    W->>W: parse_planner_dag + validate_plan (reject on any violation)
    W->>DB: persist nodes PENDING
    loop DAGScheduler
        W->>L: step prompt (coder; debugger on retry)
        W->>W: extract_json_payload → StepAction
        W->>W: authorize tools (plan ∩ manifest ∩ risk level ∩ registry)
        W->>DB: file edits, node states, logs (seq-numbered)
        W->>S: allow-listed command, no shell, no network
    end
    W->>DB: final run state + result_json
    U->>A: GET /tasks/{id}, /tasks/{id}/result, /logs/{id}
```

## Lifecycle

Run states (`RunState`): `CREATED → RUNNING → {COMPLETED | PARTIAL_SUCCESS | FAILED | CANCELLED | TIMED_OUT}`; terminal non-success runs may be re-queued to `CREATED` by `POST /retry/{id}`. `PLANNED` exists for compatibility and is not used by the worker.

Node states (`NodeState`):

```
PENDING ──► READY ──► RUNNING ──► COMPLETED | FAILED | TIMED_OUT | CANCELLED
   │          │          └──────► READY            (retry)
   └──────────┴──────────────────► BLOCKED | CANCELLED
```

`NodeStateMachine.transition` raises `InvalidTransition` for anything else, so a terminal node can never change again and a node can never run without passing through `READY`.

Run outcome: all nodes `COMPLETED` → `COMPLETED`; at least one `COMPLETED` → `PARTIAL_SUCCESS`; none → `FAILED`. Run-level cancellation or deadline overrides this with `CANCELLED` / `TIMED_OUT`.

## Planning and validation

The planner is treated as untrusted input. `parse_planner_dag` accepts a plan only if:

- it is a JSON object with a `steps` list (the first JSON value is used if the model appends text);
- each step has only the planner fields (`step_id`, `description`, `required_tools`, `preferred_agent`, `dependencies`); anything else, such as a `timeout` or `status`, is rejected;
- ids are unique and match `[A-Za-z0-9_.-]{1,64}` (integer ids are normalised to strings);
- every dependency exists (dangling dependencies are an error, never silently dropped), with no self-dependency and no cycles;
- node count ≤ `max_nodes` (the task's `max_steps`), chain depth ≤ `max_depth`;
- tools ⊆ the tools allowed for the task's `risk_level`, and agents ⊆ {`coder`, `debugger`}.

`plan_markdown_task` makes at most 2 attempts. The second attempt includes the rejection reason and samples at temperature 0.3, because a deterministic failure would repeat at temperature 0. It then raises `PlannerError`, and the run is marked `FAILED` with that message. There is no fabricated fallback plan.

## Scheduling

`DAGScheduler.run` is a single coordinating loop with a `ThreadPoolExecutor(max_workers)`:

1. Stop conditions: cancel event or run deadline → unstarted nodes `CANCELLED`, in-flight nodes signalled and marked `CANCELLED`/`TIMED_OUT`.
2. Promotion: `PENDING` → `READY` when all dependencies are `COMPLETED`; if any dependency ended in another terminal state, every transitive dependent becomes `BLOCKED`.
3. Dispatch: `READY` nodes are submitted while fewer than `max_workers` are in flight and the `max_steps` budget allows (retries do not consume budget). A node becomes `RUNNING` only when a worker thread actually starts it, so `max_observed_concurrency` measures real overlap.
4. Deadlock guard: if nothing is in flight or ready but non-terminal nodes remain, they are marked `BLOCKED` with an explicit error. This should be unreachable for a validated DAG.
5. Outcomes: success → `COMPLETED`; failure → `READY` (retry) while `attempts ≤ max_retries` and the outcome is retryable, else `FAILED` + propagate. A runner exception or a non-`StepOutcome` return value becomes a failed outcome; it never crashes the loop.

Timeouts: Python threads cannot be killed. A node past `node_timeout_sec` is marked `TIMED_OUT`, its `cancel_event` is set, and the thread is abandoned. Its eventual result is ignored. Everything the thread can still do is bounded by the LLM client timeouts and the sandbox deadline.

## Step execution and tools

`LLMStepRunner` (one attempt):

1. The agent is `coder` on attempt 1 and `debugger` on retries, and the retry receives the previous error.
2. Authorized tools are `required_tools` (or the manifest's tools if none were given) ∩ manifest tools ∩ `RISK_TOOL_POLICY[risk_level]` ∩ registry. Under `safe`, `terminal` is never authorized.
3. The model's JSON is parsed into `StepAction`: at most 10 file writes and 3 commands. Only the `write` action exists. Delete and rename are rejected.
4. `execute_step` checks authorization, path confinement (realpath + `commonpath`, which catches `..`, absolute paths and symlink escapes), `file_scope`, and Python syntax before writing anything. It writes through the `file_write` tool and runs commands through the `terminal` tool. If a write or command fails, the attempt's files are restored.

Concurrent steps write to the same task workspace. Two independent steps that write the same path both succeed and the later write wins. The planner prompt asks for independent steps to touch different files, but this is not enforced.

## Worker, persistence and state

- **Claim / duplicate delivery:** `UPDATE tasks SET status='RUNNING' WHERE id=? AND status='CREATED' AND cancel_requested=0`. Exactly one delivery wins. Later deliveries are no-ops.
- **No Celery retries:** retries live in the LLM client (transport) and the scheduler (steps). Re-running a whole task is explicit (`POST /retry`). That endpoint deletes old node rows, so a re-run never duplicates nodes.
- **Cancellation:** `POST /tasks/{id}/cancel` sets `cancel_requested`. A not-yet-started task becomes `CANCELLED` immediately. For a running task, a watcher thread polls the flag every second and sets the scheduler's cancel event.
- **Deadlines:** the run budget is `min(max_time, TASK_TIMEOUT_SECONDS - 60)`, so the worker records a terminal state before Celery's soft limit. If the soft limit still fires, the run is recorded as `TIMED_OUT`.
- **Crash recovery:** tasks still `RUNNING` beyond the hard time limit are marked `FAILED` when a worker starts. Runs are not resumed.
- **Durability:** task, node, log and file-edit rows are committed as they happen (SQLite, WAL mode). An in-flight run is lost if the worker dies; its rows remain for inspection.
- **Correlation:** every log row has `task_id`, `node_id` and a per-run monotonic `seq_id`. The worker also emits `task=… node=… seq=…` log lines.
- **Workspaces:** `WORKSPACE_DIR/<task_id>/` is kept for inspection by default (`ARCHIVE_WORKSPACES=true` tars and deletes it).

## Concurrency model and its limits

- Scheduler concurrency is real (measured in `benchmarks/results/dag_scheduler.json`).
- LLM calls are bounded by `OLLAMA_MAX_CONCURRENT`, which defaults to 1. With one local Ollama instance, LLM-bound steps therefore run one at a time even when the DAG allows more. Raising the limit only helps if Ollama is configured with `OLLAMA_NUM_PARALLEL > 1` and has the memory for it.
- One Celery task = one run. Celery worker concurrency controls how many runs execute at once; `MAX_CONCURRENT_TASKS` applies API-side backpressure.
