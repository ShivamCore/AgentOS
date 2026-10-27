# ADR 0001 — Fail-closed command execution with Docker as the only sandbox

**Status:** accepted (2026-10)

## Context
Agents run LLM-generated commands. The previous `get_sandbox()` silently fell back to `subprocess.run` on the host whenever Docker was unavailable. That fallback had a timeout but no CPU, memory or filesystem isolation, and the README described it as enforcing "hard limits". Separately, `agent/executor.py` ran `pytest`/`python` on generated files directly on the host, and its rollback path ran `kill -9` on whatever held ports 8000/3000/5000/8080, including the AgentOS API itself.

## Decision
- `DockerSandbox` is the only isolating backend. With no Docker, `get_sandbox()` raises `SandboxUnavailableError` and command steps fail with an explicit error.
- A non-isolating `UnsafeHostExecutor` exists for development, enabled only by `AGENTOS_ALLOW_UNSAFE_HOST_EXECUTION=1`, and is documented as **not a sandbox**.
- No code path outside `agent/sandbox.py` starts a process for agent output.
- No custom Python-level sandbox (restricted builtins, AST filters). Those are known to be bypassable.

## Consequences
+ A misconfigured machine cannot silently run generated code on the host.
+ The security claim is narrow and testable: container settings are unit-tested against the SDK kwargs.
− Without Docker, only file-writing steps work. Demos without Docker should use `risk_level=safe`.
− Docker is defence in depth, not a proven boundary (kernel and runtime escapes are out of scope).
