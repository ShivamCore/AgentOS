# ADR 0005 — Treat planner and step output as untrusted input

**Status:** accepted (2026-10)

## Decision
- Plans are rejected, not repaired: unknown fields, dangling dependencies, cycles, unknown tools or agents, and size/depth violations all raise `PlanValidationError`. The old code silently dropped dangling dependencies, which changed the meaning of the plan.
- Authorization is enforced again at execution time, independently of validation: tools = plan ∩ agent manifest ∩ risk level ∩ registry; strict tool schemas; workspace path confinement; `file_scope`.
- No fabricated fallback plan when planning fails. The previous code turned the entire task description into a single step and ran it.
- Ollama JSON mode (`format: "json"`) is used for planner and step calls, with output capped at 1024 tokens for the planner. In a 4-trial probe on `phi3.5`, both JSON-mode trials produced valid plans and both non-JSON trials failed. The non-JSON failures were caused by trailing text that the parser has since been fixed to tolerate, so this is **not** a valid A/B result. It is recorded only as the reason for the default.

## Consequences
Small local models fail more visibly (`PlannerError`) instead of executing a degraded plan.
