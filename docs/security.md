# Security model

## Threat model

| Asset | Threat | In scope |
|---|---|---|
| Host machine (files, credentials, processes, network) | LLM-generated code or commands that are malicious, or simply buggy and destructive | **Yes**, the primary threat |
| Other tasks' workspaces | A step reading or writing outside its own task directory | Yes |
| API | Unauthenticated clients submitting tasks, editing agent prompts, or controlling processes | Partially (see "API exposure") |
| Prompt integrity | Task text that tries to make the planner emit dangerous plans | Mitigated by validation and authorization, not prevented |
| Container escape via kernel/Docker vulnerabilities | — | **Out of scope.** Containers are defence in depth, not a proven boundary |
| Supply chain of base images and Python packages | — | Out of scope beyond `pip-audit` |

The planner and step outputs are **untrusted**. Validity as JSON never authorizes execution.

## Execution policy: fail-closed

`agent/sandbox.py:get_sandbox()` returns a `DockerSandbox` or raises `SandboxUnavailableError`. With no Docker daemon or no sandbox image, every `terminal` call fails with an explicit error and **nothing runs**. File-only steps (writes confined to the task workspace) still work.

The previous behaviour, silently falling back to `subprocess` on the host with only a timeout, has been removed.

`UnsafeHostExecutor` exists for local development without Docker. It is used **only** when `AGENTOS_ALLOW_UNSAFE_HOST_EXECUTION=1` is set (exactly `1`), and it logs a warning on every command. **It is not a sandbox.** Code runs as your user and can read anything you can. What it does provide:

- argv execution (no shell), a scrubbed environment (`PATH`, `HOME=<workspace>`, `LANG` only), cwd = workspace;
- a timeout that kills the whole process group (tested: no orphaned children);
- output capped at `SANDBOX_MAX_OUTPUT_BYTES` while the pipes keep draining (tested);
- `RLIMIT_CPU` and `RLIMIT_FSIZE`; `RLIMIT_AS` only on Linux, because macOS does not enforce it.

## Docker sandbox controls

Each command runs in a fresh container that is always removed (`agent/sandbox.py:DockerSandbox.container_kwargs`):

| Control | Setting |
|---|---|
| Network | `network_mode=none`, `network_disabled=True`; configuration cannot enable it |
| Memory | `mem_limit = memswap_limit = SANDBOX_MEMORY_MB` (no swap) |
| CPU | `nano_cpus = SANDBOX_CPUS` |
| Processes | `pids_limit = SANDBOX_PIDS_LIMIT`, `nofile` ulimit 256, `init=True` |
| Privileges | non-root user (host uid, or 65534 if the worker runs as root), `cap_drop=ALL`, `no-new-privileges`, `privileged=False` |
| Filesystem | read-only root, `/tmp` tmpfs `noexec,nosuid` (`SANDBOX_TMPFS_MB`), only the task workspace bind-mounted |
| Secrets | container environment is exactly `HOME`, `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`; no host env, no Docker socket |
| Deadline | `container.wait(timeout)` then `kill`; result marked `timed_out`, exit 124 |
| Output | stdout/stderr captured separately and capped; OOM kills reported |
| Image | must already exist locally (`make sandbox-image`); pulling requires `AGENTOS_SANDBOX_PULL=1` |
| Workspace | refuses to mount `/`, `$HOME`, or any path with fewer than 3 components |

Commands are split with `shlex` and run as argv. `argv[0]` must be on an allowlist (`python`, `python3`, `pytest`, `ls`, `cat`, `echo`, `pwd`, `head`, `tail`, `wc`, `diff`, `grep`, `mkdir`, `touch`). The allowlist keeps agents on the intended workload. **The container is the boundary**: `python -c` can do anything Python can do inside the container.

## Tool authorization

A step can perform a side effect only through `ToolRegistry.execute_tool`, which rejects:

- unknown tools (e.g. `git`, which was advertised before but not executable);
- tools not authorized for the step: `required_tools ∩ agent manifest tools ∩ risk-level tools`. Under `risk_level=safe`, `terminal` is never available, and plans that request it are rejected at validation;
- arguments outside the tool's strict schema (`extra="forbid"`, `strict=True`). The planner cannot pass `timeout`, `network`, memory or any other limit.

File tools resolve paths with `realpath` + `commonpath`, which rejects `..`, absolute paths and symlinks that leave the workspace. Writes are size-capped (`MAX_FILE_BYTES`) and atomic (temp file + `os.replace`). They also honour the task's `file_scope`.

## API exposure

- With `API_KEY` set, mutating endpoints (`/tasks/create`, `/retry`, `/tasks/{id}/cancel`, `/agents/save`, `/agents/{name}/test`, `/system/start|stop`) require `X-API-Key` (constant-time compare). **Without it, the API is unauthenticated. Bind it to localhost only.** Read endpoints are never authenticated.
- `POST /system/start|stop` (spawns and kills Redis/Celery) returns 403 unless `AGENTOS_ENABLE_PROCESS_CONTROL=1`.
- `/agents/save` and `/agents/{name}/content` used to accept arbitrary names, which allowed writing and reading `.md` files outside `agents/`. Names must now match `^[a-z][a-z0-9_-]{0,31}$`. Saved manifests still pass the loader's content policy, but **a key holder can rewrite agent system prompts**. That changes what the model is asked to do, not what the executor permits.
- Request bodies reject unknown fields (`extra="forbid"`), and model names are pattern-checked.
- Per-IP rate limiting is Redis-backed and **fails open** if Redis is down.

## Verified vs not verified (this environment)

| Property | Evidence |
|---|---|
| Fail-closed without Docker (no host execution) | `tests/security/test_sandbox.py::TestFailClosed`, E2E `test_command_step_fails_closed_without_sandbox` (verified) |
| Container settings as specified | `TestDockerIsolationSettings` against the exact SDK kwargs (verified without a daemon) |
| Network blocked, memory/PID limits, read-only FS, infinite-loop kill **inside a real container** | `TestRealDockerEnforcement`: **not run here** (Docker not installed on the audit machine). Run `make sandbox-image && pytest -m docker` |
| Unsafe executor timeout, process-group cleanup, output cap, env scrubbing | `TestUnsafeHostExecutor` (verified on macOS) |
| Planner cannot override limits / use unknown tools / add fields | `test_plan_validation.py`, `TestCommandPolicy`, `TestRegistry` (verified) |

## Known limitations

1. Real-container enforcement has not been demonstrated on this machine (see above).
2. Running the sandbox from the docker-compose worker requires giving that worker access to a Docker daemon. Mounting `/var/run/docker.sock` makes the worker root-equivalent on the host. Prefer rootless Docker or a dedicated VM. Compose does not mount the socket, so in compose command steps fail closed.
3. The workspace is mounted read-write. Code in a step can modify or delete any file in its own task's workspace.
4. No per-task disk quota inside the workspace (writes are capped per file and per tool call; the tmpfs is capped).
5. Abandoned (timed-out) step threads finish in the background, bounded by LLM and sandbox timeouts.
6. Agent manifests' content policy (`agent/loader.py`) is a keyword blocklist. It is not a security boundary.
7. `pip-audit` (2026-10-27): `pytest 8.4.2` PYSEC-2026-1845 (dev-only; fixed in 9.0.3, which needs a pytest-asyncio upgrade). `chromadb` advisories apply to its multi-tenant server API. chromadb is now an optional extra used only in embedded mode.
