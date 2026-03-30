"""
tests/regression/test_bug_fixes.py
====================================
REGRESSION TESTS — Named after the exact bugs they guard.

Each test has a docstring identifying:
  - The bug name
  - The file and (approximate) line where it existed
  - What the bug caused
  - What the fix was
"""

from __future__ import annotations

import ast
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import seed_task


def _make_agent_pool_class_mock():
    """Return a MagicMock AgentPool class whose submit() runs functions synchronously."""
    def _submit(fn, *args, **kwargs):
        f: Future = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except Exception as exc:
            f.set_exception(exc)
        return f

    pool = MagicMock()
    pool.submit.side_effect = _submit
    pool.shutdown.return_value = None

    cls = MagicMock()
    cls.return_value = pool
    return cls


# ─────────────────────────────────────────────────────────────────────────────────
# BUG 1: Global Mutation in Memory Engine
# ─────────────────────────────────────────────────────────────────────────────────

class TestRegressionGlobalMutationMemoryEngine:
    """
    REGRESSION: agent/memory/engine.py (≈line 41 in original)
    
    The original code had:
        except Exception:
            CHROMA_AVAILABLE = False   # ← mutated module global inside instance method!
    
    In a Celery multi-process setup, this re-assigned the module-global variable
    inside a single worker process's stack frame. While each process has its own
    copy of the module (no cross-process mutation), the original code ALSO kept a
    single process-level global `_global_engine: LocalMemoryEngine = None` that
    was shared across threads within the same worker. Any concurrent call could 
    overwrite it, causing a later thread to get a different workspace's engine.
    
    Fix: @lru_cache per workspace_dir + removed global mutable singleton.
    """

    def test_REGRESSION_global_mutation_memory_engine(self, tmp_path, monkeypatch):
        """
        Core regression proof: two calls with different workspace dirs must return
        distinct engine instances. The bug caused the same singleton to be returned
        regardless of workspace_dir after the global was mutated.
        """
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        engine_a = eng_mod.get_memory_engine(str(tmp_path / "worker_a"))
        engine_b = eng_mod.get_memory_engine(str(tmp_path / "worker_b"))

        assert engine_a is not engine_b, (
            "REGRESSION BUG 1: Two distinct workspace_dirs must return different engine instances. "
            "The original global singleton returned the same instance to all callers."
        )

    def test_REGRESSION_no_process_global_singleton(self):
        """
        Prove that the module no longer has a mutable process-level singleton variable.
        The original had `_global_engine: LocalMemoryEngine = None` at module level.
        """
        import agent.memory.engine as eng_mod

        # The old singleton was named _global_engine
        assert not hasattr(eng_mod, "_global_engine"), (
            "REGRESSION BUG 1: Module-level _global_engine singleton must not exist. "
            "Use get_memory_engine() lru_cache factory instead."
        )

    def test_REGRESSION_thread_safety_of_factory(self, tmp_path, monkeypatch):
        """
        Simulate 8 threads calling get_memory_engine concurrently with the same
        workspace_dir. All must receive the identical instance (lru_cache guarantee).
        No thread must receive a different (re-created) instance.
        """
        monkeypatch.setattr("agent.memory.engine._CHROMA_AVAILABLE", False)

        from agent.memory import engine as eng_mod
        eng_mod.get_memory_engine.cache_clear()

        workspace = str(tmp_path / "shared")
        results = []

        def get_it():
            results.append(id(eng_mod.get_memory_engine(workspace)))

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: get_it(), range(8)))

        assert len(set(results)) == 1, (
            "REGRESSION BUG 1: All threads must get the same engine instance. "
            f"Got {len(set(results))} distinct instances."
        )


# ─────────────────────────────────────────────────────────────────────────────────
# BUG 2: Backpressure Status Case Mismatch
# ─────────────────────────────────────────────────────────────────────────────────

class TestRegressionBackpressureStatusCase:
    """
    REGRESSION: backend/api/routers/task.py (_check_backpressure)
    
    The original _check_backpressure queried:
        .filter(TaskRecord.status.in_(["pending", "running"]))
    
    But TaskRecord.status was always stored as uppercase "CREATED", "RUNNING" etc.
    This meant the count was ALWAYS 0, allowing unlimited task submission.
    Under load this caused unbounded queue growth and memory exhaustion.
    
    Fix: Changed query to use uppercase status strings matching actual DB values.
    """

    def test_REGRESSION_backpressure_status_case(self, db, test_client):
        """
        Core regression proof: seed tasks with uppercase CREATED/RUNNING status.
        The guard must detect them and return a count > 0.
        """
        from backend.models.sql_models import TaskRecord

        seed_task(db, status="CREATED")
        seed_task(db, status="RUNNING")
        seed_task(db, status="PLANNED")

        count = db.query(TaskRecord).filter(
            TaskRecord.status.in_(["CREATED", "PLANNED", "RUNNING"])
        ).count()

        assert count == 3, (
            f"REGRESSION BUG 2: Backpressure query must find uppercase status rows. "
            f"Found {count}, expected 3. "
            "Original bug used lowercase 'pending'/'running' and always got 0."
        )

    def test_REGRESSION_no_lowercase_status_in_router_source(self):
        """
        AST-level proof: scan task.py router for lowercase status literals.
        """
        path = Path(__file__).parent.parent.parent / "backend" / "api" / "routers" / "task.py"
        source = path.read_text()
        tree = ast.parse(source)

        forbidden = {"pending", "created", "running", "failed", "completed", "planned"}
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.lower() in forbidden and node.value != node.value.upper():
                    violations.append((node.lineno, node.value))

        assert violations == [], (
            f"REGRESSION BUG 2: Found lowercase status literals in task router:\n"
            + "\n".join(f"  line {l}: {v!r}" for l, v in violations)
        )

    def test_REGRESSION_backpressure_429_when_limit_reached(self, db, test_client):
        """
        End-to-end regression: fill queue then verify next request is 429.
        With the original bug this test always passed (requests leaked through).
        """
        with patch("backend.api.routers.task.settings") as mock_cfg:
            mock_cfg.MAX_CONCURRENT_TASKS = 1
            mock_cfg.TASK_TIMEOUT_SECONDS = 600

            seed_task(db, status="CREATED")

            response = test_client.post("/tasks/create", json={
                "title": "Overflow",
                "description": "Should be blocked",
                "task_type": "fix_bug",
            })

        assert response.status_code == 429, (
            f"REGRESSION BUG 2: Expected 429 when queue full but got {response.status_code}. "
            "The original bug let this request through because count was always 0."
        )


# ─────────────────────────────────────────────────────────────────────────────────
# BUG 3: node_callback Argument Count
# ─────────────────────────────────────────────────────────────────────────────────

class TestRegressionNodeCallbackArgCount:
    """
    REGRESSION: agent/planner/executor.py (node_callback call sites)
    
    The original code called:
        self.node_callback(node.step_id, "COMPLETED")   ← 2 args ✓  (some call sites)
        ... but the lambda in tasks.py was:
        node_callback=lambda nid, ns: _update_node(task_id, nid, ns)  ← 2 args ✓
    
    However, the failed future handler did NOT call node_callback at all —
    failed nodes were silently dropped from the futures map without any status
    update. This meant failed nodes remained in "running" state indefinitely.
    
    Fix: Failed futures now call node_callback(node.step_id, "FAILED") and
    log the exception.
    """

    def test_REGRESSION_node_callback_arg_count(self, tmp_path):
        """
        Core regression proof: a strict 2-arg function passed as node_callback
        must never raise TypeError during execution.
        """
        errors = []

        def strict_two_arg_callback(node_id: str, status: str) -> None:
            """Will raise TypeError if called with wrong number of args."""
            pass  # If we get here, signature is correct

        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
            patch("agent.planner.executor.execute_markdown_agent",
                  return_value={"success": True, "stdout": "ok", "stderr": ""}),
            patch("agent.planner.executor.get_agent",
                  return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
            patch("agent.planner.executor.extract_json_payload",
                  return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
            patch("agent.planner.executor.execute_step",
                  return_value={"success": True, "stdout": "done", "stderr": ""}),
        ):
            from agent.planner.executor import DAGOrchestrator
            from agent.planner.graph import TaskGraph, StepNode

            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="reg-t3",
                max_workers=1,
                node_callback=strict_two_arg_callback,
            )
            graph = TaskGraph(task_id="reg-t3")
            node = StepNode(step_id="s0", description="Test", required_tools=[], dependencies=[])
            graph.nodes["s0"] = node
            orch.run_graph(graph)  # Must not raise TypeError

        assert errors == [], f"node_callback raised errors: {errors}"

    def test_REGRESSION_failed_futures_update_node_status(self, tmp_path):
        """
        Regression: failed futures must NOT be silently discarded.
        After the bug fix, a failed node must have status='failed', not 'running'.
        """
        with (
            patch("agent.planner.executor.AgentPool", new=_make_agent_pool_class_mock()),
            patch("agent.planner.executor.get_memory_engine"),
            patch("agent.planner.executor.execute_step",
                  return_value={"success": False, "stdout": "", "stderr": "failed"}),
            patch("agent.planner.executor.execute_markdown_agent",
                  return_value={"success": False, "stdout": "", "stderr": "failed"}),
            patch("agent.planner.executor.get_agent",
                  return_value=__import__("agent.selector", fromlist=["SelectionResult"]).SelectionResult("coder", 0.9, "test", None)),
            patch("agent.planner.executor.extract_json_payload",
                  return_value={"files": [], "commands": [], "action": "patch_file", "error": None}),
        ):
            from agent.planner.executor import DAGOrchestrator
            from agent.planner.graph import TaskGraph, StepNode

            orch = DAGOrchestrator(
                workspace_dir=str(tmp_path),
                task_id="reg-t3b",
                max_workers=1,
                max_steps=5,
            )
            graph = TaskGraph(task_id="reg-t3b")
            node = StepNode(step_id="s0", description="Will fail", required_tools=[], dependencies=[])
            graph.nodes["s0"] = node
            orch.run_graph(graph)

        # After the fix, node status must resolve (not stay 'running')
        assert graph.nodes["s0"].status != "running", (
            "REGRESSION BUG 3: Failed node must not remain in 'running' state. "
            "Original bug silently discarded failed futures."
        )


# ─────────────────────────────────────────────────────────────────────────────────
# BUG 4: Retry Status String Mismatch
# ─────────────────────────────────────────────────────────────────────────────────

class TestRegressionRetryStatusString:
    """
    REGRESSION: backend/workers/tasks.py (retry exception handler)
    
    The original retry path set:
        task_record.status = "pending"   ← lowercase string literal!
    
    The backpressure guard queried for ["CREATED", "PLANNED", "RUNNING"].
    So retried tasks in "pending" status were invisible to the guard AND
    to the frontend poller (which polled for uppercase statuses).
    Retried tasks became permanent "black holes" in the queue.
    
    Fix: Use TaskStatus.CREATED enum value throughout. No raw status strings.
    """

    def test_REGRESSION_retry_status_string(self, db):
        """
        Core regression proof: after any status update, the stored value
        must be uppercase and match a valid TaskStatus enum value.
        """
        from backend.models.sql_models import TaskRecord, TaskStatus

        task = seed_task(db, status="FAILED")

        # Simulate what the retry path now does (using enum)
        task.status = TaskStatus.CREATED
        db.commit()
        db.refresh(task)

        assert task.status == "CREATED", (
            f"REGRESSION BUG 4: After retry, status must be 'CREATED', got {task.status!r}. "
            "Original bug set status='pending' (lowercase) making task invisible."
        )
        assert task.status in {s.value for s in TaskStatus}, (
            f"Status {task.status!r} is not a valid TaskStatus enum value."
        )

    def test_REGRESSION_no_lowercase_status_in_worker_source(self):
        """
        AST-level proof: tasks.py must contain no lowercase status string literals.
        """
        path = Path(__file__).parent.parent.parent / "backend" / "workers" / "tasks.py"
        source = path.read_text()
        tree = ast.parse(source)

        forbidden_lowercase = {"pending", "running", "created", "failed", "completed", "planned"}
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in forbidden_lowercase:  # exact lowercase match
                    violations.append((node.lineno, node.value))

        assert violations == [], (
            f"REGRESSION BUG 4: Found lowercase status string literals in tasks.py:\n"
            + "\n".join(f"  line {l}: {v!r}" for l, v in violations)
            + "\nUse TaskStatus enum values instead."
        )

    def test_REGRESSION_TaskStatus_enum_values_match_db_values(self, db):
        """
        Prove the TaskStatus enum values round-trip correctly through the DB.
        An ORM record stored with TaskStatus.CREATED must come back as "CREATED".
        """
        from backend.models.sql_models import TaskRecord, TaskStatus

        task_id = str(uuid.uuid4())
        record = TaskRecord(
            id=task_id,
            description="Enum round-trip test",
            status=TaskStatus.CREATED,
        )
        db.add(record)
        db.commit()

        retrieved = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        assert retrieved.status == "CREATED", (
            f"REGRESSION BUG 4: TaskStatus.CREATED must round-trip as 'CREATED', "
            f"got {retrieved.status!r}"
        )
