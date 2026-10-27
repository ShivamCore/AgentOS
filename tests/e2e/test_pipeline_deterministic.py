"""
Deterministic end-to-end tests: HTTP API -> Celery task function -> planner
validation -> DAG scheduler -> step executor -> tool registry -> filesystem /
sandbox -> DB -> HTTP API.

Only the LLM is replaced (by a scripted function patched into agent.selector);
everything else is the production code path. The Celery task is invoked
directly, exactly as a worker would, against a file-backed SQLite database.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent import sandbox as sb

pytestmark = pytest.mark.e2e

TASK = {
    "title": "Two modules and tests",
    "description": "Create mathutils.add, strutils.shout and pytest tests for both.",
    "task_type": "build_app",
    "constraints": {"max_time": 60, "max_steps": 5, "risk_level": "balanced"},
}

FILES = {
    "a": ("mathutils.py", "def add(a, b):\n    return a + b\n"),
    "b": ("strutils.py", "def shout(s):\n    return s.upper() + '!'\n"),
    "c": ("test_utils.py",
          "from mathutils import add\nfrom strutils import shout\n\n\n"
          "def test_add():\n    assert add(2, 3) == 5\n\n\n"
          "def test_shout():\n    assert shout('hi') == 'HI!'\n"),
}


def plan_json(with_test_step: bool) -> str:
    steps = [
        {"step_id": "a", "description": "STEP[a] write mathutils.py", "required_tools": ["file_write"]},
        {"step_id": "b", "description": "STEP[b] write strutils.py", "required_tools": ["file_write"]},
        {"step_id": "c", "description": "STEP[c] write test_utils.py", "required_tools": ["file_write"],
         "dependencies": ["a", "b"]},
    ]
    if with_test_step:
        steps.append({"step_id": "d", "description": "STEP[d] run the tests", "required_tools": ["terminal"],
                      "dependencies": ["c"]})
    return json.dumps({"task_id": "auto_generated", "steps": steps})


class ScriptedLLM:
    """Deterministic stand-in for generate_text keyed on the step marker in the prompt."""

    def __init__(self, plan: str, hooks: dict[str, Callable[[], None]] | None = None) -> None:
        self.plan = plan
        self.hooks = hooks or {}
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def __call__(self, prompt: str, system_prompt: str = "", task_type: str = "code", **kw) -> str:
        with self.lock:
            self.calls.append(task_type)
        if task_type == "plan":
            return self.plan
        sid = prompt.split("STEP[", 1)[1][0]
        if sid in self.hooks:
            self.hooks[sid]()
        if sid == "d":
            return json.dumps({"files": [], "command": "pytest -q"})
        path, code = FILES[sid]
        return "```json\n" + json.dumps({"files": [{"path": path, "code": code}], "command": None}) + "\n```"


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    """API client + worker entry point wired to an isolated file DB, workspace and fake Redis."""
    from backend.api import main as api_main
    from backend.api.routers import task as task_router
    from backend.db import database
    from backend.db.database import get_db
    from backend.db.migrations import ensure_schema
    from backend.workers import tasks as worker

    engine = create_engine(f"sqlite:///{tmp_path / 'e2e.db'}", connect_args={"check_same_thread": False, "timeout": 30})
    ensure_schema(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", Session)
    monkeypatch.setattr(worker, "SessionLocal", Session)
    monkeypatch.setattr(worker, "CANCEL_POLL_SECONDS", 0.05)
    monkeypatch.setattr(worker._workspace_manager, "root", str(tmp_path / "workspaces"))
    monkeypatch.setenv("OLLAMA_MODEL", "scripted-model")  # router must not query a real Ollama
    monkeypatch.delenv(sb.UNSAFE_ENV_FLAG, raising=False)

    def no_docker(*a, **k):
        raise sb.SandboxUnavailableError("Docker daemon is not reachable (test)")

    monkeypatch.setattr(sb.DockerSandbox, "__init__", no_docker)

    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(task_router._limiter, "_redis", fake)
    dispatched: list[list] = []
    monkeypatch.setattr(task_router, "run_agent_task",
                        MagicMock(apply_async=lambda args, **kw: dispatched.append(args)))

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    api_main.app.dependency_overrides[get_db] = _db
    with patch("redis.from_url", return_value=fake), TestClient(api_main.app) as client:
        def run_worker(task_id: str) -> dict:
            return worker.run_agent_task(task_id, "Auto")

        yield client, run_worker, dispatched, tmp_path / "workspaces"
    api_main.app.dependency_overrides.clear()


def submit(client, **overrides) -> str:
    body = {**TASK, **overrides}
    r = client.post("/tasks/create", json=body)
    assert r.status_code == 202, r.text
    return r.json()["task_id"]


def test_full_pipeline_completes_with_parallel_branches(stack):
    client, run_worker, dispatched, root = stack
    barrier = threading.Barrier(2, timeout=5)  # a and b must be in flight together
    llm = ScriptedLLM(plan_json(with_test_step=False), hooks={"a": barrier.wait, "b": barrier.wait})

    task_id = submit(client)
    assert dispatched == [[task_id, "Auto"]]
    with patch("agent.selector.generate_text", llm):
        result = run_worker(task_id)

    assert result["status"] == "COMPLETED", result
    detail = client.get(f"/tasks/{task_id}").json()
    assert detail["status"] == "COMPLETED"
    assert {n["node_id"]: n["status"] for n in detail["nodes"]} == {"a": "COMPLETED", "b": "COMPLETED", "c": "COMPLETED"}
    assert detail["result"]["max_observed_concurrency"] == 2
    for path, code in FILES.values():
        assert (root / task_id / path).read_text() == code
    res = client.get(f"/tasks/{task_id}/result").json()
    assert res["files_modified"] == ["mathutils.py", "strutils.py", "test_utils.py"]
    logs = client.get(f"/logs/{task_id}").json()
    seqs = [entry["seq_id"] for entry in logs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert llm.calls.count("plan") == 1 and llm.calls.count("code") == 3


def test_command_step_fails_closed_without_sandbox(stack):
    client, run_worker, _, root = stack
    task_id = submit(client)
    with patch("agent.selector.generate_text", ScriptedLLM(plan_json(with_test_step=True))):
        result = run_worker(task_id)

    assert result["status"] == "PARTIAL_SUCCESS"
    detail = client.get(f"/tasks/{task_id}").json()
    nodes = {n["node_id"]: n for n in detail["nodes"]}
    assert nodes["d"]["status"] == "FAILED"
    assert "fail-closed" in nodes["d"]["error"]
    assert nodes["d"]["attempts"] == 2  # first attempt + one debugger retry (STEP_MAX_RETRIES=1)
    assert all(nodes[k]["status"] == "COMPLETED" for k in "abc")
    assert not (root / task_id / ".pytest_cache").exists(), "nothing may have executed on the host"


@pytest.mark.unsafe_host
def test_command_step_runs_with_explicit_unsafe_opt_in(stack, monkeypatch):
    client, run_worker, _, root = stack
    monkeypatch.setenv(sb.UNSAFE_ENV_FLAG, "1")
    task_id = submit(client)
    with patch("agent.selector.generate_text", ScriptedLLM(plan_json(with_test_step=True))):
        result = run_worker(task_id)
    assert result["status"] == "COMPLETED", client.get(f"/tasks/{task_id}").json()
    node_d = next(n for n in client.get(f"/tasks/{task_id}").json()["nodes"] if n["node_id"] == "d")
    assert node_d["status"] == "COMPLETED"


def test_safe_risk_level_forbids_terminal_in_plan(stack):
    client, run_worker, _, _ = stack
    task_id = submit(client, constraints={**TASK["constraints"], "risk_level": "safe"})
    with patch("agent.selector.generate_text", ScriptedLLM(plan_json(with_test_step=True))):
        result = run_worker(task_id)
    assert result["status"] == "FAILED"
    detail = client.get(f"/tasks/{task_id}").json()
    assert "terminal" in detail["error"] and detail["nodes"] == []


@pytest.mark.parametrize("bad_plan", ["I cannot help with that.", '{"steps": [{"step_id": "1", "description": "x", '
                                      '"dependencies": ["2"]}]}'])
def test_invalid_plan_fails_clearly(stack, bad_plan):
    client, run_worker, _, _ = stack
    task_id = submit(client)
    with patch("agent.selector.generate_text", ScriptedLLM(bad_plan)):
        result = run_worker(task_id)
    assert result["status"] == "FAILED"
    detail = client.get(f"/tasks/{task_id}").json()
    assert detail["status"] == "FAILED" and "PlannerError" in detail["error"]


def test_llm_unavailable_fails_clearly(stack):
    from agent.llm import LLMUnavailableError

    client, run_worker, _, _ = stack
    task_id = submit(client)
    with patch("agent.selector.generate_text", side_effect=LLMUnavailableError("connection refused")):
        result = run_worker(task_id)
    assert result["status"] == "FAILED"
    assert "LLMUnavailableError" in client.get(f"/tasks/{task_id}").json()["error"]


def test_duplicate_delivery_is_a_noop(stack):
    client, run_worker, _, _ = stack
    task_id = submit(client)
    llm = ScriptedLLM(plan_json(with_test_step=False))
    with patch("agent.selector.generate_text", llm):
        first = run_worker(task_id)
        second = run_worker(task_id)
    assert first["status"] == "COMPLETED"
    assert second["summary"].startswith("Not executed")
    assert llm.calls.count("plan") == 1


def test_cancel_before_start(stack):
    client, run_worker, _, _ = stack
    task_id = submit(client)
    assert client.post(f"/tasks/{task_id}/cancel").json()["status"] == "CANCELLED"
    llm = ScriptedLLM(plan_json(with_test_step=False))
    with patch("agent.selector.generate_text", llm):
        run_worker(task_id)
    assert llm.calls == []
    assert client.post(f"/tasks/{task_id}/cancel").status_code == 409


def test_cancel_while_running(stack):
    client, run_worker, _, _ = stack
    in_step = threading.Event()
    release = threading.Event()

    def block():
        in_step.set()
        release.wait(10)

    llm = ScriptedLLM(plan_json(with_test_step=False), hooks={"a": block, "b": block})
    task_id = submit(client)
    out: dict = {}
    with patch("agent.selector.generate_text", llm):
        t = threading.Thread(target=lambda: out.update(run_worker(task_id)))
        t.start()
        assert in_step.wait(10)
        assert client.post(f"/tasks/{task_id}/cancel").status_code == 202
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and client.get(f"/tasks/{task_id}").json()["status"] == "RUNNING":
            time.sleep(0.05)
        release.set()
        t.join(15)
    detail = client.get(f"/tasks/{task_id}").json()
    assert detail["status"] == "CANCELLED"
    assert {n["node_id"]: n["status"] for n in detail["nodes"]}["c"] == "CANCELLED"


def test_retry_resets_and_reruns(stack):
    client, run_worker, dispatched, _ = stack
    task_id = submit(client)
    with patch("agent.selector.generate_text", ScriptedLLM("garbage")):
        run_worker(task_id)
    assert client.get(f"/tasks/{task_id}").json()["status"] == "FAILED"
    assert client.post(f"/retry/{task_id}").status_code == 202
    assert dispatched[-1] == [task_id, "Auto"]
    with patch("agent.selector.generate_text", ScriptedLLM(plan_json(with_test_step=False))):
        assert run_worker(task_id)["status"] == "COMPLETED"
    assert client.post(f"/retry/{task_id}").status_code == 409


def test_broker_down_is_reported_not_hidden(stack, monkeypatch):
    from backend.api.routers import task as task_router

    client, _, _, _ = stack

    def down(*a, **k):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(task_router, "run_agent_task", MagicMock(apply_async=down))
    r = client.post("/tasks/create", json=TASK)
    assert r.status_code == 503
    tasks = client.get("/tasks").json()
    assert tasks[0]["status"] == "FAILED"


def test_api_key_enforced_when_configured(stack, monkeypatch):
    from backend.config import settings

    client, _, _, _ = stack
    monkeypatch.setattr(settings, "API_KEY", "s3cret")
    assert client.post("/tasks/create", json=TASK).status_code == 401
    r = client.post("/tasks/create", json=TASK, headers={"X-API-Key": "s3cret"})
    assert r.status_code == 202


def test_unknown_request_fields_rejected(stack):
    client, _, _, _ = stack
    r = client.post("/tasks/create", json={**TASK, "constraints": {**TASK["constraints"], "sandbox": "off"}})
    assert r.status_code == 422


def test_workspace_left_for_inspection(stack):
    client, run_worker, _, root = stack
    task_id = submit(client)
    with patch("agent.selector.generate_text", ScriptedLLM(plan_json(with_test_step=False))):
        result = run_worker(task_id)
    assert result["workspace"] == os.path.realpath(str(root / task_id))
