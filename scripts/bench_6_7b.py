"""
scripts/bench_6_7b.py
======================
Live timing benchmark for deepseek-coder:6.7b on complex code generation tasks.
Run: venv/bin/python scripts/bench_6_7b.py
"""
import sys
import time

sys.path.insert(0, ".")

from agent.llm import generate_text, get_metrics_snapshot

MODEL = "deepseek-coder:6.7b"

PROMPTS = [
    (
        "binary_search_tree",
        (
            "Write a complete Python implementation of a binary search tree with "
            "insert, delete, search, and inorder traversal methods. "
            "Include type hints and docstrings."
        ),
        "You are a senior Python engineer. Output only clean, working Python code.",
    ),
    (
        "async_http_client",
        (
            "Write a complete async Python HTTP client class using aiohttp that supports: "
            "connection pooling, automatic retry with exponential backoff, request timeout, "
            "response caching with TTL, and structured error handling. Include full type hints."
        ),
        "You are a senior Python engineer. Output only clean, working Python code.",
    ),
    (
        "dag_scheduler",
        (
            "Write a Python DAG task scheduler that: parses a list of tasks with dependencies, "
            "detects cycles using DFS, returns a valid topological execution order, and supports "
            "parallel execution groups. Include full implementation with type hints."
        ),
        "You are a senior Python engineer. Output only clean, working Python code.",
    ),
]


def run_bench():
    print(f"Model: {MODEL}")
    print("=" * 64)

    results = []

    for name, prompt, system in PROMPTS:
        print(f"\nTEST: {name}")
        print(f"  Prompt: {len(prompt)} chars")
        t0 = time.time()
        try:
            response = generate_text(
                prompt=prompt,
                system_prompt=system,
                model=MODEL,
                temperature=0.1,
                task_type="code",
                use_cache=False,   # force live inference every time
            )
            elapsed = time.time() - t0
            chars = len(response)
            tokens_est = chars // 4
            tps = tokens_est / elapsed if elapsed > 0 else 0.0
            results.append(
                {"name": name, "elapsed": elapsed, "chars": chars, "tps": tps, "ok": True}
            )
            preview = response[:120].replace("\n", " ").strip()
            print(f"  Time:      {elapsed:.2f}s")
            print(f"  Output:    {chars} chars  (~{tokens_est} tokens)")
            print(f"  Tok/sec:   {tps:.1f}")
            print(f"  Preview:   {preview!r}")
        except Exception as exc:
            elapsed = time.time() - t0
            results.append(
                {"name": name, "elapsed": elapsed, "chars": 0, "tps": 0.0, "ok": False, "err": str(exc)}
            )
            print(f"  ERROR after {elapsed:.2f}s: {exc}")

    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    for r in results:
        status = "PASS" if r["ok"] else "FAIL"
        err = f"  {r.get('err','')}" if not r["ok"] else ""
        print(
            f"  [{status}] {r['name']:<24} {r['elapsed']:6.2f}s"
            f"  {r['chars']:5} chars  {r['tps']:5.1f} tok/s{err}"
        )

    snap = get_metrics_snapshot()
    print("\nMetrics snapshot (this process):")
    print(f"  total_calls:        {snap['total_calls']}")
    print(f"  avg_latency_ms:     {snap['avg_latency_ms']}")
    print(f"  avg_tokens_per_sec: {snap['avg_tokens_per_sec']}")
    print(f"  errors:             {snap['errors']}")
    for m_name, s in snap.get("by_model", {}).items():
        print(f"  [{m_name}] calls={s['calls']}  avg_lat={s['avg_latency_ms']}ms  errors={s['errors']}")


if __name__ == "__main__":
    run_bench()
