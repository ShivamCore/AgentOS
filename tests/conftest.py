"""
AgentOS — Master Test Fixtures (conftest.py)
=============================================
Shared fixtures for all test phases.
All external services are mocked — no live LLM, Redis, filesystem, or HTTP calls.
"""

from __future__ import annotations

import uuid
from typing import Generator
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# ── DB Setup ─────────────────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite://"  # fully in-memory, no file created
test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def create_test_tables() -> Generator:
    """Create all SQLAlchemy tables once per test session."""
    from backend.db.database import Base
    import backend.models.sql_models  # noqa: F401 — registers models onto Base

    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    """
    Provide a transactional DB session that fully rolls back after each test.
    Guarantees complete test isolation — no shared state between tests.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# Alias used by some test modules
db_session = db


# ── Redis Mock ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_redis() -> Generator:
    """
    Provide a fakeredis server instance, flushed between tests.
    Patches the redis.from_url factory to return this fake.
    """
    server = fakeredis.FakeServer()
    fake = fakeredis.FakeRedis(server=server, decode_responses=True)
    with patch("redis.from_url", return_value=fake):
        yield fake
    fake.flushall()


# ── LLM Mock ─────────────────────────────────────────────────────────────────────

class _MockLLM:
    """Configurable fake for generate_text. Tracks calls for assertion."""

    def __init__(self) -> None:
        self.call_count = 0
        self.last_prompt: str = ""
        self.last_system: str = ""
        self._response = '{"steps": [{"step_id": "s1", "description": "Write hello.py", "required_tools": ["write_file"], "dependencies": []}]}'

    def configure(self, response: str) -> None:
        self._response = response

    def __call__(self, prompt: str, system_prompt: str = "", **kwargs: object) -> str:
        self.call_count += 1
        self.last_prompt = prompt
        self.last_system = system_prompt
        return self._response


@pytest.fixture()
def mock_llm() -> Generator[_MockLLM, None, None]:
    """
    Replace all generate_text calls with a controllable fake.
    Also mocks check_ollama and warmup_model to be no-ops.
    """
    fake = _MockLLM()
    with (
        patch("agent.llm.generate_text", side_effect=fake),
        patch("agent.llm.check_ollama", return_value=True),
        patch("agent.llm.warmup_model", return_value=None),
    ):
        yield fake


# ── Celery Mock ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_celery() -> Generator:
    """
    Prevent Celery from dispatching to a broker.
    Captures task calls without executing them.
    """
    mock_task = MagicMock()
    mock_task.apply_async = MagicMock(return_value=MagicMock(id=str(uuid.uuid4())))
    with patch("backend.workers.tasks.run_agent_task", mock_task):
        yield mock_task


# ── FastAPI TestClient ────────────────────────────────────────────────────────────

@pytest.fixture()
def test_client(db: Session, mock_celery: MagicMock, mock_redis: fakeredis.FakeRedis) -> Generator:
    """
    FastAPI TestClient with all external dependencies replaced:
    - DB: in-memory SQLite (transactional rollback)
    - Celery: no-op dispatch
    - Redis: fakeredis
    """
    from backend.api.main import app
    from backend.db.database import get_db
    from backend.api.routers.task import _limiter

    def _override_db() -> Generator[Session, None, None]:
        yield db

    # Point the rate limiter at fakeredis so tests don't share real Redis counters
    original_redis = _limiter._redis
    _limiter._redis = mock_redis

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    app.dependency_overrides.clear()
    _limiter._redis = original_redis


# ── Agent Manifest Fixtures ───────────────────────────────────────────────────────

_VALID_PLANNER_MD = """\
# Agent: planner

## Role
Breaks down user goals into atomic DAG steps.

## Model
Auto

## Tools
- write_file
- run_command

## System Prompt
You are a planning agent. Output JSON DAG only.

## Constraints
- Max 10 steps per plan
"""

_VALID_CODER_MD = """\
# Agent: coder

## Role
Writes clean, production-ready Python code.

## Model
Auto

## Tools
- write_file
- run_command

## System Prompt
You are a senior software engineer.
"""

_VALID_DEBUGGER_MD = """\
# Agent: debugger

## Role
Diagnoses and fixes failing tests and runtime errors.

## Model
Auto

## Tools
- run_command

## System Prompt
You are an expert debugger. Output only the fix.
"""

_BROKEN_MD = """\
# Agent: broken

## Role
http://evil.com/exfiltrate  
This agent has os.system() calls embedded.
"""


@pytest.fixture()
def agent_manifests(tmp_path):
    """Write valid agent .md files to a temp directory for loader tests."""
    (tmp_path / "planner.md").write_text(_VALID_PLANNER_MD)
    (tmp_path / "coder.md").write_text(_VALID_CODER_MD)
    (tmp_path / "debugger.md").write_text(_VALID_DEBUGGER_MD)
    return tmp_path


@pytest.fixture()
def broken_agent_manifest(tmp_path):
    """Write a malformed .md file for rollback and security tests."""
    (tmp_path / "broken.md").write_text(_BROKEN_MD)
    return tmp_path


# ── Parametrized Status Fixture ───────────────────────────────────────────────────

@pytest.fixture(params=["CREATED", "RUNNING", "COMPLETED", "FAILED", "PLANNED", "PARTIAL_SUCCESS"])
def any_task_status(request):
    """Parametrized fixture yielding every valid TaskStatus string."""
    return request.param


# ── Task Record Seed Helper ───────────────────────────────────────────────────────

def seed_task(db: Session, status: str = "CREATED", task_id: str | None = None) -> object:
    """Helper to insert a TaskRecord into the test DB."""
    from backend.models.sql_models import TaskRecord

    record = TaskRecord(
        id=task_id or str(uuid.uuid4()),
        description="Test task",
        status=status,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
