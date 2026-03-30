"""
scripts/turbo_compare.py
=========================
Before/After TurboQuant comparison:
  BEFORE: deepseek-coder:6.7b  Q4_0  3.83GB  (accuracy tier)
  AFTER:  deepseek-coder:1.3b  Q4_0  0.78GB  (TurboQuant / speed tier)

Same quantization algorithm (Q4_0), different model size.
This is exactly what the ModelRouter does: route execution tasks to the
smallest Q4_0 model that can still produce valid code JSON.

Metrics captured from Ollama's own counters (eval_count / eval_duration)
for accuracy — not wall-clock estimates.
"""
import json
import sys
import time
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/generate"

BEFORE_MODEL = "deepseek-coder:6.7b"   # accuracy tier
AFTER_MODEL  = "deepseek-coder:1.3b"   # TurboQuant / speed tier

# Three representative code tasks the AgentOS executor actually sends
TESTS = [
    {
        "name": "write_fibonacci",
        "prompt": (
            "Write a Python function called fibonacci(n) that returns the nth "
            "Fibonacci number using dynamic programming. Include type hints."
        ),
        "num_predict": 300,
    },
    {
        "name": "binary_search_tree",
        "prompt": (
            "Write a Python class BinarySearchTree with insert, search, and "
            "inorder_traversal methods. Include type hints and docstrings."
        ),
        "num_predict": 600,
    },
    {
        "name": "async_retry_client",
        "prompt": (
            "Write a Python async HTTP client function using aiohttp that retries "
            "failed requests with exponential backoff (max 3 retries). "
            "Include type hints and error handling."
        ),
        "num_predict": 500,
    },
]


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
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
    wall = time.time() - t0
    data = json.loads(raw)
    return {**data, "_wall_s": wall}


def run_model(model: str, label: str) -> list:
    print(f"\n{'='*68}")
    print(f"  {label}: {model}")
    print(f"{'='*68}")
    results = []
    for t in TESTS:
        name = t["name"]
        prompt = t["prompt"]
        num_predict = t["num_predict"]
        print(f"\n  [{name}]  max_tokens={num_predict}")
        try:
            d = call_ollama(model, prompt, num_predict)
            wall_s     = d["_wall_s"]
            load_s     = d.get("load_duration", 0) / 1e9
            eval_count = d.get("eval_count", 0)
            eval_s     = d.get("eval_duration", 0) / 1e9
            tps        = eval_count / eval_s if eval_s > 0 else 0.0
            response   = d.get("response", "")
            preview    = response[:80].replace("\n", " ").strip()
            print(f"    wall_time:   {wall_s:.2f}s")
            print(f"    load_time:   {load_s:.2f}s")
            print(f"    eval_tokens: {eval_count}")
            print(f"    eval_time:   {eval_s:.2f}s")
            print(f"    tok/sec:     {tps:.1f}")
            print(f"    preview:     {preview!r}")
            results.append({
                "name": name, "wall_s": wall_s, "load_s": load_s,
                "eval_count": eval_count, "eval_s": eval_s, "tps": tps,
                "chars": len(response), "ok": True,
            })
        except Exception as exc:
            print(f"    ERROR: {exc}")
            results.append({"name": name, "ok": False, "err": str(exc),
                            "wall_s": 0, "tps": 0, "eval_count": 0})
    return results


def print_comparison(before: list, after: list):
    print(f"\n\n{'='*68}")
    print("  BEFORE vs AFTER — TurboQuant Comparison")
    print(f"  BEFORE: {BEFORE_MODEL}  (Q4_0  3.83GB  accuracy tier)")
    print(f"  AFTER:  {AFTER_MODEL}  (Q4_0  0.78GB  TurboQuant speed tier)")
    print(f"{'='*68}")

    header = f"  {'test':<24} {'before':>8}  {'after':>8}  {'speedup':>8}  {'before_tps':>10}  {'after_tps':>9}"
    print(header)
    print(f"  {'-'*24} {'-'*8}  {'-'*8}  {'-'*8}  {'-'*10}  {'-'*9}")

    speedups = []
    tps_before_all = []
    tps_after_all  = []

    for b, a in zip(before, after):
        name = b["name"]
        if not b["ok"] or not a["ok"]:
            print(f"  {name:<24}  SKIP (error)")
            continue
        bw = b["wall_s"]
        aw = a["wall_s"]
        speedup = bw / aw if aw > 0 else 0.0
        speedups.append(speedup)
        tps_before_all.append(b["tps"])
        tps_after_all.append(a["tps"])
        print(
            f"  {name:<24} {bw:>7.1f}s  {aw:>7.1f}s  {speedup:>7.1f}x"
            f"  {b['tps']:>9.1f}  {a['tps']:>8.1f}"
        )

    if speedups:
        avg_speedup    = sum(speedups) / len(speedups)
        avg_tps_before = sum(tps_before_all) / len(tps_before_all)
        avg_tps_after  = sum(tps_after_all)  / len(tps_after_all)
        tps_gain       = avg_tps_after / avg_tps_before if avg_tps_before > 0 else 0.0

        print(f"\n  {'AVERAGE':<24} {'':>8}  {'':>8}  {avg_speedup:>7.1f}x"
              f"  {avg_tps_before:>9.1f}  {avg_tps_after:>8.1f}")

        print(f"\n  Summary:")
        print(f"    Avg wall-time speedup:  {avg_speedup:.1f}x faster")
        print(f"    Avg tok/sec before:     {avg_tps_before:.1f}")
        print(f"    Avg tok/sec after:      {avg_tps_after:.1f}  ({tps_gain:.1f}x)")
        print(f"    Model size reduction:   3.83GB → 0.78GB  (5.0x smaller)")
        print(f"    RAM freed:              ~3.0GB per worker")
        print(f"\n  Trade-off: speed tier produces shorter/simpler code.")
        print(f"  Use accuracy tier (6.7b) for planning; speed tier (1.3b) for")
        print(f"  execution steps where JSON structure matters more than depth.")


def main():
    print("TurboQuant Before/After Benchmark")
    print(f"Ollama endpoint: {OLLAMA_URL}")

    before_results = run_model(BEFORE_MODEL, "BEFORE (accuracy)")
    after_results  = run_model(AFTER_MODEL,  "AFTER  (TurboQuant speed)")
    print_comparison(before_results, after_results)


if __name__ == "__main__":
    main()
