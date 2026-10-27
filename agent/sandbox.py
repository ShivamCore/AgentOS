"""
AgentOS — Command execution sandbox
====================================
Every command an agent asks to run goes through ``get_sandbox()``. The policy
is **fail-closed**:

* The only isolating backend is ``DockerSandbox`` (one throw-away container per
  command, no network, capped CPU/memory/PIDs/output, non-root, all capabilities
  dropped, read-only root filesystem, only the task workspace mounted).
* If Docker (or the sandbox image) is unavailable, ``get_sandbox()`` raises
  ``SandboxUnavailableError``. There is no silent fallback.
* ``UnsafeHostExecutor`` exists only for local development on machines without
  Docker. It must be enabled explicitly with
  ``AGENTOS_ALLOW_UNSAFE_HOST_EXECUTION=1``, logs a warning on every use, and is
  **not a security boundary**: it runs generated code as the current user on the
  host with a timeout, a scrubbed environment, output caps and best-effort rlimits.

Resource limits come from server-side configuration (``SandboxPolicy.from_env``)
and are never taken from planner or LLM output.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time

from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:  # The SDK is optional at import time so the rest of AgentOS can load without it.
    import docker.errors
    import docker.types

    import docker

    DOCKER_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on environment
    docker = None  # type: ignore[assignment]
    DOCKER_SDK_AVAILABLE = False

UNSAFE_ENV_FLAG = "AGENTOS_ALLOW_UNSAFE_HOST_EXECUTION"
DEFAULT_IMAGE = "agentos-sandbox:latest"
# Path *inside* the container (a size-capped noexec tmpfs), not a host temp dir.
CONTAINER_TMP = "/tmp"  # noqa: S108  # nosec B108

# argv[0] values an agent may invoke. This is a policy guard that keeps agents on
# the intended workload; the container, not this list, is the security boundary.
COMMAND_ALLOWLIST = frozenset(
    {
        "python",
        "python3",
        "pytest",
        "ls",
        "cat",
        "echo",
        "pwd",
        "head",
        "tail",
        "wc",
        "diff",
        "grep",
        "mkdir",
        "touch",
    }
)


class SandboxUnavailableError(RuntimeError):
    """Raised when no acceptable execution backend is available (fail-closed)."""


class CommandRejectedError(ValueError):
    """Raised when a command is malformed or not permitted by policy."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class SandboxPolicy:
    """Server-side execution limits. Immutable; not influenced by agent output."""

    timeout_sec: int = 60
    memory_mb: int = 512
    cpus: float = 1.0
    pids_limit: int = 128
    max_output_bytes: int = 64 * 1024
    tmpfs_mb: int = 64
    image: str = DEFAULT_IMAGE
    network_disabled: bool = True

    @classmethod
    def from_env(cls) -> SandboxPolicy:
        return cls(
            timeout_sec=_env_int("SANDBOX_TIMEOUT_SECONDS", cls.timeout_sec),
            memory_mb=_env_int("SANDBOX_MEMORY_MB", cls.memory_mb),
            cpus=float(os.getenv("SANDBOX_CPUS", str(cls.cpus))),
            pids_limit=_env_int("SANDBOX_PIDS_LIMIT", cls.pids_limit),
            max_output_bytes=_env_int("SANDBOX_MAX_OUTPUT_BYTES", cls.max_output_bytes),
            tmpfs_mb=_env_int("SANDBOX_TMPFS_MB", cls.tmpfs_mb),
            image=os.getenv("SANDBOX_IMAGE", DEFAULT_IMAGE),
            # Network can only be *disabled* by configuration; there is no flag to enable it.
            network_disabled=True,
        )


@dataclass
class SandboxResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_sec: float
    backend: str
    timed_out: bool = False
    truncated: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration_sec"] = round(self.duration_sec, 3)
        return d


def parse_command(cmd: str) -> list[str]:
    """Split a command string into argv (no shell) and enforce the allowlist."""
    if not isinstance(cmd, str) or not cmd.strip():
        raise CommandRejectedError("empty command")
    if "\x00" in cmd:
        raise CommandRejectedError("NUL byte in command")
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        raise CommandRejectedError(f"invalid command syntax: {exc}") from exc
    prog = os.path.basename(argv[0])
    if prog not in COMMAND_ALLOWLIST:
        raise CommandRejectedError(
            f"command '{prog}' is not permitted; allowed: {', '.join(sorted(COMMAND_ALLOWLIST))}"
        )
    return argv


def _validate_workspace(workspace_dir: str) -> str:
    real = os.path.realpath(workspace_dir)
    if not os.path.isdir(real):
        raise CommandRejectedError(f"workspace does not exist: {workspace_dir}")
    home = os.path.realpath(os.path.expanduser("~"))
    if real in ("/", home) or len(real.split(os.sep)) < 3:
        raise CommandRejectedError(
            f"refusing to mount a broad host path as workspace: {real}"
        )
    return real


def _decode_capped(data: bytes, cap: int) -> tuple[str, bool]:
    truncated = len(data) > cap
    text = data[:cap].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n[... output truncated at {cap} bytes ...]"
    return text, truncated


# ── Docker backend ──────────────────────────────────────────────────────────────


class DockerSandbox:
    """One disposable container per command."""

    backend = "docker"

    def __init__(
        self,
        workspace_dir: str,
        policy: SandboxPolicy | None = None,
        client: Any = None,
    ) -> None:
        self.workspace_dir = _validate_workspace(workspace_dir)
        self.policy = policy or SandboxPolicy.from_env()
        if client is None:
            if not DOCKER_SDK_AVAILABLE:
                raise SandboxUnavailableError("docker Python SDK is not installed")
            try:
                client = docker.from_env()  # type: ignore[attr-defined]
                client.ping()
            except Exception as exc:
                raise SandboxUnavailableError(
                    f"Docker daemon is not reachable: {exc}"
                ) from exc
        self.client = client
        self._ensure_image()

    def _ensure_image(self) -> None:
        try:
            self.client.images.get(self.policy.image)
        except Exception as exc:
            if os.getenv("AGENTOS_SANDBOX_PULL") == "1":
                self.client.images.pull(self.policy.image)
                return
            raise SandboxUnavailableError(
                f"sandbox image '{self.policy.image}' is not present locally; "
                "build it with `make sandbox-image` (or set AGENTOS_SANDBOX_PULL=1)"
            ) from exc

    def container_kwargs(self, argv: list[str]) -> dict[str, Any]:
        """The exact isolation settings used for every container (unit-tested)."""
        p = self.policy
        uid = os.getuid() if hasattr(os, "getuid") else 65534
        user = f"{uid}:{os.getgid()}" if uid != 0 else "65534:65534"
        kwargs: dict[str, Any] = {
            "image": p.image,
            "command": argv,
            "working_dir": "/workspace",
            "user": user,
            "environment": {
                "HOME": CONTAINER_TMP,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            },  # nosec B108
            "volumes": {self.workspace_dir: {"bind": "/workspace", "mode": "rw"}},
            "network_mode": "none",
            "network_disabled": True,
            "mem_limit": f"{p.memory_mb}m",
            "memswap_limit": f"{p.memory_mb}m",
            "nano_cpus": int(p.cpus * 1e9),
            "pids_limit": p.pids_limit,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "privileged": False,
            "read_only": True,
            "tmpfs": {CONTAINER_TMP: f"rw,noexec,nosuid,size={p.tmpfs_mb}m"},
            "init": True,
            "detach": True,
        }
        if DOCKER_SDK_AVAILABLE:
            kwargs["ulimits"] = [docker.types.Ulimit(name="nofile", soft=256, hard=256)]
        return kwargs

    def run(self, argv: list[str]) -> SandboxResult:
        start = time.monotonic()
        container = None
        timed_out = False
        try:
            kwargs = self.container_kwargs(argv)
            image = kwargs.pop("image")
            container = self.client.containers.create(image, **kwargs)
            container.start()
            try:
                status = container.wait(timeout=self.policy.timeout_sec)
                exit_code = int(status.get("StatusCode", 1))
            except Exception:
                # docker-py raises requests' ReadTimeout/ConnectionError on wait timeout.
                timed_out = True
                exit_code = 124
                try:
                    container.kill()
                except Exception:
                    logger.warning("failed to kill timed-out container", exc_info=True)
            cap = self.policy.max_output_bytes
            out, t1 = _decode_capped(
                container.logs(stdout=True, stderr=False) or b"", cap
            )
            err, t2 = _decode_capped(
                container.logs(stdout=False, stderr=True) or b"", cap
            )
            state: dict[str, Any] = {}
            try:
                container.reload()
                state = (
                    container.attrs.get("State", {})
                    if isinstance(container.attrs, dict)
                    else {}
                )
            except Exception:
                logger.debug("could not inspect container state", exc_info=True)
            if state.get("OOMKilled"):
                err += "\n[sandbox] process killed: memory limit exceeded"
            if timed_out:
                err += f"\n[sandbox] timed out after {self.policy.timeout_sec}s; container killed"
            return SandboxResult(
                success=(exit_code == 0 and not timed_out),
                stdout=out,
                stderr=err.strip(),
                exit_code=exit_code,
                duration_sec=time.monotonic() - start,
                backend=self.backend,
                timed_out=timed_out,
                truncated=t1 or t2,
                meta={"oom_killed": bool(state.get("OOMKilled"))},
            )
        except SandboxUnavailableError:
            raise
        except Exception as exc:
            return SandboxResult(
                False,
                "",
                f"sandbox error: {exc}",
                1,
                time.monotonic() - start,
                self.backend,
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    logger.warning("failed to remove sandbox container", exc_info=True)

    def execute_command(self, cmd: str) -> SandboxResult:
        return self.run(parse_command(cmd))


# ── Explicitly-enabled, NON-isolating host executor ─────────────────────────────


class UnsafeHostExecutor:
    """
    Development-only executor. NOT A SANDBOX: code runs on the host as the
    current user and can read anything that user can read. Provided so the
    pipeline can be exercised without Docker; never selected implicitly.
    """

    backend = "unsafe-host"

    def __init__(self, workspace_dir: str, policy: SandboxPolicy | None = None) -> None:
        self.workspace_dir = _validate_workspace(workspace_dir)
        self.policy = policy or SandboxPolicy.from_env()

    def _preexec(self) -> None:  # pragma: no cover - runs in the child
        try:
            import resource

            cpu = max(1, int(self.policy.timeout_sec))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
            fsize = 16 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
            if sys.platform.startswith("linux"):  # RLIMIT_AS is not enforced on macOS
                mem = self.policy.memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        except (ImportError, ValueError, OSError):
            pass  # best effort; limits are documented as not guaranteed

    def run(self, argv: list[str]) -> SandboxResult:
        logger.warning("UNSAFE host execution (no isolation): %s", argv)
        cap = self.policy.max_output_bytes
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.workspace_dir,
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if argv[0] in ("python", "python3"):
            argv = [sys.executable, *argv[1:]]
        elif argv[0] == "pytest":
            argv = [sys.executable, "-m", "pytest", *argv[1:]]
        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv,
                cwd=self.workspace_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                preexec_fn=self._preexec if os.name == "posix" else None,
            )
        except OSError as exc:
            return SandboxResult(
                False, "", f"failed to start: {exc}", 127, 0.0, self.backend
            )

        buffers: dict[str, bytearray] = {"out": bytearray(), "err": bytearray()}
        overflow = {"out": False, "err": False}

        def _drain(stream: Any, key: str) -> None:
            for chunk in iter(lambda: stream.read(8192), b""):
                buf = buffers[key]
                room = cap + 1 - len(buf)
                if room > 0:
                    buf.extend(chunk[:room])
                if len(chunk) > room:
                    overflow[key] = True

        readers = [
            threading.Thread(target=_drain, args=(proc.stdout, "out"), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, "err"), daemon=True),
        ]
        for t in readers:
            t.start()
        timed_out = False
        try:
            proc.wait(timeout=self.policy.timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
        # Kill the whole process group so children cannot outlive the step.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        for t in readers:
            t.join(timeout=2)
        out, t1 = _decode_capped(bytes(buffers["out"]), cap)
        err, t2 = _decode_capped(bytes(buffers["err"]), cap)
        if timed_out:
            err += f"\n[unsafe-host] timed out after {self.policy.timeout_sec}s; process group killed"
        code = 124 if timed_out else proc.returncode
        return SandboxResult(
            success=(code == 0),
            stdout=out,
            stderr=err.strip(),
            exit_code=code,
            duration_sec=time.monotonic() - start,
            backend=self.backend,
            timed_out=timed_out,
            truncated=t1 or t2 or overflow["out"] or overflow["err"],
        )

    def execute_command(self, cmd: str) -> SandboxResult:
        return self.run(parse_command(cmd))


def unsafe_host_execution_enabled() -> bool:
    return os.getenv(UNSAFE_ENV_FLAG, "").strip() == "1"


def get_sandbox(
    workspace_dir: str, policy: SandboxPolicy | None = None
) -> DockerSandbox | UnsafeHostExecutor:
    """
    Return the execution backend. Docker is required unless the operator has
    explicitly opted into unsafe host execution. Never degrades silently.
    """
    try:
        return DockerSandbox(workspace_dir, policy)
    except SandboxUnavailableError as exc:
        if unsafe_host_execution_enabled():
            logger.warning(
                "Docker sandbox unavailable (%s); %s=1 is set, using UNSAFE host execution",
                exc,
                UNSAFE_ENV_FLAG,
            )
            return UnsafeHostExecutor(workspace_dir, policy)
        raise SandboxUnavailableError(
            f"{exc}. Command execution is disabled (fail-closed). Install/start Docker and build the "
            f"sandbox image, or for local development only set {UNSAFE_ENV_FLAG}=1."
        ) from exc


def sandbox_status() -> dict[str, Any]:
    """Diagnostic used by `agentos doctor` and /health."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="agentos-probe-") as d:
        try:
            DockerSandbox(d)
            return {
                "backend": "docker",
                "available": True,
                "detail": "Docker sandbox ready",
            }
        except (SandboxUnavailableError, CommandRejectedError) as exc:
            if unsafe_host_execution_enabled():
                return {
                    "backend": "unsafe-host",
                    "available": True,
                    "detail": f"UNSAFE host execution enabled ({exc})",
                }
            return {"backend": None, "available": False, "detail": str(exc)}
