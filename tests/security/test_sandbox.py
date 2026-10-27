"""
Sandbox security tests.

* Fail-closed behaviour and command policy: always run (no Docker needed).
* Docker isolation settings: verified against the exact kwargs passed to the
  Docker SDK, using a fake client (always run).
* Real container enforcement (network, memory, PIDs, read-only FS, timeouts):
  marked ``docker``; skipped unless a Docker daemon and the agentos-sandbox
  image are available.
* UnsafeHostExecutor: verifies timeouts, process-group cleanup, output caps and
  environment scrubbing using harmless local commands. It is NOT a sandbox and
  these tests do not claim isolation.
"""

from __future__ import annotations

import os
import sys
import time
from unittest.mock import MagicMock

import pytest

from agent import sandbox as sb
from agent.sandbox import (
    CommandRejectedError,
    DockerSandbox,
    SandboxPolicy,
    SandboxUnavailableError,
    UnsafeHostExecutor,
    get_sandbox,
    parse_command,
)
from agent.tools.registry import registry


@pytest.fixture()
def ws(tmp_path):
    d = tmp_path / "workspace"
    d.mkdir()
    return str(d)


@pytest.fixture()
def no_docker(monkeypatch):
    def boom(*a, **k):
        raise SandboxUnavailableError("Docker daemon is not reachable: test")

    monkeypatch.setattr(sb.DockerSandbox, "__init__", boom)
    monkeypatch.delenv(sb.UNSAFE_ENV_FLAG, raising=False)


class TestFailClosed:
    def test_get_sandbox_raises_without_docker(self, ws, no_docker):
        with pytest.raises(SandboxUnavailableError, match="fail-closed"):
            get_sandbox(ws)

    def test_terminal_tool_refuses_without_docker(self, ws, no_docker):
        marker = os.path.join(ws, "pwned")
        res = registry.execute_tool("terminal", {"command": f"touch {marker}"}, ws)
        assert not res.success
        assert "fail-closed" in (res.error or "")
        assert not os.path.exists(marker), "command must not have executed on the host"

    def test_unsafe_mode_requires_explicit_flag(self, ws, no_docker, monkeypatch):
        monkeypatch.setenv(sb.UNSAFE_ENV_FLAG, "true")  # only the literal "1" enables it
        with pytest.raises(SandboxUnavailableError):
            get_sandbox(ws)
        monkeypatch.setenv(sb.UNSAFE_ENV_FLAG, "1")
        assert isinstance(get_sandbox(ws), UnsafeHostExecutor)


class TestCommandPolicy:
    @pytest.mark.parametrize("cmd", [
        "", "   ", "rm -rf /", "bash -c 'id'", "sh -c id", "curl http://x", "docker ps",
        "python 'unterminated", "echo hi\x00", "/bin/sh",
    ])
    def test_rejected(self, cmd):
        with pytest.raises(CommandRejectedError):
            parse_command(cmd)

    def test_no_shell_interpretation(self):
        # Shell metacharacters become literal argv items, not a second command.
        assert parse_command("echo hi; rm -rf /") == ["echo", "hi;", "rm", "-rf", "/"]
        assert parse_command("python -c 'print(1)'") == ["python", "-c", "print(1)"]

    def test_planner_cannot_override_limits(self, ws):
        res = registry.execute_tool("terminal", {"command": "echo hi", "timeout_sec": 99999, "network": True}, ws)
        assert not res.success and "invalid arguments" in res.error

    def test_policy_network_cannot_be_enabled(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_NETWORK", "1")
        assert SandboxPolicy.from_env().network_disabled is True

    @pytest.mark.parametrize("bad", ["/", os.path.expanduser("~")])
    def test_refuses_broad_workspace_mounts(self, bad):
        with pytest.raises(CommandRejectedError):
            DockerSandbox(bad, client=MagicMock())


class TestDockerIsolationSettings:
    """The exact container configuration, checked without a daemon."""

    def make(self, ws, **policy):
        client = MagicMock()
        return DockerSandbox(ws, SandboxPolicy(**policy), client=client), client

    def test_container_kwargs(self, ws):
        box, _ = self.make(ws, memory_mb=256, cpus=0.5, pids_limit=64, tmpfs_mb=32)
        kw = box.container_kwargs(["python", "x.py"])
        assert kw["network_mode"] == "none" and kw["network_disabled"] is True
        assert kw["mem_limit"] == "256m" and kw["memswap_limit"] == "256m"
        assert kw["nano_cpus"] == 500_000_000
        assert kw["pids_limit"] == 64
        assert kw["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in kw["security_opt"]
        assert kw["privileged"] is False
        assert kw["read_only"] is True
        assert kw["tmpfs"] == {"/tmp": "rw,noexec,nosuid,size=32m"}
        assert kw["user"].split(":")[0] != "0"
        assert kw["command"] == ["python", "x.py"]  # argv, no `sh -c`

    def test_only_workspace_is_mounted_and_no_secrets_passed(self, ws, monkeypatch):
        monkeypatch.setenv("SUPER_SECRET_TOKEN", "hunter2")
        box, _ = self.make(ws)
        kw = box.container_kwargs(["python", "x.py"])
        assert list(kw["volumes"]) == [os.path.realpath(ws)]
        assert all("docker.sock" not in k for k in kw["volumes"])
        assert "SUPER_SECRET_TOKEN" not in kw["environment"]
        assert set(kw["environment"]) == {"HOME", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED"}

    def test_missing_image_fails_closed(self, ws, monkeypatch):
        monkeypatch.delenv("AGENTOS_SANDBOX_PULL", raising=False)
        client = MagicMock()
        client.images.get.side_effect = Exception("no such image")
        with pytest.raises(SandboxUnavailableError, match="not present"):
            DockerSandbox(ws, SandboxPolicy(), client=client)
        client.images.pull.assert_not_called()

    def test_timeout_kills_and_removes_container(self, ws):
        box, client = self.make(ws, timeout_sec=1)
        container = client.containers.create.return_value
        container.wait.side_effect = Exception("Read timed out")
        container.logs.return_value = b""
        container.attrs = {"State": {"OOMKilled": False}}
        res = box.run(["python", "loop.py"])
        assert res.timed_out and not res.success and res.exit_code == 124
        container.kill.assert_called_once()
        container.remove.assert_called_once_with(force=True)

    def test_output_is_capped_and_oom_reported(self, ws):
        box, client = self.make(ws, max_output_bytes=100)
        container = client.containers.create.return_value
        container.wait.return_value = {"StatusCode": 137}
        container.logs.side_effect = lambda stdout, stderr: (b"x" * 10_000) if stdout else b""
        container.attrs = {"State": {"OOMKilled": True}}
        res = box.run(["python", "big.py"])
        assert res.truncated and len(res.stdout) < 200
        assert "memory limit" in res.stderr and res.meta["oom_killed"]
        container.remove.assert_called_once_with(force=True)

    def test_container_removed_even_if_start_fails(self, ws):
        box, client = self.make(ws)
        container = client.containers.create.return_value
        container.start.side_effect = RuntimeError("start failed")
        res = box.run(["python", "x.py"])
        assert not res.success
        container.remove.assert_called_once_with(force=True)


@pytest.mark.unsafe_host
class TestUnsafeHostExecutor:
    def test_timeout_kills_process_group_no_orphans(self, ws):
        script = os.path.join(ws, "spawn.py")
        pidfile = os.path.join(ws, "child.pid")
        with open(script, "w") as f:
            f.write(
                "import subprocess, sys, time\n"
                f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({pidfile!r}, 'w').write(str(p.pid))\n"
                "time.sleep(60)\n"
            )
        ex = UnsafeHostExecutor(ws, SandboxPolicy(timeout_sec=2))
        t0 = time.monotonic()
        res = ex.run(["python", "spawn.py"])
        assert res.timed_out and res.exit_code == 124
        assert time.monotonic() - t0 < 10
        child = int(open(pidfile).read())
        time.sleep(0.2)
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)

    def test_output_capped(self, ws):
        ex = UnsafeHostExecutor(ws, SandboxPolicy(max_output_bytes=1000, timeout_sec=20))
        res = ex.run(["python", "-c", "print('y' * 5_000_000)"])
        assert res.success and res.truncated
        assert len(res.stdout) < 1100

    def test_environment_is_scrubbed(self, ws, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "do-not-leak")
        ex = UnsafeHostExecutor(ws, SandboxPolicy(timeout_sec=20))
        res = ex.run(["python", "-c", "import os; print(sorted(os.environ))"])
        assert "AWS_SECRET_ACCESS_KEY" not in res.stdout
        assert "do-not-leak" not in res.stdout

    @pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is only enforced on Linux")
    def test_memory_rlimit_linux(self, ws):
        ex = UnsafeHostExecutor(ws, SandboxPolicy(memory_mb=200, timeout_sec=20))
        res = ex.run(["python", "-c", "x = bytearray(1024 * 1024 * 1024)"])
        assert not res.success


def _docker_ready() -> bool:
    try:
        import tempfile

        with tempfile.TemporaryDirectory(dir=os.getcwd(), prefix=".probe-") as d:
            DockerSandbox(d)
        return True
    except Exception:
        return False


docker_only = pytest.mark.skipif(not _docker_ready(), reason="Docker daemon or agentos-sandbox image unavailable")


@pytest.mark.docker
@docker_only
class TestRealDockerEnforcement:
    def run_py(self, ws, code, **policy):
        with open(os.path.join(ws, "t.py"), "w") as f:
            f.write(code)
        return DockerSandbox(ws, SandboxPolicy(**policy)).run(["python", "t.py"])

    def test_network_blocked(self, ws):
        r = self.run_py(ws, "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)", timeout_sec=20)
        assert not r.success

    def test_memory_bounded(self, ws):
        r = self.run_py(ws, "x = bytearray(1024 * 1024 * 1024)", memory_mb=128, timeout_sec=30)
        assert not r.success

    def test_fork_bomb_bounded(self, ws):
        r = self.run_py(ws, "import os\nwhile True:\n    os.fork()", pids_limit=32, timeout_sec=10)
        assert not r.success

    def test_infinite_loop_terminated(self, ws):
        t0 = time.monotonic()
        r = self.run_py(ws, "while True: pass", timeout_sec=3)
        assert r.timed_out and time.monotonic() - t0 < 20

    def test_root_fs_read_only_and_no_host_env(self, ws, monkeypatch):
        monkeypatch.setenv("HOST_SECRET", "nope")
        r = self.run_py(ws, "import os\nprint(os.environ.get('HOST_SECRET'))\nopen('/etc/x', 'w')", timeout_sec=20)
        assert not r.success and "nope" not in r.stdout
