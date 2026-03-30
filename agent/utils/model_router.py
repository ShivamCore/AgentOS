"""
agent/utils/model_router.py
============================
ModelRouter — task-aware LLM selection with quantization tier support.

Design:
  - Three tiers: accuracy (planning), speed (execution), balanced (debugging)
  - Each tier has an ordered preference list; first available model wins
  - TurboQuant = smallest quantized model in the speed tier (INT4/Q4_0)
  - Runtime discovery via Ollama /api/tags with a 5-minute TTL cache
  - Thread-safe; safe to call from Celery worker threads
  - Fully backward-compatible: select_model() still works as before

Quantization priority (best speed, acceptable quality):
  Q4_0  → smallest, fastest  (TurboQuant equivalent for GGUF)
  Q4_K_M → good balance
  Q5_K_M → higher quality
  F16    → full precision (avoid for execution tasks)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Tier definitions ──────────────────────────────────────────────────────────
# Each tier is an ordered list of preferred model names.
# The router picks the first name that is actually available in Ollama.
# Add your TurboQuant / custom GGUF model names here.

_TIER_ACCURACY = [          # planning — needs coherent multi-step reasoning
    "llama3.1:8b",
    "deepseek-coder:6.7b",
    "llama3.2:latest",
    "llama3.2:3b",
    "qwen2.5:3b",
]

_TIER_SPEED = [             # execution — needs fast JSON output, low latency
    "deepseek-coder:1.3b",  # Q4_0 — TurboQuant equivalent (smallest, fastest)
    "qwen2.5-coder:1.5b-base",
    "deepseek-coder:6.7b",
    "llama3.2:3b",
]

_TIER_BALANCED = [          # debugging — needs reasoning + speed
    "deepseek-coder:6.7b",
    "llama3.2:3b",
    "qwen2.5:3b",
    "deepseek-coder:1.3b",
]

# task_type → tier list
_TASK_TIER_MAP: dict[str, list[str]] = {
    "plan":    _TIER_ACCURACY,
    "code":    _TIER_SPEED,
    "debug":   _TIER_BALANCED,
    # fallback for unknown types
    "default": _TIER_BALANCED,
}

# Quantization quality scores (higher = better quality, lower = faster)
_QUANT_SCORE: dict[str, int] = {
    "F16":    100,
    "Q8_0":    80,
    "Q5_K_M":  60,
    "Q4_K_M":  50,
    "Q4_0":    40,   # TurboQuant tier
    "Q3_K_M":  30,
    "Q2_K":    20,
}


@dataclass
class ModelInfo:
    name: str
    family: str
    quantization: str
    size_gb: float
    quant_score: int = field(init=False)

    def __post_init__(self) -> None:
        self.quant_score = _QUANT_SCORE.get(self.quantization, 50)

    @property
    def is_turbo_quant(self) -> bool:
        """True if this model is in the fast/INT4 quantization tier."""
        return self.quant_score <= 40

    @property
    def is_embedding_only(self) -> bool:
        return self.family in ("nomic-bert", "bert")


@dataclass
class RoutingDecision:
    model: str
    tier: str
    reason: str
    fallback_used: bool = False


class ModelRouter:
    """
    Thread-safe model router.  Instantiate once per process (module-level singleton).

    Usage:
        router = ModelRouter()
        decision = router.route("plan")
        model_name = decision.model
    """

    _CACHE_TTL = 300  # seconds before re-querying Ollama for available models

    def __init__(self, ollama_tags_url: str | None = None) -> None:
        self._tags_url = ollama_tags_url or os.getenv(
            "OLLAMA_TAGS_URL", "http://localhost:11434/api/tags"
        )
        self._lock = threading.Lock()
        self._available: list[ModelInfo] = []
        self._available_names: set[str] = set()
        self._cache_ts: float = 0.0
        self._default_model = os.getenv("OLLAMA_MODEL", "deepseek-coder:6.7b")

    # ── Public API ────────────────────────────────────────────────────────────

    def route(
        self,
        task_type: str,
        user_override: str | None = None,
        attempt: int = 1,
    ) -> RoutingDecision:
        """
        Select the best available model for task_type.

        Args:
            task_type:      "plan" | "code" | "debug" | any string
            user_override:  explicit model name (bypasses routing)
            attempt:        retry attempt number (≥2 → downgrade to speed tier)

        Returns:
            RoutingDecision with .model, .tier, .reason, .fallback_used
        """
        # Hard override — user knows what they want
        if user_override and user_override.lower() not in ("auto", ""):
            return RoutingDecision(
                model=user_override,
                tier="override",
                reason=f"user override: {user_override}",
            )

        # On retry, always downgrade to the fastest available model
        effective_type = "code" if attempt >= 2 else task_type

        tier_list = _TASK_TIER_MAP.get(effective_type, _TASK_TIER_MAP["default"])
        available = self._get_available_names()

        # Walk the tier list and pick the first available model
        for candidate in tier_list:
            if candidate in available:
                tier_label = self._tier_label(effective_type)
                reason = (
                    f"{task_type} task → {tier_label} tier"
                    + (f" (retry {attempt}, downgraded)" if attempt >= 2 else "")
                )
                return RoutingDecision(
                    model=candidate,
                    tier=tier_label,
                    reason=reason,
                    fallback_used=(attempt >= 2),
                )

        # Nothing in the tier is available — fall back to default model
        logger.warning(
            "[ModelRouter] No preferred model available for task_type=%s; "
            "falling back to %s",
            task_type,
            self._default_model,
        )
        return RoutingDecision(
            model=self._default_model,
            tier="fallback",
            reason=f"no preferred model available for {task_type}",
            fallback_used=True,
        )

    def get_model_info(self, name: str) -> ModelInfo | None:
        """Return ModelInfo for a named model, or None if not available."""
        self._refresh_if_stale()
        for m in self._available:
            if m.name == name:
                return m
        return None

    def list_available(self) -> list[ModelInfo]:
        """Return all non-embedding models currently available in Ollama."""
        self._refresh_if_stale()
        return [m for m in self._available if not m.is_embedding_only]

    def turbo_model(self) -> str | None:
        """Return the fastest (lowest quant score) available model, or None."""
        models = self.list_available()
        if not models:
            return None
        return min(models, key=lambda m: (m.quant_score, m.size_gb)).name

    def accuracy_model(self) -> str | None:
        """Return the highest-quality available model, or None."""
        models = self.list_available()
        if not models:
            return None
        return max(models, key=lambda m: (m.quant_score, m.size_gb)).name

    def invalidate_cache(self) -> None:
        """Force a fresh model list on the next call."""
        with self._lock:
            self._cache_ts = 0.0

    @staticmethod
    def _tier_label(task_type: str) -> str:
        return {"plan": "accuracy", "code": "speed", "debug": "balanced"}.get(task_type, "balanced")

    def _get_available_names(self) -> set[str]:
        self._refresh_if_stale()
        return self._available_names

    def _refresh_if_stale(self) -> None:
        with self._lock:
            if time.time() - self._cache_ts < self._CACHE_TTL:
                return
            self._do_refresh()

    def _do_refresh(self) -> None:
        """Must be called with self._lock held."""
        try:
            resp = requests.get(self._tags_url, timeout=5)
            resp.raise_for_status()
            raw_models = resp.json().get("models", [])
            infos: list[ModelInfo] = []
            for m in raw_models:
                details = m.get("details", {})
                infos.append(ModelInfo(
                    name=m["name"],
                    family=details.get("family", "unknown"),
                    quantization=details.get("quantization_level", "unknown"),
                    size_gb=round(m.get("size", 0) / 1e9, 2),
                ))
            self._available = infos
            self._available_names = {m.name for m in infos}
            self._cache_ts = time.time()
            logger.debug("[ModelRouter] Refreshed: %d models", len(infos))
        except requests.exceptions.RequestException as exc:
            logger.warning("[ModelRouter] Could not reach Ollama: %s", exc)
            # Keep stale cache rather than clearing it


# ── Module-level singleton ────────────────────────────────────────────────────
_router = ModelRouter()


def select_model(
    task_type: str,
    agent_type: str = "",
    attempt: int = 1,
    user_override: str | None = None,
) -> str:
    """
    Backward-compatible function wrapper around ModelRouter.

    Returns the model name string (same contract as the old select_model).
    """
    decision = _router.route(task_type, user_override=user_override, attempt=attempt)
    logger.info(
        "[ModelRouter] %s → %s  (%s)",
        task_type,
        decision.model,
        decision.reason,
    )
    return decision.model


def get_router() -> ModelRouter:
    """Return the module-level ModelRouter singleton."""
    return _router
