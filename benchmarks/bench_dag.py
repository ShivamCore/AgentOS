#!/usr/bin/env python3
"""
DAG scheduler benchmark (no LLM, no network).

Measures, for fixed synthetic workloads:
  * scheduling overhead   = wall time - ideal critical-path time
  * parallel speedup      = wall(max_workers=1) / wall(max_workers=N)
  * completion rate       = fraction of nodes COMPLETED
  * failure-handling cost = wall time of a graph where one root fails and is retried

Each step blocks for a fixed duration (``--step-ms``) to model an I/O-bound step
(e.g. waiting on an LLM or a container). This isolates the scheduler; it says
nothing about LLM throughput, which is serialized by default (OLLAMA_MAX_CONCURRENT=1).

    python benchmarks/bench_dag.py --reps 5 --out benchmarks/results/dag.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.planner.graph import StepNode, TaskGraph  # noqa: E402
from agent.planner.scheduler import DAGScheduler, SchedulerConfig, StepOutcome  # noqa: E402


def build(shape: str, n: int) -> tuple[TaskGraph, int]:
    """Return (graph, critical path length in steps)."""
    g = TaskGraph(task_id=shape)
    if shape == "wide":
        edges = {f"n{i}": [] for i in range(n)}
        depth = 1
    elif shape == "chain":
        edges = {f"n{i}": ([f"n{i-1}"] if i else []) for i in range(n)}
        depth = n
    elif shape == "layered":  # 4 layers, each node depends on every node of the previous layer
        width = max(1, n // 4)
        edges = {}
        for layer in range(4):
            for j in range(width):
                edges[f"l{layer}_{j}"] = [f"l{layer-1}_{k}" for k in range(width)] if layer else []
        depth = 4
    else:
        raise ValueError(shape)
    for nid, deps in edges.items():
        g.nodes[nid] = StepNode(step_id=nid, description=nid, dependencies=deps)
    return g, depth


def run_once(shape: str, n: int, workers: int, step_s: float, fail_root: bool = False) -> dict:
    g, depth = build(shape, n)
    first = next(iter(g.nodes))

    def step(node, ctx):
        time.sleep(step_s)
        if fail_root and node.step_id == first and ctx.attempt == 1:
            return StepOutcome(False, error="injected failure")
        return StepOutcome(True)

    sched = DAGScheduler(step, SchedulerConfig(max_workers=workers, max_retries=1, poll_interval_sec=0.005))
    t0 = time.perf_counter()
    rep = sched.run(g)
    wall = time.perf_counter() - t0
    import math

    waves = depth if shape != "wide" else 1
    per_wave = max(1, math.ceil(len(g.nodes) / waves / workers)) if shape != "chain" else 1
    ideal = waves * per_wave * step_s + (step_s if fail_root else 0.0)
    done = sum(1 for s in rep.node_states.values() if s == "COMPLETED")
    return {"wall_s": wall, "ideal_s": ideal, "overhead_s": wall - ideal, "completed": done,
            "nodes": len(g.nodes), "max_concurrency": rep.max_observed_concurrency, "state": rep.state.value}


def summarize(xs: list[float]) -> dict:
    xs = sorted(xs)
    p95 = xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]
    return {"median": statistics.median(xs), "min": xs[0], "max": xs[-1], "p95": p95,
            "stdev": statistics.stdev(xs) if len(xs) > 1 else 0.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--nodes", type=int, default=16)
    ap.add_argument("--step-ms", type=float, default=50.0)
    ap.add_argument("--workers", default="1,2,4,8")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    step_s = args.step_ms / 1000
    workers = [int(w) for w in args.workers.split(",")]

    rows = []
    for shape in ("wide", "chain", "layered"):
        base = None
        for w in workers:
            runs = [run_once(shape, args.nodes, w, step_s) for _ in range(args.reps)]
            walls = [r["wall_s"] for r in runs]
            med = statistics.median(walls)
            base = base or med
            rows.append({
                "workload": shape, "nodes": runs[0]["nodes"], "max_workers": w,
                "wall_s": summarize(walls), "ideal_s": runs[0]["ideal_s"],
                "overhead_s": summarize([r["overhead_s"] for r in runs]),
                "speedup_vs_1_worker": base / med,
                "completion_rate": statistics.mean(r["completed"] / r["nodes"] for r in runs),
                "max_observed_concurrency": max(r["max_concurrency"] for r in runs),
                "raw": runs,
            })
    fail_runs = [run_once("wide", args.nodes, 4, step_s, fail_root=True) for _ in range(args.reps)]
    ok_runs = [run_once("wide", args.nodes, 4, step_s) for _ in range(args.reps)]
    failure = {
        "workload": "wide, 1 root fails once then succeeds on retry", "max_workers": 4,
        "wall_s_with_retry": summarize([r["wall_s"] for r in fail_runs]),
        "wall_s_without_failure": summarize([r["wall_s"] for r in ok_runs]),
        "completion_rate": statistics.mean(r["completed"] / r["nodes"] for r in fail_runs),
        "raw": fail_runs,
    }
    result = {
        "benchmark": "dag_scheduler",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": sys.version.split()[0], "platform": platform.platform(),
                        "machine": platform.machine(), "cpu_count": os.cpu_count()},
        "config": {"reps": args.reps, "nodes": args.nodes, "step_ms": args.step_ms, "workers": workers},
        "results": rows, "failure_handling": failure,
    }
    print(f"{'workload':10} {'workers':>7} {'median s':>9} {'ideal s':>8} {'ovh ms':>7} {'speedup':>8} {'conc':>5}")
    for r in rows:
        print(f"{r['workload']:10} {r['max_workers']:>7} {r['wall_s']['median']:>9.3f} {r['ideal_s']:>8.3f} "
              f"{r['overhead_s']['median'] * 1000:>7.1f} {r['speedup_vs_1_worker']:>8.2f} {r['max_observed_concurrency']:>5}")
    print(f"failure+retry median {failure['wall_s_with_retry']['median']:.3f}s vs "
          f"{failure['wall_s_without_failure']['median']:.3f}s without; completion {failure['completion_rate']:.0%}")
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
        print(f"raw results: {args.out}")


if __name__ == "__main__":
    main()
