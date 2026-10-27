"""
Ollama client.

* Endpoint and model come from configuration (``OLLAMA_HOST`` or the legacy
  ``OLLAMA_URL``/``OLLAMA_TAGS_URL``; ``OLLAMA_MODEL``). The default model is not
  assumed to be installed: a missing model raises ``ModelNotFoundError``
  immediately (no retry, no silent substitution of another model).
* Connection errors, timeouts and 5xx responses are retried a bounded number of
  times with exponential backoff; 4xx responses are not retried.
* Streaming requests use a per-chunk read timeout, so a stalled generation fails
  instead of hanging; non-streaming requests have a total read timeout.
* Concurrency: ``OLLAMA_MAX_CONCURRENT`` (default 1) bounds in-flight requests
  from this process. With the default, LLM-bound steps of a DAG are serialized
  even though the scheduler runs them on separate threads.
* Throughput metrics use Ollama's own ``eval_count``/``eval_duration`` counters.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from typing import Any

import requests

logger = logging.getLogger(__name__)

_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
if not _HOST.startswith("http"):
    _HOST = "http://" + _HOST
OLLAMA_URL = os.getenv("OLLAMA_URL", f"{_HOST}/api/generate")
OLLAMA_TAGS_URL = os.getenv("OLLAMA_TAGS_URL", f"{_HOST}/api/tags")
DEV_MODE_MODEL = os.getenv("DEV_MODE_MODEL", "deepseek-coder:1.3b")
PROD_MODE_MODEL = os.getenv("PROD_MODE_MODEL", "deepseek-coder:6.7b")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", DEV_MODE_MODEL)
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "2048"))
MAX_INPUT_CHARS = int(os.getenv("OLLAMA_MAX_INPUT_CHARS", "7000"))
CONNECT_TIMEOUT = float(os.getenv("OLLAMA_CONNECT_TIMEOUT", "5"))
LOAD_TIMEOUT = int(os.getenv("OLLAMA_LOAD_TIMEOUT", "120"))
TOKEN_TIMEOUT = int(os.getenv("OLLAMA_TOKEN_TIMEOUT", "30"))
MAX_TOTAL_TIMEOUT = int(os.getenv("OLLAMA_MAX_TOTAL_TIMEOUT", "600"))
KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
CACHE_ENABLED = os.getenv("LLM_CACHE_ENABLED", "false").lower() == "true"
MAX_CONCURRENT = max(1, int(os.getenv("OLLAMA_MAX_CONCURRENT", "1")))


class LLMError(RuntimeError):
    """Base class for inference failures."""


class LLMUnavailableError(LLMError):
    """Ollama could not be reached or kept failing (connection / timeout / 5xx)."""


class ModelNotFoundError(LLMError):
    """The configured model is not installed in Ollama."""


class LLMResponseError(LLMError):
    """Ollama answered but the response was malformed, empty or a client error."""


_loaded_models: dict[str, bool] = {}
_model_lock = threading.Lock()
_generate_slots = threading.BoundedSemaphore(MAX_CONCURRENT)

_metrics_lock = threading.Lock()
_metrics: list[dict] = []
_METRICS_MAX = 500


def _track_latency_and_tokens(model: str, task_type: str, prompt_chars: int, response_chars: int,
                              latency_ms: int, tokens_per_sec: float, cache_hit: bool,
                              error: str | None) -> None:
    entry = {
        "ts": time.time(), "model": model, "task_type": task_type,
        "prompt_chars": prompt_chars, "response_chars": response_chars,
        "latency_ms": latency_ms, "tokens_per_sec": round(tokens_per_sec, 1),
        "cache_hit": cache_hit, "error": error,
    }
    with _metrics_lock:
        _metrics.append(entry)
        if len(_metrics) > _METRICS_MAX:
            _metrics.pop(0)


def get_metrics_snapshot() -> dict:
    with _metrics_lock:
        snapshot = list(_metrics)
    if not snapshot:
        return {"total_calls": 0, "cache_hits": 0, "cache_hit_rate": 0.0, "avg_latency_ms": 0,
                "avg_tokens_per_sec": 0.0, "errors": 0, "by_model": {}, "recent": []}
    total = len(snapshot)
    hits = sum(1 for m in snapshot if m["cache_hit"])
    errors = sum(1 for m in snapshot if m["error"])
    live = [m for m in snapshot if not m["cache_hit"] and not m["error"] and m["tokens_per_sec"] > 0]
    by_model: dict[str, dict] = {}
    for m in snapshot:
        s = by_model.setdefault(m["model"], {"calls": 0, "total_ms": 0, "errors": 0})
        s["calls"] += 1
        s["total_ms"] += m["latency_ms"]
        s["errors"] += 1 if m["error"] else 0
    for s in by_model.values():
        s["avg_latency_ms"] = int(s.pop("total_ms") / s["calls"])
    return {
        "total_calls": total, "cache_hits": hits, "cache_hit_rate": round(hits / total, 3),
        "avg_latency_ms": int(sum(m["latency_ms"] for m in snapshot) / total),
        "avg_tokens_per_sec": round(sum(m["tokens_per_sec"] for m in live) / len(live), 1) if live else 0.0,
        "errors": errors, "by_model": by_model, "recent": snapshot[-10:],
    }


def trim_prompt(prompt: str, max_chars: int = MAX_INPUT_CHARS) -> tuple[str, bool]:
    if len(prompt) <= max_chars:
        return prompt, False
    head = int(max_chars * 0.60)
    tail = max_chars - head
    trimmed = prompt[:head] + "\n\n[... context trimmed for token budget ...]\n\n" + prompt[-tail:]
    logger.warning("[LLM] Prompt trimmed %d -> %d chars (budget %d)", len(prompt), len(trimmed), max_chars)
    return trimmed, True


def list_models(timeout: float = 5.0) -> list[str]:
    r = requests.get(OLLAMA_TAGS_URL, timeout=timeout)
    r.raise_for_status()
    return [m.get("name", "") for m in r.json().get("models", [])]


def check_ollama() -> bool:
    try:
        list_models()
        return True
    except (requests.exceptions.RequestException, ValueError):
        logger.error("[LLM] Cannot reach Ollama at %s. Is `ollama serve` running?", OLLAMA_TAGS_URL)
        return False


def ollama_health(model: str | None = None) -> dict[str, Any]:
    """Diagnostic: is Ollama reachable and is ``model`` installed?"""
    model = model or DEFAULT_MODEL
    try:
        models = list_models()
    except (requests.exceptions.RequestException, ValueError) as exc:
        return {"reachable": False, "url": OLLAMA_TAGS_URL, "model": model, "model_present": False,
                "models": [], "detail": f"cannot reach Ollama: {exc}"}
    present = model in models or f"{model}:latest" in models
    detail = "ok" if present else f"model '{model}' is not installed; run `ollama pull {model}`"
    return {"reachable": True, "url": OLLAMA_TAGS_URL, "model": model, "model_present": present,
            "models": models, "detail": detail}


def warmup_model(model: str = DEFAULT_MODEL) -> None:
    """Load ``model`` into memory. Network failures are logged, not raised."""
    with _model_lock:
        if model in _loaded_models:
            return
        logger.info("[LLM] Warming up model: %s ...", model)
        t0 = time.time()
        try:
            r = requests.post(
                OLLAMA_URL,
                json={"model": model, "prompt": "hi", "stream": False, "keep_alive": KEEP_ALIVE,
                      "options": {"num_ctx": NUM_CTX, "temperature": 0, "num_predict": 1}},
                timeout=(CONNECT_TIMEOUT, LOAD_TIMEOUT),
            )
            r.raise_for_status()
            _loaded_models[model] = True
            logger.info("[LLM] %s ready in %.1fs", model, time.time() - t0)
        except requests.exceptions.RequestException as exc:
            logger.warning("[LLM] Warmup failed for %s: %s", model, exc)


def _raise_for_response(resp: requests.Response, model: str) -> None:
    if resp.status_code < 400:
        return
    try:
        body = resp.json().get("error", "")
    except ValueError:
        body = resp.text[:300]
    if resp.status_code == 404 or "not found" in str(body).lower():
        raise ModelNotFoundError(f"model '{model}' is not available in Ollama ({body}); run `ollama pull {model}`")
    if resp.status_code >= 500 and any(k in str(body).lower() for k in ("prediction aborted", "repeat limit")):
        # The server is fine; this generation degenerated. Retrying the identical request will not help.
        raise LLMResponseError(f"generation aborted by Ollama: {body}")
    if resp.status_code >= 500:
        raise requests.exceptions.HTTPError(f"Ollama {resp.status_code}: {body}", response=resp)
    raise LLMResponseError(f"Ollama rejected the request ({resp.status_code}): {body}")


def _do_request(payload: dict, stream_callback: Callable[[str], None] | None) -> tuple[str, dict]:
    stream = stream_callback is not None
    with _generate_slots:
        resp = requests.post(
            OLLAMA_URL, json=payload, stream=stream,
            timeout=(CONNECT_TIMEOUT, TOKEN_TIMEOUT if stream else MAX_TOTAL_TIMEOUT),
        )
        try:
            _raise_for_response(resp, payload["model"])
            if not stream:
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise LLMResponseError(f"Ollama returned non-JSON body: {exc}") from exc
                if data.get("error"):
                    raise LLMResponseError(f"Ollama error: {data['error']}")
                return data.get("response", ""), data
            parts: list[str] = []
            final: dict = {}
            deadline = time.monotonic() + MAX_TOTAL_TIMEOUT
            for line in resp.iter_lines():
                if time.monotonic() > deadline:
                    raise requests.exceptions.Timeout(f"generation exceeded {MAX_TOTAL_TIMEOUT}s")
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise LLMResponseError(f"Ollama error: {chunk['error']}")
                piece = chunk.get("response", "")
                if piece:
                    parts.append(piece)
                    stream_callback(piece)  # type: ignore[misc]
                if chunk.get("done"):
                    final = chunk
                    break
            if not final:
                raise LLMResponseError("stream ended before Ollama reported completion (incomplete output)")
            return "".join(parts), final
        finally:
            resp.close()


def generate_text(
    prompt: str,
    system_prompt: str = "",
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    retries: int = 2,
    stream_callback: Callable[[str], None] | None = None,
    max_tokens: int = -1,
    task_type: str = "code",
    use_cache: bool | None = None,
    json_mode: bool = False,
) -> str:
    """Generate text. Raises ``LLMError`` subclasses on failure; never returns a fabricated response."""
    if model in ("Auto", "auto", ""):
        from agent.utils.model_router import select_model

        model = select_model(task_type=task_type)
    if not 0.0 <= float(temperature) <= 2.0:
        raise ValueError(f"temperature must be within [0, 2], got {temperature}")
    if retries < 0 or retries > 5:
        raise ValueError("retries must be within [0, 5]")

    prompt, _ = trim_prompt(prompt)
    should_cache = (use_cache if use_cache is not None else CACHE_ENABLED) and stream_callback is None
    if should_cache:
        from agent.utils.inference_cache import get_cached

        cached = get_cached(model, prompt, system_prompt, temperature)
        if cached is not None:
            _track_latency_and_tokens(model, task_type, len(prompt), len(cached), 0, 0.0, True, None)
            return cached

    payload: dict[str, Any] = {
        "model": model, "prompt": prompt, "system": system_prompt,
        "stream": stream_callback is not None, "keep_alive": KEEP_ALIVE,
        "options": {"temperature": temperature, "num_ctx": NUM_CTX},
    }
    if max_tokens > 0:
        payload["options"]["num_predict"] = max_tokens
    if json_mode:
        payload["format"] = "json"  # Ollama constrains decoding to syntactically valid JSON

    t0 = time.time()
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            text, meta = _do_request(payload, stream_callback)
            if not text.strip():
                raise LLMResponseError("model returned an empty response")
            elapsed = time.time() - t0
            ev_count, ev_dur = meta.get("eval_count") or 0, meta.get("eval_duration") or 0
            tps = ev_count / (ev_dur / 1e9) if ev_count and ev_dur else 0.0
            logger.info("[LLM] %s -> %d chars in %.1fs (%.1f tok/s)", model, len(text), elapsed, tps)
            _track_latency_and_tokens(model, task_type, len(prompt), len(text), int(elapsed * 1000), tps, False, None)
            if should_cache:
                from agent.utils.inference_cache import set_cached

                set_cached(model, prompt, text, system_prompt, temperature, task_type)
            return text
        except (ModelNotFoundError, LLMResponseError) as exc:
            # Client-side / model problems: retrying will not help (except an empty reply, retried once).
            last_exc = exc
            if isinstance(exc, LLMResponseError) and "empty response" in str(exc) and attempt < retries:
                continue
            _track_latency_and_tokens(model, task_type, len(prompt), 0, int((time.time() - t0) * 1000),
                                      0.0, False, str(exc))
            raise
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            logger.warning("[LLM] attempt %d/%d failed: %s", attempt + 1, retries + 1, exc)
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))

    _track_latency_and_tokens(model, task_type, len(prompt), 0, int((time.time() - t0) * 1000), 0.0, False,
                              str(last_exc))
    raise LLMUnavailableError(f"Ollama request for model '{model}' failed after {retries + 1} attempt(s): {last_exc}")


def _first_json_value(text: str, opener: str) -> Any:
    decoder = json.JSONDecoder()
    idx = text.find(opener)
    while idx != -1:
        try:
            value, _ = decoder.raw_decode(text, idx)
            return value
        except json.JSONDecodeError:
            idx = text.find(opener, idx + 1)
    return None


def extract_json_safely(text: str) -> dict | list | None:
    """Best-effort extraction of the first JSON object (preferred) or array from model text."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    obj = _first_json_value(text, "{")
    if obj is not None:
        return obj
    return _first_json_value(text, "[")
