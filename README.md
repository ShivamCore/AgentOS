# AgentOS

A local-first agent runner. You describe a coding task; a local LLM (via [Ollama](https://ollama.com)) plans it as a dependency graph of small steps; AgentOS validates the plan, schedules independent steps concurrently, executes each step through an authorized tool layer and a fail-closed Docker sandbox, and records every state transition, log line and file edit.

[Architecture](docs/architecture.md) · [Security model](docs/security.md) · [Testing](docs/testing.md) · [Benchmarks](docs/benchmarking.md) · [Design decisions](docs/decisions/) · [Audit](docs/engineering-audit.md)

## What works today

| Capability | Status | Evidence |
|---|---|---|
| Submit a task via HTTP API or `agentos` CLI; poll status, logs and result; cancel; retry | Working | `tests/e2e`, real run in `docs/engineering-audit.md` §5 |
| LLM planner → strictly validated DAG (schema, ids, dependencies, cycles, tools, agents, size, depth) | Working | `tests/unit/test_plan_validation.py` |
| Scheduler: dependency ordering, bounded concurrency, retries, node/run timeouts, cancellation, `BLOCKED` propagation | Working | `tests/unit/test_scheduler.py`, `benchmarks/results/dag_scheduler.json` |
| Tool authorization (plan ∩ agent manifest ∩ risk level), strict tool schemas, workspace path confinement | Working | `tests/unit/test_step_executor.py` |
| Fail-closed command execution (no Docker → no execution) | Working | `tests/security/test_sandbox.py::TestFailClosed` |
| Docker sandbox: no network, CPU/memory/PID/output limits, non-root, caps dropped, read-only root | Implemented and configuration-tested; **real-container enforcement not yet demonstrated** (no Docker on the audit machine) | `TestDockerIsolationSettings`; `pytest -m docker` |
| Duplicate-delivery safety, crash recovery (mark stale runs failed) | Working | E2E tests |
| Ollama client: typed errors, bounded retries, timeouts, streaming, JSON mode | Working; live-tested with `phi3.5` | `tests/unit/test_llm.py`, `pytest -m ollama` |
| Semantic memory (ChromaDB) | Optional, off by default | `AGENTOS_MEMORY_ENABLED=1` |
| Web UI (`frontend/`) | Not maintained; uses outdated status names | — |

## How it works

```
agentos run "…" ──► FastAPI ──► SQLite (task CREATED) ──► Celery/Redis ──► worker
                                                                     │ claim CREATED→RUNNING (exactly once)
                                                                     ▼
                                     Ollama planner ──► parse + validate_plan (reject, never repair)
                                                                     ▼
                       DAGScheduler: PENDING→READY→RUNNING→COMPLETED | FAILED | TIMED_OUT | CANCELLED | BLOCKED
                                ├── step A ─┐   (independent steps in parallel, ≤ MAX_WORKERS)
                                ├── step B ─┤
                                └── step C ◄┘   (waits for A and B)
                                     each step: LLM → StepAction JSON → authorized tools
                                         file_write → task workspace only
                                         terminal   → Docker sandbox (fail-closed)
```

Details: [docs/architecture.md](docs/architecture.md).

## Setup

Requirements: Python ≥ 3.11, Redis, Ollama with at least one model. Docker is optional, but without it steps that run commands fail closed.

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

`--risk safe` authorizes only file tools, so this works without Docker. Generated files are in `workspace/<task_id>/`. With `--risk balanced`, steps may also run allow-listed commands, but only inside the Docker sandbox.

Without an LLM, the deterministic E2E suite exercises the same path (HTTP → worker → validation → scheduler → tools → DB):

```bash
pytest tests/e2e -v
```

## Tests

```bash
pytest -q                     # 276 passed, 9 skipped on the audit machine (skips: Docker-only, opt-in Ollama, Linux-only)
pytest -m docker              # real-container enforcement (requires Docker + make sandbox-image)
AGENTOS_TEST_OLLAMA_MODEL=phi3.5 pytest -m ollama
```

See [docs/testing.md](docs/testing.md) for what each layer covers.

## Benchmarks

The scheduler and inference harnesses record raw JSON in `benchmarks/results/`. Measured on an Apple M4 (16 GB):

- **Scheduler, 16 independent 50 ms steps:** 0.86 s with 1 worker → 0.11 s with 8 (7.8× speedup), about 4 ms overhead per sequential wave. A 16-step chain gets 1.0×, as expected.
- **`phi3.5` (3.8B, Q4_0):** warm decode median 40.9 tok/s (n=9); cold first request 5.74 s, reported separately.

These are scheduler and single-model numbers, not end-to-end task speedups. LLM calls are serialized by default (`OLLAMA_MAX_CONCURRENT=1`). No quantization comparison and no output-quality evaluation have been run, so no such claims are made. Methodology: [docs/benchmarking.md](docs/benchmarking.md).

## Security in one paragraph

LLM output is untrusted. Plans are validated, and every side effect goes through the tool registry, which checks authorization and argument schemas. Commands run without a shell, only if allow-listed, and only in a throw-away container with no network and capped resources. If Docker is unavailable, nothing runs. A development-only `AGENTOS_ALLOW_UNSAFE_HOST_EXECUTION=1` exists and is explicitly **not** a sandbox. The API is unauthenticated unless `API_KEY` is set, so keep it on localhost. Full threat model and limitations: [docs/security.md](docs/security.md).

## Known limitations

- Docker sandbox enforcement has not yet been demonstrated end to end on a real daemon (see above).
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

MIT License.
