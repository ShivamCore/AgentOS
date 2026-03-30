# Ollama bindings, caching, and local metrics routing.
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

OLLAMA_URL        = os.getenv("OLLAMA_URL",        "http://localhost:11434/api/generate")
OLLAMA_TAGS_URL   = os.getenv("OLLAMA_TAGS_URL",   "http://localhost:11434/api/tags")
DEV_MODE_MODEL    = os.getenv("DEV_MODE_MODEL",    "deepseek-coder:1.3b")
PROD_MODE_MODEL   = os.getenv("PROD_MODE_MODEL",   "deepseek-coder:6.7b")
DEFAULT_MODEL     = os.getenv("OLLAMA_MODEL",      DEV_MODE_MODEL)
NUM_CTX           = int(os.getenv("OLLAMA_NUM_CTX",           "2048"))
MAX_INPUT_CHARS   = int(os.getenv("OLLAMA_MAX_INPUT_CHARS",   "7000"))
LOAD_TIMEOUT      = int(os.getenv("OLLAMA_LOAD_TIMEOUT",      "120"))
TOKEN_TIMEOUT     = int(os.getenv("OLLAMA_TOKEN_TIMEOUT",     "30"))
MAX_TOTAL_TIMEOUT = int(os.getenv("OLLAMA_MAX_TOTAL_TIMEOUT", "600"))
KEEP_ALIVE        = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
CACHE_ENABLED     = os.getenv("LLM_CACHE_ENABLED", "true").lower() == "true"

_loaded_models: dict[str, bool] = {}
_model_lock    = threading.Lock()
_generate_lock = threading.Lock()

_metrics_lock = threading.Lock()
_metrics: list[dict] = []
_METRICS_MAX  = 500


def _track_latency_and_tokens(model: str, task_type: str, prompt_chars: int,
                   response_chars: int, latency_ms: int, tokens_per_sec: float,
                   cache_hit: bool, error: Optional[str]) -> None:
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
        return {"total_calls": 0, "cache_hits": 0, "cache_hit_rate": 0.0,
                "avg_latency_ms": 0, "avg_tokens_per_sec": 0.0,
                "errors": 0, "by_model": {}, "recent": []}
    total   = len(snapshot)
    hits    = sum(1 for m in snapshot if m["cache_hit"])
    errors  = sum(1 for m in snapshot if m["error"])
    avg_lat = int(sum(m["latency_ms"] for m in snapshot) / total)
    live    = [m for m in snapshot if not m["cache_hit"] and not m["error"]]
    avg_tps = round(sum(m["tokens_per_sec"] for m in live) / len(live), 1) if live else 0.0
    by_model: dict[str, dict] = {}
    for m in snapshot:
        s = by_model.setdefault(m["model"], {"calls": 0, "total_ms": 0, "errors": 0})
        s["calls"] += 1
        s["total_ms"] += m["latency_ms"]
        if m["error"]:
            s["errors"] += 1
    for s in by_model.values():
        s["avg_latency_ms"] = int(s["total_ms"] / s["calls"])
        del s["total_ms"]
    return {"total_calls": total, "cache_hits": hits,
            "cache_hit_rate": round(hits / total, 3),
            "avg_latency_ms": avg_lat, "avg_tokens_per_sec": avg_tps,
            "errors": errors, "by_model": by_model, "recent": snapshot[-10:]}


def trim_prompt(prompt: str, max_chars: int = MAX_INPUT_CHARS) -> tuple[str, bool]:
    if len(prompt) <= max_chars:
        return prompt, False
    head    = int(max_chars * 0.60)
    tail    = max_chars - head
    trimmed = (prompt[:head]
               + "\n\n[... context trimmed for token budget ...]\n\n"
               + prompt[-tail:])
    logger.warning("[LLM] Prompt trimmed %d -> %d chars (budget %d)",
                   len(prompt), len(trimmed), max_chars)
    return trimmed, True


def check_ollama() -> bool:
    try:
        r = requests.get(OLLAMA_TAGS_URL, timeout=5)
        r.raise_for_status()
        return True
    except requests.exceptions.RequestException:
        logger.error("[LLM] Cannot reach Ollama. Is `ollama serve` running?")
        return False


def warmup_model(model: str = DEFAULT_MODEL) -> None:
    if not check_ollama():
        return
    with _model_lock:
        if model in _loaded_models:
            return
        logger.info("[LLM] Warming up model: %s ...", model)
        t0 = time.time()
        try:
            requests.post(
                OLLAMA_URL,
                json={"model": model, "prompt": "hi", "stream": False,
                      "keep_alive": KEEP_ALIVE,
                      "options": {"num_ctx": NUM_CTX, "temperature": 0}},
                timeout=None,
            )
            _loaded_models[model] = True
            logger.info("[LLM] %s ready in %.1fs", model, time.time() - t0)
        except requests.exceptions.RequestException as exc:
            logger.warning("[LLM] Warmup failed for %s: %s", model, exc)


def generate_text(
    prompt: str,
    system_prompt: str = "",
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    retries: int = 2,
    stream_callback: Optional[Callable[[str], None]] = None,
    max_tokens: int = -1,
    task_type: str = "code",
    use_cache: Optional[bool] = None,
) -> str:
    if model in ("Auto", "auto", ""):
        from agent.utils.model_router import select_model
        model = select_model(task_type=task_type)

    prompt, _ = trim_prompt(prompt)

    should_cache = (use_cache if use_cache is not None else CACHE_ENABLED)
    if should_cache and stream_callback is None:
        from agent.utils.inference_cache import get_cached, set_cached
        cached = get_cached(model, prompt, system_prompt, temperature)
        if cached is not None:
            logger.debug("[LLM] Cache hit for model=%s", model)
            _track_latency_and_tokens(model, task_type, len(prompt), len(cached), 0, 0.0, True, None)
            return cached

    if model not in _loaded_models:
        warmup_model(model)

    payload: dict = {
        "model": model, "prompt": prompt, "system": system_prompt,
        "stream": stream_callback is not None,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": temperature, "num_ctx": NUM_CTX},
    }
    if max_tokens > 0:
        payload["options"]["num_predict"] = max_tokens

    t0 = time.time()
    last_exc: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            with _generate_lock:
                full_response = ""
                resp = requests.post(
                    OLLAMA_URL, json=payload,
                    stream=stream_callback is not None,
                    timeout=MAX_TOTAL_TIMEOUT,
                )
                resp.raise_for_status()
                if stream_callback:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        try:
                            chunk_data: dict = json.loads(line)
                            chunk: str = chunk_data.get("response", "")
                            if chunk:
                                full_response += chunk
                                stream_callback(chunk)
                            if chunk_data.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
                else:
                    full_response = resp.json().get("response", "")

            elapsed = time.time() - t0
            tps = (len(full_response) / 4) / elapsed if elapsed > 0 else 0.0
            logger.info("[LLM] %s -> %d chars in %.1fs (%.0f tok/s)",
                        model, len(full_response), elapsed, tps)
            _track_latency_and_tokens(model, task_type, len(prompt), len(full_response),
                           int(elapsed * 1000), tps, False, None)
            if should_cache and stream_callback is None:
                from agent.utils.inference_cache import set_cached
                set_cached(model, prompt, full_response, system_prompt, temperature, task_type)
            return full_response

        except requests.exceptions.Timeout as exc:
            last_exc = exc
            logger.warning("[LLM] Timeout on attempt %d/%d", attempt + 1, retries + 1)
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue

        except requests.exceptions.RequestException as exc:
            last_exc = exc
            logger.error("[LLM] Request error: %s", exc)
            if attempt < retries:
                time.sleep(2)
                continue

    if model == PROD_MODE_MODEL and model != DEV_MODE_MODEL:
        logger.warning("[LLM] %s failed -- downgrading to %s", model, DEV_MODE_MODEL)
        _track_latency_and_tokens(model, task_type, len(prompt), 0,
                       int((time.time() - t0) * 1000), 0.0, False, str(last_exc))
        return generate_text(prompt, system_prompt, DEV_MODE_MODEL,
                             temperature, retries=1,
                             stream_callback=stream_callback, task_type=task_type)

    _track_latency_and_tokens(model, task_type, len(prompt), 0,
                   int((time.time() - t0) * 1000), 0.0, False, str(last_exc))
    raise RuntimeError(
        f"[LLM] Model {model} unavailable after {retries + 1} attempts. "
        f"Last error: {last_exc}"
    )


def extract_json_safely(text: str) -> dict | list | None:
    if not text:
        return None
        
    try:
        return json.loads(text)
    except Exception:
        pass
        
    try:
        # Standard markdown block parsing
        match = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1).strip())
    except Exception:
        pass
        
    try:
        # Array bracket sniffing
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            return json.loads(match.group(0).strip())
    except Exception:
        pass
        
    try:
        # Object brace sniffing
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0).strip())
    except Exception:
        pass
        
    return None
