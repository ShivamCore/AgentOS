"""
scripts/bench_all_models.py
============================
Benchmark every non-embedding model available in Ollama on 3 code tasks.
Uses Ollama's own eval_count / eval_duration for accurate tok/s.
Produces a ranked comparison table.
"""
import json
import sys
import time
import urllib.request

OLLAMA_BASE  = "http://localhost:11434"
TAGS_URL     = f"{OLLAMA_BASE}/api/tags"
GENERATE_URL = f"{OLLAMA_BASE}/api/generate"

# Embedding-only families to skip
SKIP_FAMILIES = {"nomic-bert", "bert"}

TESTS = [
    {
        "name": "fibonacci_dp",
        "prompt": (
            "Write a Python function fibonacci(n) using dynamic programming "
            "that returns the nth Fibonacci number. Include type hints."
        ),
        "num_predict": 250,
    },
    {
        "name": "binary_search_tree",
        "prompt": (
            "Write a Python class BinarySearchTree with insert, search, and "
            "inorder_traversal methods. Include type hints and docstrings."
        ),
        "num_predict": 500,
    },
    {
        "name": "async_retry_client",
        "prompt": (
            "Write a Python async function using aiohttp that retries failed "
            "HTTP requests with exponential backoff (max 3 retries). "
            "Include type hints and error handling."
        ),
        "num_predict": 400,
    },
]


def get_models():
    with urllib.request.urlopen(TAGS_URL, timeout=10) as r:
        data = json.loads(r.read())
    models = []
    for m in data.get("models", []):
        family = m.get("details", {}).get("family", "")
        if family in SKIP_FAMILIES:
            continue
        models.append({
            "name":  m["name"],
            "quant": m.get("details", {}).get("quantization_level", "?"),
            "size_gb": round(m.get("size", 0) / 1e9, 2),
            "family": family,
        })
    return sorted(models, key=lambda x: x["size_gb"])


def call_ollama(model: str, prompt: str, num_predict: int) -> dict:
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": num_predict,
            "num_ctx": 2048,
        },
    }).encode()
    req = urllib.request.Request(
        GENERATE_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
    wall = time.time() - t0
    d = json.loads(raw)
    return {**d, "_wall_s": wall}


def bench_model(model_info: dict) -> dict:
    name = model_info["name"]
    results = []
    for t in TESTS:
        try:
            d = call_ollama(name, t["prompt"], t["num_predict"])
            eval_count = d.get("eval_count", 0)
            eval_s     = d.get("eval_duration", 0) / 1e9
            load_s     = d.get("load_duration", 0) / 1e9
            tps        = eval_count / eval_s if eval_s > 0 else 0.0
            results.append({
                "test": t["name"],
                "wall_s": d["_wall_s"],
                "load_s": load_s,
                "eval_count": eval_count,
                "eval_s": eval_s,
                "tps": tps,
                "ok": True,
            })
            print(f"    [{t['name']}]  {d['_wall_s']:.1f}s  {tps:.1f} tok/s")
        except Exception as exc:
            results.append({"test": t["name"], "ok": False, "err": str(exc),
                            "wall_s": 0, "tps": 0, "eval_count": 0, "eval_s": 0})
            print(f"    [{t['name']}]  ERROR: {exc}")
    return {"model": model_info, "results": results}


def summarise(bench: dict) -> dict:
    ok = [r for r in bench["results"] if r["ok"]]
    if not ok:
        return {"avg_tps": 0, "avg_wall": 0, "ok": False}
    return {
        "avg_tps":  round(sum(r["tps"]    for r in ok) / len(ok), 1),
        "avg_wall": round(sum(r["wall_s"] for r in ok) / len(ok), 1),
        "ok": True,
    }


def main():
    models = get_models()
    print(f"Found {len(models)} non-embedding models\n")

    all_benches = []
    for m in models:
        print(f"\n{'='*60}")
        print(f"  {m['name']}  ({m['quant']}  {m['size_gb']}GB  {m['family']})")
        print(f"{'='*60}")
        b = bench_model(m)
        all_benches.append(b)

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n\n{'='*72}")
    print("  FULL BENCHMARK RESULTS — All Models")
    print(f"{'='*72}")
    print(f"  {'model':<35} {'quant':<8} {'size':>6}  {'avg_wall':>9}  {'avg_tok/s':>9}  {'tier'}")
    print(f"  {'-'*35} {'-'*8} {'-'*6}  {'-'*9}  {'-'*9}  ------")

    rows = []
    for b in all_benches:
        s = summarise(b)
        m = b["model"]
        if s["ok"]:
            rows.append((s["avg_tps"], s["avg_wall"], m, s))

    # Sort by tok/s descending
    rows.sort(key=lambda x: -x[0])

    for avg_tps, avg_wall, m, s in rows:
        # Assign tier label
        if avg_tps >= 40:
            tier = "TURBO"
        elif avg_tps >= 15:
            tier = "FAST"
        elif avg_tps >= 8:
            tier = "BALANCED"
        else:
            tier = "SLOW"
        print(
            f"  {m['name']:<35} {m['quant']:<8} {m['size_gb']:>5.2f}GB"
            f"  {avg_wall:>8.1f}s  {avg_tps:>9.1f}  {tier}"
        )

    # Failed models
    failed = [b for b in all_benches if not summarise(b)["ok"]]
    if failed:
        print(f"\n  FAILED:")
        for b in failed:
            print(f"    {b['model']['name']}")

    # ── Recommendation ────────────────────────────────────────────────────────
    if rows:
        fastest = rows[0]
        slowest = rows[-1]
        print(f"\n  Fastest:  {fastest[2]['name']}  ({fastest[0]:.1f} tok/s)")
        print(f"  Slowest:  {slowest[2]['name']}  ({slowest[0]:.1f} tok/s)")
        if len(rows) > 1:
            ratio = fastest[0] / slowest[0] if slowest[0] > 0 else 0
            print(f"  Speedup:  {ratio:.1f}x  (fastest vs slowest)")

        print(f"\n  ModelRouter recommendation:")
        # Best for planning (largest/most capable)
        by_size = sorted(rows, key=lambda x: -x[2]["size_gb"])
        print(f"    plan  → {by_size[0][2]['name']}  (largest, best reasoning)")
        # Best for execution (fastest)
        print(f"    code  → {rows[0][2]['name']}  (fastest, TurboQuant tier)")
        # Balanced
        balanced = [r for r in rows if 8 <= r[0] <= 30]
        if balanced:
            print(f"    debug → {balanced[0][2]['name']}  (balanced)")


if __name__ == "__main__":
    main()
