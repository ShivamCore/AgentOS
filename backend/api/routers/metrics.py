"""
backend/api/routers/metrics.py
================================
GET /metrics/inference — live inference telemetry.

Returns per-model latency, tokens/sec, cache hit rate, and the last 10 calls.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["Metrics"])


@router.get("/metrics/inference", summary="Live LLM inference telemetry")
def inference_metrics() -> dict:
    """
    Returns aggregated inference metrics from the in-process ring buffer.

    Fields:
      total_calls       — total generate_text() calls since worker start
      cache_hits        — calls served from Redis cache
      cache_hit_rate    — fraction 0.0–1.0
      avg_latency_ms    — mean wall-clock time per call
      avg_tokens_per_sec — mean throughput (non-cached calls only)
      errors            — calls that raised an exception
      by_model          — per-model breakdown {calls, avg_latency_ms, errors}
      recent            — last 10 call records for live dashboard
    """
    from agent.llm import get_metrics_snapshot
    return get_metrics_snapshot()


@router.get("/metrics/cache", summary="Inference cache hit/miss counters")
def cache_metrics() -> dict:
    """Returns Redis cache hit/miss/error counters."""
    from agent.utils.inference_cache import get_stats
    return get_stats()


@router.get("/metrics/models", summary="Available models with quantization info")
def model_metrics() -> dict:
    """Returns all models available in Ollama with their quantization tier."""
    from agent.utils.model_router import get_router
    router_inst = get_router()
    models = router_inst.list_available()
    return {
        "turbo_model":    router_inst.turbo_model(),
        "accuracy_model": router_inst.accuracy_model(),
        "available": [
            {
                "name":         m.name,
                "family":       m.family,
                "quantization": m.quantization,
                "size_gb":      m.size_gb,
                "is_turbo":     m.is_turbo_quant,
                "quant_score":  m.quant_score,
            }
            for m in models
        ],
    }
