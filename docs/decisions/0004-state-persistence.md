# ADR 0004 — SQLite as the source of truth; no automatic resume

**Status:** accepted (2026-10)

## Decision
- The relational DB (SQLite by default, WAL mode, 30 s busy timeout) is the only authoritative state. Redis carries the Celery queue and best-effort pub/sub for live UIs. Losing Redis loses queued messages and live updates, never recorded state.
- State is committed as it changes: node transitions, logs with a per-run `seq_id`, file edits, and the final `result_json`.
- Duplicate deliveries are handled with a conditional `UPDATE … WHERE status='CREATED'` claim.
- After a worker crash, a run is **not** resumed. Tasks stuck in `RUNNING` beyond the hard time limit are marked `FAILED` at worker start; the user retries.
- Schema changes are additive and applied at startup (`backend/db/migrations.py`). Alembic is listed but unused, and was not introduced.

## Why not resume
Resuming requires idempotent steps. Steps call a non-deterministic model and write files, so replaying a half-finished step can produce a different workspace. Explicit retry is honest about that.

## Consequences
− SQLite limits write concurrency to one writer at a time. Fine for one machine; use PostgreSQL (`DATABASE_URL`) for several workers. That path is untested.
