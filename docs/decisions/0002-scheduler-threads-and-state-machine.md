# ADR 0002 — In-process thread-pool DAG scheduler with an explicit state machine

**Status:** accepted (2026-10)

## Context
Two orchestrators existed (`agent/orchestrator.py`, `agent/planner/executor.py`), with different lowercase/uppercase status strings. Neither prevented illegal transitions, propagated failures to dependents, or enforced a per-node timeout. Nodes whose dependencies failed stayed `pending` until a generic "loop trap" message ended the run.

## Decision
- One scheduler (`agent/planner/scheduler.py`) drives one state machine (`agent/lifecycle.py`). All status strings come from `NodeState`/`RunState`.
- Steps run on a `ThreadPoolExecutor(max_workers)` inside the Celery task. A step is I/O-bound (an HTTP call to Ollama, or waiting on a container), so threads are sufficient and keep shared state simple.
- Failure policy: transitive dependents of a non-completed node become `BLOCKED`; independent branches continue.
- Step runners are injected (`StepRunner`), so tests and benchmarks use the production scheduler without an LLM.

## Alternatives considered
- *One Celery task per node (chord/group):* gives cross-machine parallelism, but adds broker round-trips per step, makes cancellation and failure propagation distributed problems, and is unnecessary while inference is the bottleneck on one machine.
- *asyncio:* would require async LLM/Docker clients throughout. Threads give the same concurrency for this workload with less change.

## Consequences
+ Deterministic, testable transitions; observed concurrency is measured, not assumed.
− Threads cannot be force-killed: timed-out steps are abandoned and bounded by their own I/O timeouts.
− Parallelism is limited to one worker process per run.
