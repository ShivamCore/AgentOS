# Engineering audit: AgentOS (October 2026)

Scope: the Python system (`agent/`, `backend/`, `cli/`, `tests/`, `scripts/`, CI, Docker, docs). The Next.js UI was reviewed only for contract compatibility.

## 1. Environment and restrictions

| Item | State on the audit machine |
|---|---|
| OS / Python | macOS 26.2 arm64 (Apple M4, 16 GB), Python 3.12.13 in `.venv` |
| Version control | **Not a git repository.** No history to inspect, so earlier changes to claims could not be traced. A source snapshot was taken before any edit (outside the repo). |
| Docker | **Not installed.** Real-container sandbox tests could not run. |
| Ollama | 0.40.6 running, one model installed: `phi3.5:latest` (3.8B, Q4_0). The configured defaults (`deepseek-coder:1.3b`/`6.7b`) were not installed. |
| Redis | Running locally |
| Network | PyPI reachable but slow (first `pip-audit` attempt timed out) |

## 2. Architecture as found

- **Two execution engines.**
  - `agent/core.py` + `agent/orchestrator.py` + `agent/task_graph.py` (used by `main.py`) could not be imported: `agent.planner.plan_task` and `model_router.classify_task` do not exist.
  - `agent/planner/executor.py` (used by the Celery worker) imported cleanly but called a broken `agent/executor.py`.
- **Status strings in three dialects:** lowercase `pending/running/completed/failed` (legacy graph, UI), uppercase `CREATED/RUNNING/COMPLETED/FAILED` (DB), and lowercase literals in `/health/resources` that never matched anything.
- **API → Celery → worker → planner → DAGOrchestrator → executor → ToolRegistry → sandbox.** The pieces existed, but the chain was broken in the middle (see P0-1).
- In git, `frontend` was a submodule pointer with no `.gitmodules`, so clones got an empty folder; the real UI lived in a separate local checkout (`frontend copy/`). It is now vendored into `frontend/` as regular files. It expects lowercase statuses and was already out of sync with the backend before this work.

## 3. Baseline (before any change)

| Check | Command | Result |
|---|---|---|
| Tests | `pytest -q` | **154 passed, 0 failed.** The earlier report of "152 passed / 2 failed" no longer applied. |
| What the tests exercised | — | The executor path was fully mocked. Mocks returned dicts where the real function returns `str`, which hid that the real path could never succeed. |
| Lint | `ruff check agent backend cli` | 1110 findings; 120 at `F`/`E9` level, most of them `E902` caused by macOS AppleDouble `._*` files |
| SAST | `bandit -ll` | 3 medium: `requests` with `timeout=None` (warmup), hard-coded `/tmp/` workspace, MD5 |
| Dependencies | `pip-audit -r requirements.txt` | Timed out at baseline. Later: chromadb 0.5.23 ×3, pytest 8.4.2 ×1 |
| Secrets | `scripts/check_secrets.py` | No `.env` present; default Flower password warning |
| E2E | real executor on a hand-written step | `NameError: cmd_execution_report` |
| Legacy CLI | `python main.py …` | `ImportError: cannot import name 'plan_task'` |

## 4. Verified defects and resolution

### P0: correctness and security

| # | Defect (evidence) | Resolution |
|---|---|---|
| 1 | `agent/executor.py` undefined names. `_sanitize_parsed_output` used `fixed_files`/`result`, so **every** LLM response was classified "INVALID OUTPUT FORMAT". Command handling used `cmd_execution_report`, so any command crashed. No task could ever complete a step. | Rewritten around a validated `StepAction`; covered by unit and E2E tests |
| 2 | Silent host execution. `get_sandbox()` fell back to `subprocess` with only a timeout. The executor's "semantic validation" ran `pytest`/`python` on generated files directly on the host. The legacy orchestrator ran generated code on the host. | Fail-closed `get_sandbox()`; explicit, labelled `UnsafeHostExecutor` opt-in; no other subprocess path ([ADR 0001](decisions/0001-fail-closed-sandbox.md)) |
| 3 | Failure "rollback" ran `kill -9` on any process listening on 8000/3000/5000/8080, which includes the AgentOS API itself | Removed |
| 4 | `POST /system/start|stop` spawned and killed host processes without authentication. `/agents/save` and `/agents/{name}/content` took unvalidated names, so `.md` files could be written and read outside `agents/`. | 403 unless `AGENTOS_ENABLE_PROCESS_CONTROL=1`; agent names validated; optional `API_KEY` on mutating endpoints |
| 4b | `WS /ws/terminal` executed any received text with `create_subprocess_shell` on the host, with no authentication. `start.sh` bound the API to `0.0.0.0`, so anyone on the network could run commands. (Found while fixing CI on PR #1.) | Refused unless `AGENTOS_ENABLE_WEB_TERMINAL=1` + loopback client + API key when configured; localhost bind by default; `tests/security/test_web_terminal.py` |
| 5 | Unsupported performance claims: a deepseek-coder 6.7b row with no measurement behind it, a cold-vs-warm comparison, a model swap labelled as quantization, "zero quality regression" | Claims removed; the old report archived with a warning; new reproducible harness ([benchmarking.md](benchmarking.md)) |

### P1: reliability and contracts

| # | Defect | Resolution |
|---|---|---|
| 6 | Legacy engine and `main.py` could not be imported | Moved to git-ignored `.attic/` (`rm` was blocked by the session's safety policy; the move is reversible) |
| 7 | Planner output: dangling dependencies silently dropped, no tool/agent/size/field validation; after a planning failure, the whole description was executed as one step | `validate_plan` + `PlanValidationError`; `PlannerError` after 2 attempts ([ADR 0005](decisions/0005-untrusted-planner.md)) |
| 8 | Dependents of failed nodes stayed `pending`; the run ended through a "loop trap" message; `is_complete()` required all nodes `completed` | Scheduler with `BLOCKED` propagation and a derived run state ([ADR 0002](decisions/0002-scheduler-threads-and-state-machine.md)) |
| 9 | Celery `max_retries=5` on `ValueError` re-ran the whole pipeline and re-inserted node rows. There was no claim step, so a duplicate delivery executed twice. | Conditional-UPDATE claim; no Celery retries; retry endpoint resets nodes ([ADR 0003](decisions/0003-retry-semantics.md)) |
| 10 | No cancellation. No per-node timeout. `max_time` only checked between dispatches. | `POST /tasks/{id}/cancel`, DB-polled cancel event, node and run deadlines |
| 11 | `AgentSelector` dereferenced the nonexistent `agent.capabilities` for unknown roles. Manifests advertised a `git` tool (not in the sandbox image) and a `rename` action. | Explicit role map; manifests list only real tools; only `write` is accepted |
| 12 | `file_callback` was never wired, so `files_modified` was always empty. The workspace was archived **and deleted** right after the run while the CLI said "review generated files in your workspace". | Wired; workspace kept by default (`ARCHIVE_WORKSPACES`) |
| 13 | LLM client: silent 6.7b → 1.3b downgrade; warm-up with `timeout=None`; tok/s computed as chars/4; undocumented global lock serializing all generation; response cache on by default (a retry could get the same bad output back); JSON extractor preferred arrays over objects | Typed errors, bounded timeouts, Ollama counters, `OLLAMA_MAX_CONCURRENT`, cache off by default, object-first extraction |
| 14 | API tests published real Celery messages to the developer's local Redis (the router's reference was not patched) | Fixed in `tests/conftest.py` |
| 15 | Worker start marked **every** `RUNNING` task `FAILED`, which breaks multi-worker setups | Only tasks older than the hard time limit |
| 16 | File tools used `startswith` for path confinement (`/ws` vs `/ws-evil` bypass, symlink escape) | `realpath` + `commonpath`; symlink test |
| 17 | `/health/resources` counted lowercase statuses (always 0) | Uses `RunState` |

### P2: hygiene and documentation

| # | Item | Resolution |
|---|---|---|
| 18 | 11 `__pycache__` dirs outside `.venv`; `._*` AppleDouble files; `saas_backend.db` in the repo root | Caches removed; `.gitignore` covers `._*`, `.DS_Store`, `*.db*`, `.attic/` |
| 19 | CI: `pip install … \|\| true` hid install failures; a 90% coverage gate that was never met; security and E2E suites not run | Install failures fail the build; combined suite gate at 75% (measured) |
| 20 | README: "Sandboxes everything", "hard limits", "`ulimit` fallback" (no `ulimit` existed), `agentos demo --watch` (no such option), automatic memory recall (ChromaDB, which downloads an embedding model on first use) | README rewritten from verified behaviour |
| 21 | `SECURITY.md` with placeholder e-mail, PGP key and response SLAs | Replaced with a pointer to `docs/security.md` |
| 22 | Dependency advisories | chromadb made optional (server-mode advisories; embedded use only); pytest 9.0.3 (+ pytest-asyncio 1.x, pytest-cov 7, pytest-benchmark 5); frontend `next` 16.4.3 and `dompurify` override → `npm audit` clean |
| 23 | CI lint/type gates had never passed on `main` (ruff: 910 findings, `mypy --strict`: 121 errors), so every test job was skipped | Code formatted (ruff + black), findings fixed; E501 (formatter-owned) and PTH (style) ignored in config; SQLAlchemy models moved to typed `Mapped[...]`; `mypy --strict` clean; Trivy action pinned to an existing tag |

## 5. Real end-to-end run (2026-10-27)

Stack: `uvicorn` API + Celery worker (solo pool) + local Redis (db 15) + Ollama `phi3.5`, using a scratch DB and workspace. Submitted with `agentos run "<two modules>" --risk safe --steps 8 --silent`.

| Attempt | Outcome | Cause | Action |
|---|---|---|---|
| 1 | FAILED, `LLMUnavailableError` | Ollama returned 500 "prediction aborted, token repeat limit reached" (JSON mode, T=0); misclassified as unavailability and retried identically | Classified as `LLMResponseError`; planner retry samples at T=0.3 |
| 2 | FAILED, `PlannerError` | Second planner call ran 3.5 min, then was truncated (4000-token output budget) | Planner `max_output_tokens` 1024; coder/debugger 2048; parser tolerates trailing JSON |
| 3 | **PARTIAL_SUCCESS** in 23.9 s of scheduling | 4-node plan; nodes 1–3 COMPLETED and wrote correct `mathutils.py`/`strutils.py`; node 4's model output contained a command, rejected because `terminal` is not authorized under `risk=safe` (2 attempts) | Working as designed; `max_observed_concurrency=2` |

This is one anecdotal run, not a success-rate measurement.

## 6. Remaining gaps (not fixed)

1. Real-container enforcement (network, memory, PIDs, read-only FS) is implemented and configuration-tested, but **not demonstrated**, because Docker is not available here.
2. The UI (`frontend/`) still uses lowercase statuses and does not know `BLOCKED`/`TIMED_OUT`/`CANCELLED`/`PENDING`.
3. LLM-bound steps run one at a time by default (`OLLAMA_MAX_CONCURRENT=1`); scheduler parallelism helps only the non-LLM part.
4. No end-to-end success-rate benchmark over a task set; no output-quality evaluation.
5. Untested: PostgreSQL, multi-worker deployments, and Redis outages in the middle of a run.
6. 75% line coverage; `agent/utils/inference_cache.py`, `batch_infer.py` and the WebSocket router are largely untested.
7. `npm run lint` reports 2 warnings in UI components (not part of CI).
