"""
Optional live-inference tests. Skipped unless AGENTOS_TEST_OLLAMA_MODEL names a
model installed in a reachable Ollama, e.g.:

    AGENTOS_TEST_OLLAMA_MODEL=phi3.5 pytest -m ollama -q

These verify the real client and that the real planner output can pass (or is
cleanly rejected by) plan validation. They do not assert plan quality.
"""

from __future__ import annotations

import os

import pytest

MODEL = os.getenv("AGENTOS_TEST_OLLAMA_MODEL")


def _ready() -> bool:
    if not MODEL:
        return False
    from agent.llm import ollama_health

    return ollama_health(MODEL)["model_present"]


pytestmark = [
    pytest.mark.ollama,
    pytest.mark.skipif(
        not _ready(),
        reason="set AGENTOS_TEST_OLLAMA_MODEL to an installed Ollama model",
    ),
]


def test_generate_and_stream():
    from agent.llm import generate_text, get_metrics_snapshot

    text = generate_text(
        "Reply with the single word: pong",
        model=MODEL,
        temperature=0,
        max_tokens=8,
        use_cache=False,
    )
    assert text.strip()
    chunks: list[str] = []
    streamed = generate_text(
        "Count: 1 2 3",
        model=MODEL,
        temperature=0,
        max_tokens=8,
        stream_callback=chunks.append,
        use_cache=False,
    )
    assert "".join(chunks) == streamed
    assert get_metrics_snapshot()["recent"][-1]["tokens_per_sec"] > 0


def test_missing_model_is_reported():
    from agent.llm import ModelNotFoundError, generate_text

    with pytest.raises(ModelNotFoundError):
        generate_text(
            "hi",
            model="agentos-definitely-not-installed:0b",
            retries=0,
            use_cache=False,
        )


def test_real_planner_output_is_validated(monkeypatch):
    from agent.planner.graph import PlanValidationError
    from agent.selector import PlannerError, plan_markdown_task

    monkeypatch.setenv("OLLAMA_MODEL", MODEL)
    try:
        graph = plan_markdown_task("Write hello.py that prints 'hello'.", model=MODEL)
    except PlannerError as exc:
        pytest.xfail(f"model produced no valid plan (rejected cleanly): {exc}")
    except PlanValidationError:
        pytest.fail("PlanValidationError must be wrapped in PlannerError")
    assert graph.nodes and graph.topological_order()
