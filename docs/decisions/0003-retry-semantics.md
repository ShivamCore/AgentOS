# ADR 0003 — Where retries happen

**Status:** accepted (2026-10)

## Decision
| Layer | Retries | Rationale |
|---|---|---|
| Ollama HTTP (`agent/llm.py`) | ≤ `retries` (default 2) for connection errors, timeouts and 5xx, with exponential backoff (1, 2, … ≤ 8 s) | Transient transport failures |
| Ollama 4xx / model missing / degenerate generation | none (an empty reply gets one retry) | Retrying an identical request cannot succeed; no silent switch to a different model |
| Planner (`plan_markdown_task`) | 2 attempts; the second includes the rejection reason and samples at T=0.3 | Validation failures are often fixable; a deterministic failure would repeat at T=0 |
| Step (scheduler) | `STEP_MAX_RETRIES` (default 1); the retry uses the debugger agent with the previous error | The model can correct its own output |
| Whole task (Celery) | none automatically; `POST /retry/{id}` | Avoids duplicate side effects and duplicate node rows; the user decides |

The removed behaviour: Celery `max_retries=5` re-ran the entire pipeline on `ValueError` (including planner JSON errors), re-inserting node rows each time; and `generate_text` silently downgraded 6.7b → 1.3b on failure.

## Consequences
Retries are bounded at every layer, and the worst-case time is predictable: run budget ≤ `TASK_TIMEOUT_SECONDS - 60`.
