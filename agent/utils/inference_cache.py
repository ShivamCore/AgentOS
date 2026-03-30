"""
agent/utils/inference_cache.py
================================
Redis-backed prompt response cache with SHA-256 keying.

Design:
  - Key = sha256(model + prompt + system_prompt + temperature)
  - TTL configurable per task type (planning results cached longer)
  - Thread-safe; safe to call from Celery workers
  - Graceful no-op when Redis is unavailable
  - Cache hit/miss counters exposed for /metrics/inference

Cache TTLs by task type:
  plan   → 3600 s  (plans are deterministic for the same input)
  code   → 300 s   (code may vary; short TTL avoids stale patches)
  debug  → 120 s   (errors change frequently)
  default → 300 s
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_TASK_TTL: dict[str, int] = {
    "plan":    3600,
    "code":    300,
    "debug":   120,
    "default": 300,
}

# Module-level counters (reset on worker restart)
_hits = 0
_misses = 0
_errors = 0
_counter_lock = threading.Lock()


def _make_key(model: str, prompt: str, system_prompt: str, temperature: float) -> str:
    raw = json.dumps(
        {"m": model, "p": prompt, "s": system_prompt, "t": round(temperature, 3)},
        sort_keys=True,
    )
    return "llm_cache:" + hashlib.sha256(raw.encode()).hexdigest()


def _get_redis():
    """Lazy Redis client — returns None if unavailable."""
    try:
        import redis as redis_pkg
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        client = redis_pkg.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        return client
    except Exception:
        return None


def get_cached(
    model: str,
    prompt: str,
    system_prompt: str = "",
    temperature: float = 0.2,
) -> str | None:
    """
    Return cached response string, or None on miss/error.
    """
    global _hits, _misses, _errors
    key = _make_key(model, prompt, system_prompt, temperature)
    try:
        r = _get_redis()
        if r is None:
            with _counter_lock:
                _misses += 1
            return None
        value = r.get(key)
        with _counter_lock:
            if value is not None:
                _hits += 1
            else:
                _misses += 1
        return value.decode() if isinstance(value, bytes) else value
    except Exception as exc:
        logger.debug("[Cache] get error: %s", exc)
        with _counter_lock:
            _errors += 1
        return None


def set_cached(
    model: str,
    prompt: str,
    response: str,
    system_prompt: str = "",
    temperature: float = 0.2,
    task_type: str = "default",
) -> None:
    """
    Store response in cache with appropriate TTL.  Fire-and-forget.
    """
    global _errors
    # Don't cache empty or very short responses (likely errors)
    if not response or len(response) < 10:
        return
    key = _make_key(model, prompt, system_prompt, temperature)
    ttl = _TASK_TTL.get(task_type, _TASK_TTL["default"])
    try:
        r = _get_redis()
        if r is None:
            return
        r.setex(key, ttl, response)
    except Exception as exc:
        logger.debug("[Cache] set error: %s", exc)
        with _counter_lock:
            _errors += 1


def get_stats() -> dict:
    """Return cache hit/miss/error counters."""
    with _counter_lock:
        total = _hits + _misses
        return {
            "hits": _hits,
            "misses": _misses,
            "errors": _errors,
            "hit_rate": round(_hits / total, 3) if total > 0 else 0.0,
        }


def clear_stats() -> None:
    global _hits, _misses, _errors
    with _counter_lock:
        _hits = _misses = _errors = 0
