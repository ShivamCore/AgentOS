"""
agent/utils/batch_infer.py
============================
Parallel batch inference for independent DAG nodes.

Design:
  - Accepts a list of (prompt, system_prompt, model, temperature) tuples
  - Fires them concurrently using a ThreadPoolExecutor
  - Each call goes through the normal generate_text path (cache + metrics)
  - Returns results in the same order as inputs
  - Thread count bounded by BATCH_MAX_WORKERS env var (default 4)
  - Respects Ollama's single-instance constraint via the existing _generate_lock
    in llm.py — concurrent calls will queue at the lock, not deadlock

Usage:
    from agent.utils.batch_infer import batch_infer, BatchRequest

    requests = [
        BatchRequest(prompt="Write add.py", model="deepseek-coder:1.3b"),
        BatchRequest(prompt="Write sub.py", model="deepseek-coder:1.3b"),
    ]
    results = batch_infer(requests)
    # results[0].response, results[1].response
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_MAX_WORKERS = int(__import__("os").getenv("BATCH_MAX_WORKERS", "4"))


@dataclass
class BatchRequest:
    prompt: str
    system_prompt: str = ""
    model: str = ""          # empty → caller must fill from ModelRouter
    temperature: float = 0.2
    max_tokens: int = -1
    task_type: str = "code"  # used for cache TTL selection
    stream_callback: Optional[Callable[[str], None]] = None
    # Internal tracking
    _index: int = field(default=0, repr=False)


@dataclass
class BatchResult:
    index: int
    response: str
    model: str
    latency_ms: int
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


def batch_infer(
    requests: list[BatchRequest],
    max_workers: int = _MAX_WORKERS,
) -> list[BatchResult]:
    """
    Execute a list of BatchRequests in parallel.

    Returns results in the same order as the input list.
    Failed requests have result.error set and result.response == "".
    """
    if not requests:
        return []

    # Tag each request with its original index
    for i, req in enumerate(requests):
        req._index = i

    results: list[BatchResult | None] = [None] * len(requests)

    def _run_one(req: BatchRequest) -> BatchResult:
        from agent.llm import generate_text  # local import avoids circular dep
        t0 = time.time()
        try:
            response = generate_text(
                prompt=req.prompt,
                system_prompt=req.system_prompt,
                model=req.model,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                stream_callback=req.stream_callback,
            )
            return BatchResult(
                index=req._index,
                response=response,
                model=req.model,
                latency_ms=int((time.time() - t0) * 1000),
            )
        except Exception as exc:
            logger.error("[BatchInfer] request %d failed: %s", req._index, exc)
            return BatchResult(
                index=req._index,
                response="",
                model=req.model,
                latency_ms=int((time.time() - t0) * 1000),
                error=str(exc),
            )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_one, req): req for req in requests}
        for future in as_completed(futures):
            result = future.result()
            results[result.index] = result

    # Guarantee no None slots (shouldn't happen, but be safe)
    return [
        r if r is not None else BatchResult(
            index=i, response="", model="", latency_ms=0, error="missing result"
        )
        for i, r in enumerate(results)
    ]
