"""agent/utils/inference_cache.py: keying, TTLs, counters and Redis failure handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from agent.utils import inference_cache as ic


@pytest.fixture()
def fake_redis():
    r = fakeredis.FakeRedis()
    ic.clear_stats()
    with patch.object(ic, "_get_redis", return_value=r):
        yield r
    ic.clear_stats()


def test_key_depends_on_every_input():
    base = ic._make_key("m", "p", "s", 0.2)
    assert base.startswith("llm_cache:")
    assert base == ic._make_key("m", "p", "s", 0.2)
    others = {
        ic._make_key("m2", "p", "s", 0.2),
        ic._make_key("m", "p2", "s", 0.2),
        ic._make_key("m", "p", "s2", 0.2),
        ic._make_key("m", "p", "s", 0.3),
    }
    assert base not in others and len(others) == 4


def test_round_trip_ttl_and_stats(fake_redis):
    assert ic.get_cached("m", "prompt") is None
    ic.set_cached("m", "prompt", "a long enough response", task_type="debug")
    assert ic.get_cached("m", "prompt") == "a long enough response"
    key = ic._make_key("m", "prompt", "", 0.2)
    assert 0 < fake_redis.ttl(key) <= ic._TASK_TTL["debug"]
    assert ic.get_stats() == {"hits": 1, "misses": 1, "errors": 0, "hit_rate": 0.5}


def test_short_or_empty_responses_are_not_cached(fake_redis):
    ic.set_cached("m", "p", "")
    ic.set_cached("m", "p", "tiny")
    assert ic.get_cached("m", "p") is None


def test_unknown_task_type_uses_default_ttl(fake_redis):
    ic.set_cached("m", "p", "a long enough response", task_type="nonsense")
    assert (
        0 < fake_redis.ttl(ic._make_key("m", "p", "", 0.2)) <= ic._TASK_TTL["default"]
    )


def test_redis_unavailable_is_a_miss_not_an_error():
    ic.clear_stats()
    with patch.object(ic, "_get_redis", return_value=None):
        assert ic.get_cached("m", "p") is None
        ic.set_cached("m", "p", "a long enough response")
    assert ic.get_stats()["misses"] == 1 and ic.get_stats()["errors"] == 0


def test_redis_errors_are_counted_and_swallowed():
    ic.clear_stats()
    broken = MagicMock()
    broken.get.side_effect = ConnectionError("down")
    broken.setex.side_effect = ConnectionError("down")
    with patch.object(ic, "_get_redis", return_value=broken):
        assert ic.get_cached("m", "p") is None
        ic.set_cached("m", "p", "a long enough response")
    assert ic.get_stats()["errors"] == 2


def test_get_redis_returns_none_when_unreachable(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    assert ic._get_redis() is None
