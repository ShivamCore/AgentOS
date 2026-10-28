# AgentOS

A local-first agent runner. You describe a coding task. A local LLM (via [Ollama](https://ollama.com)) plans it as a dependency graph of small steps. AgentOS then rejects any plan that fails strict validation, runs independent steps concurrently, executes each step through an authorized tool layer and a fail-closed Docker sandbox, and persists every state transition, log line and file edit.

The engineering focus is **treating model output as untrusted input**: plans are validated rather than repaired, tools are re-authorized at execution time, and if no sandbox is available, nothing runs.

[Architecture](docs/architecture.md) · [Security model](docs/security.md) · [Testing](docs/testing.md) · [Benchmarks](docs/benchmarking.md) · [Design decisions](docs/decisions/) · [Engineering audit](docs/engineering-audit.md) · [API reference](docs/api-reference.md)

## What works today

| Capability | Status | Evidence |
|---|---|---|
| Submit a task via HTTP API or `agentos` CLI; poll status, logs and result; cancel; retry | Working | [`tests/e2e`](tests/e2e), real run in [`docs/engineering-audit.md`](docs/engineering-audit.md) §5 |
| LLM planner → strictly validated DAG (schema, ids, dependencies, cycles, tools, agents, size, depth) | Working | [`tests/unit/test_plan_validation.py`](tests/unit/test_plan_validation.py) |
| Scheduler: dependency ordering, bounded concurrency, retries, node/run timeouts, cancellation, `BLOCKED` propagation | Working | [`tests/unit/test_scheduler.py`](tests/unit/test_scheduler.py), [`benchmarks/results/dag_scheduler.json`](benchmarks/results/dag_scheduler.json) |
| Tool authorization (plan ∩ agent manifest ∩ risk level), strict tool schemas, workspace path confinement | Working | [`tests/unit/test_step_executor.py`](tests/unit/test_step_executor.py) |
| Fail-closed command execution (no Docker → no execution) | Working | `tests/security/test_sandbox.py::TestFailClosed` |
| Docker sandbox: no network, CPU/memory/PID/output limits, non-root, capabilities dropped, read-only root | Implemented and configuration-tested; **enforcement in a real container not yet demonstrated** (no Docker on the test machine) | `TestDockerIsolationSettings`; `pytest -m docker` |
| Duplicate-delivery safety; crash recovery (stale runs marked failed) | Working | E2E tests |
| Ollama client: typed errors, bounded retries, timeouts, streaming, JSON mode | Working; live-tested with `phi3.5` | [`tests/unit/test_llm.py`](tests/unit/test_llm.py), `pytest -m ollama` |
| Semantic memory (ChromaDB) | Optional, off by default | `AGENTOS_MEMORY_ENABLED=1` |
| Web UI (`frontend/`) | Not maintained; uses outdated status names | — |

## How it works

```mermaid
sequenceDiagram
    participant U as CLI / HTTP client
    participant A as FastAPI
    participant DB as SQLite
    participant Q as Redis (Celery broker)
    participant W as Worker
    participant L as Ollama
    participant S as Docker sandbox
    U->>A: POST /tasks/create
    A->>DB: insert task CREATED
    A->>Q: enqueue task_id (503 + FAILED if broker is down)
    Q->>W: deliver
    W->>DB: conditional UPDATE CREATED→RUNNING (claim exactly once)
    W->>L: planner prompt (JSON mode)
    W->>W: parse_planner_dag + validate_plan (reject on any violation)
    W->>DB: persist nodes PENDING
    loop DAGScheduler (≤ MAX_WORKERS steps in flight)
        W->>L: step prompt (coder; debugger on retry)
        W->>W: parse StepAction, authorize tools
        W->>DB: file edits, node states, sequence-numbered logs
        W->>S: allow-listed command, no shell, no network
    end
    W->>DB: final run state + result_json
    U->>A: GET /tasks/{id}, /tasks/{id}/result, /logs/{id}
```

| Component | Path | Responsibility |
|---|---|---|
| Lifecycle | [`agent/lifecycle.py`](agent/lifecycle.py) | The only definition of node/run states and legal transitions; illegal transitions raise |
| Plan model and validation | [`agent/planner/graph.py`](agent/planner/graph.py) | `TaskGraph`, `parse_planner_dag`, `validate_plan`, topological sort, depth limit |
| Scheduler | [`agent/planner/scheduler.py`](agent/planner/scheduler.py) | Thread-pool DAG execution, retries, timeouts, cancellation, failure propagation |
| Step executor | [`agent/executor.py`](agent/executor.py) | Parses untrusted step JSON into a `StepAction` and applies it through the tool registry |
| Tools | [`agent/tools/`](agent/tools/) | `file_read`, `file_write`, `terminal`; strict input schemas; authorization check |
| Sandbox | [`agent/sandbox.py`](agent/sandbox.py) | One Docker container per command; fail-closed; explicit opt-in unsafe host executor |
| Ollama client | [`agent/llm.py`](agent/llm.py) | Typed errors, bounded retries with backoff, streaming, metrics from Ollama counters |
| API and worker | [`backend/`](backend/) | FastAPI routes, Celery task (claim → plan → execute → persist), SQLAlchemy models |
| CLI | [`cli/`](cli/) | `agentos run/status/result/logs/retry/cancel/doctor/demo` over the HTTP API |

Node lifecycle, enforced by `NodeStateMachine.transition`:

```
PENDING ──► READY ──► RUNNING ──► COMPLETED | FAILED | TIMED_OUT | CANCELLED
   │          │          └──────► READY            (retry)
   └──────────┴──────────────────► BLOCKED | CANCELLED
```

When a node ends without completing, every transitive dependent becomes `BLOCKED` and independent branches keep running. A run with some but not all nodes completed ends as `PARTIAL_SUCCESS`.

## Design decisions

Each decision is recorded as an ADR with its context and consequences:

| ADR | Decision |
|---|---|
| [0001](docs/decisions/0001-fail-closed-sandbox.md) | Docker is the only isolating backend; without it, command steps fail. No Python-level "sandbox" (restricted builtins and AST filters are bypassable). |
| [0002](docs/decisions/0002-scheduler-threads-and-state-machine.md) | One thread-pool scheduler and one state machine replaced two inconsistent orchestrators. Steps are I/O-bound, so threads are sufficient. Step runners are injected so tests and benchmarks use the production scheduler without an LLM. |
| [0003](docs/decisions/0003-retry-semantics.md) | Retries happen per layer: HTTP transport errors, planner validation (with the rejection reason fed back), and steps (via the debugger agent). Whole tasks are never retried automatically, to avoid duplicate side effects. |
| [0004](docs/decisions/0004-state-persistence.md) | SQLite (WAL) is the source of truth; Redis only carries the queue and live updates. Crashed runs are marked failed rather than resumed. |
| [0005](docs/decisions/0005-untrusted-planner.md) | Plans are rejected, never repaired. There is no fabricated fallback plan when planning fails. |

## Setup

Requirements: Python ≥ 3.11, Redis, Ollama with at least one model. Docker is optional, but without it any step that runs a command fails closed.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e . --no-deps   # provides the `agentos` command
cp .env.example .env
ollama pull phi3.5                    # or any model; then set OLLAMA_MODEL in .env
export OLLAMA_MODEL=phi3.5
make check-inference                  # verifies Ollama is reachable and the model is installed
make sandbox-image                    # optional: builds agentos-sandbox:latest (needs Docker)
```

## Smallest end-to-end run

Three terminals (Redis already running):

```bash
celery -A backend.workers.celery_app worker --loglevel=info --pool=solo
```

```bash
uvicorn backend.api.main:app --port 8000
```

```bash
agentos doctor        # Ollama/model, sandbox and API status
agentos run "Create mathutils.py with add(a, b) and strutils.py with shout(s) returning s.upper() + '!'" --risk safe --steps 8
agentos status <task_id>
agentos result <task_id>
```

`--risk safe` authorizes only file tools, so this works without Docker. Generated files land in `workspace/<task_id>/`. With `--risk balanced`, steps may also run allow-listed commands, but only inside the Docker sandbox.

Without an LLM, the deterministic E2E suite exercises the same path (HTTP → worker → validation → scheduler → tools → DB):

```bash
pytest tests/e2e -v
```

## Verification

The core suite needs no LLM, Docker or Redis server: the LLM is a scripted function, Redis is `fakeredis`, and Celery tasks are invoked directly.

```bash
pytest -q                     # unit, integration, contract, regression, security, e2e, perf
pytest -m docker              # real-container enforcement (requires Docker + make sandbox-image)
AGENTOS_TEST_OLLAMA_MODEL=phi3.5 pytest -m ollama
```

| Check | Result | How it was established |
|---|---|---|
| `pytest -q` | 288 passed, 9 skipped | Run locally on 2026-10-28 (macOS arm64, Python 3.12). Skips: 5 need Docker, 3 need opt-in Ollama, 1 Linux-only rlimit. |
| Lint, type check, security scan, unit/integration/contract/regression/perf tests, coverage gate (≥ 75%) | Passing | GitHub Actions [`ci.yml`](.github/workflows/ci.yml) on `main` |
| Docker build and image scan job | Failing on `main` | The Trivy SARIF upload lacks the `security-events` permission; the fix is in an open PR |
| Coverage of `agent` + `backend` (line + branch) | 76.75% | Recorded in [`docs/testing.md`](docs/testing.md) |

What each layer covers is described in [`docs/testing.md`](docs/testing.md).

## Benchmarks

The scheduler and inference harnesses write raw JSON to [`benchmarks/results/`](benchmarks/results/). Measured on an Apple M4 (16 GB):

- **Scheduler, 16 independent 50 ms steps:** 0.86 s with 1 worker → 0.11 s with 8 (7.8× speedup), about 4 ms overhead per sequential wave. A 16-step chain gets 1.0×, as expected.
- **`phi3.5` (3.8B, Q4_0):** warm decode median 40.9 tok/s (n=9); cold first request 5.74 s, reported separately.

These are scheduler and single-model numbers, not end-to-end task speedups. LLM calls are serialized by default (`OLLAMA_MAX_CONCURRENT=1`). No quantization comparison or output-quality evaluation has been run, so no such claims are made. Methodology: [docs/benchmarking.md](docs/benchmarking.md).

## Security model

LLM output is untrusted. Plans are validated, and every side effect goes through the tool registry, which checks authorization and argument schemas. Commands run without a shell, only if allow-listed, and only in a disposable container with no network and capped resources. If Docker is unavailable, nothing runs. A development-only `AGENTOS_ALLOW_UNSAFE_HOST_EXECUTION=1` exists and is explicitly **not** a sandbox. The API is unauthenticated unless `API_KEY` is set, so keep it on localhost. Full threat model and limitations: [docs/security.md](docs/security.md).

## Known limitations

- Docker sandbox enforcement has not yet been demonstrated end to end on a real daemon.
- Small local models often produce invalid plans. AgentOS rejects them with `PlannerError` rather than executing them.
- LLM-bound steps of one task run one at a time unless Ollama is configured for parallel requests.
- Concurrent steps that write the same file: the last write wins.
- A run interrupted by a worker crash is marked failed, not resumed.
- Only SQLite and a single worker have been tested.
- The web UI is out of date with the API's state names.

## Project layout

```
agent/        lifecycle, planner (graph, scheduler, orchestrator), selector, executor, tools, sandbox, llm
agents/       Markdown agent manifests (planner, coder, debugger)
backend/      FastAPI app, Celery worker, SQLAlchemy models, additive migration
cli/          `agentos` CLI
benchmarks/   reproducible benchmark harnesses + raw results
docker/       sandbox image
docs/         architecture, security, testing, benchmarking, ADRs, audit
tests/        unit, integration, contract, regression, security, e2e, perf
```

## License

[MIT](LICENSE)
