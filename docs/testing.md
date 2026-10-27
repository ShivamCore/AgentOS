# Testing

The core suite needs no LLM, no Docker, no Redis server and no paid API. External services are replaced by:

- **LLM:** a scripted function patched into `agent.selector.generate_text`, or mocked HTTP responses for `agent/llm.py`;
- **Redis:** `fakeredis`;
- **Celery:** `apply_async` captured, and the task function called directly the way a worker would call it;
- **Docker:** a fake SDK client for configuration tests, or `DockerSandbox` made unavailable to exercise the fail-closed path.

## Commands

```bash
pip install -r requirements.txt
pytest -q                                   # everything that can run here
pytest tests/e2e -v                         # deterministic end-to-end
pytest tests/security -v -rs                # sandbox + injection; shows why Docker tests skipped
pytest --cov=agent --cov=backend -q         # coverage (CI floor: 75%)
ruff check . && ruff format --check . && black --check backend/ agent/
mypy backend/ agent/ --strict --ignore-missing-imports
make sandbox-image && pytest -m docker -v   # real-container enforcement (needs Docker)
AGENTOS_TEST_OLLAMA_MODEL=phi3.5 pytest -m ollama -v -rxs   # live inference (needs Ollama + model)
```

## Results (2026-10-27, macOS arm64, Python 3.12.13, no Docker, Ollama 0.x with `phi3.5` installed)

| Suite | Result |
|---|---|
| `tests/unit` | 184 passed (1 skipped without chromadb) |
| `tests/integration` | 12 passed, 3 skipped (live Ollama, opt-in) |
| `tests/contract` | 16 passed |
| `tests/regression` | 11 passed |
| `tests/security` | 42 passed, 6 skipped (5 need Docker, 1 Linux-only rlimit) |
| `tests/e2e` | 15 passed |
| `tests/perf` | 7 passed |
| **Total** `pytest -q` | **288 passed, 9 skipped** (dev venv with chromadb) |
| Clean venv from `requirements.txt` (pytest 9.0.3, as in CI) | 287 passed, 10 skipped (chromadb test skips; chromadb is optional) |
| `AGENTOS_TEST_OLLAMA_MODEL=phi3.5 pytest -m ollama` | 3 passed |
| Coverage (`agent` + `backend`, line + branch) | 76.75% (CI gate 75%) |

Skipped and never run here: `TestRealDockerEnforcement` (network block, memory limit, PID limit, read-only root FS, infinite-loop kill in a real container). Docker is not installed on the audit machine. These are the only sandbox properties not demonstrated end to end.

## What each layer covers

**Unit**
- `test_plan_validation.py`: malformed and incomplete JSON, duplicate ids, dangling dependencies, self-dependency, cycles, unknown tools/agents/fields, size and depth limits, lifecycle transition rules.
- `test_scheduler.py`: dependency ordering (diamond, 40-node chain), real concurrency proven with a `threading.Barrier`, the `max_workers` bound, failure propagation (`BLOCKED`) while independent branches continue, retries and non-retryable failures, runner exceptions, malformed outcomes, node timeout, run cancellation, late results ignored, step budget.
- `test_step_executor.py`: step JSON parsing, path traversal (incl. symlinks), syntax pre-check, tool authorization, `file_scope`, size cap, rollback on command failure, registry argument validation.
- `test_llm.py`: model-not-found (no retry, no substitution), 5xx retry with backoff, connection errors, timeout-then-success, degenerate generations, empty and incomplete streams, metrics from Ollama counters, parameter validation, health check.
- `test_executor.py`: orchestrator callback contracts, `max_steps`/`max_time`, file callback.

**Integration / contract / regression:** API routes against an in-memory DB, response schemas, and the historical status-string and backpressure bugs.

**E2E** (`tests/e2e/test_pipeline_deterministic.py`): HTTP create → worker → planner validation → scheduler → executor → tools → DB → HTTP read-back, against a file-backed SQLite DB. Covers parallel branches (barrier-proven), fail-closed command steps, the explicit unsafe opt-in, `risk_level=safe`, invalid plans, LLM unavailable, duplicate delivery, cancel before start and cancel while running, retry, broker down (503), API key, unknown request fields.

**Security:** sandbox fail-closed, command policy, container kwargs, cleanup on timeout and start failure, output caps, OOM reporting, the unsafe executor's process-group kill and env scrubbing, plus SQL injection, XSS and CORS tests.

## Fixed during the overhaul

- The API tests did not patch the router's own `run_agent_task` reference, so `POST /tasks/create` in tests published real Celery messages to whatever `REDIS_URL` pointed at. `tests/conftest.py` now patches both references.
- `test_llm.py::test_import_makes_no_http_calls` deleted `agent.llm` from `sys.modules` and never restored it. Later tests then saw two copies of the module's exception classes (order-dependent failure). It now restores the original module.
- `test_executor.py` and two regression tests patched internals of the removed `AgentPool`. They were rewritten against the injectable `step_runner` seam, with the same contracts and stronger assertions (exact state sequences).
