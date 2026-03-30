"""
scripts/turbo_bench_pairs.py
=============================
Measures BEFORE (Q8_0) vs AFTER (TurboQuant: Q4_0 / Q4_K_M) for every
model family that has both variants installed.

Uses Ollama's own eval_count / eval_duration for accurate tok/s.
Writes results to docs/turboquant_results.md
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

OLLAMA_BASE  = "http://localhost:11434"
TAGS_URL     = f"{OLLAMA_BASE}/api/tags"
GEN_URL      = f"{OLLAMA_BASE}/api/generate"

# ── Model pairs: (family_label, before_Q8, after_TurboQuant) ─────────────────
PAIRS = [
    {
        "family":  "deepseek-coder 1.3b",
        "before":  "deepseek-coder:1.3b-instruct-q8_0",
        "after":   "deepseek-coder:1.3b",
        "b_quant": "Q8_0",  "b_size": 1.43,
        "a_quant": "Q4_0",  "a_size": 0.78,
    },
    {
        "family":  "deepseek-coder 6.7b",
        "before":  "deepseek-coder:6.7b-instruct-q8_0",
        "after":   "deepseek-coder:6.7b",
        "b_quant": "Q8_0",  "b_size": 7.16,
        "a_quant": "Q4_0",  "a_size": 3.83,
    },
    {
        "family":  "qwen2.5-coder 1.5b",
        "before":  "qwen2.5-coder:1.5b-base-q8_0",
        "after":   "qwen2.5-coder:1.5b-base",
        "b_quant": "Q8_0",  "b_size": 1.65,
        "a_quant": "Q4_K_M","a_size": 0.99,
    },
    {
        "family":  "qwen2.5 3b",
        "before":  "qwen2.5:3b-instruct-q8_0",
        "after":   "qwen2.5:3b",
        "b_quant": "Q8_0",  "b_size": 3.29,
        "a_quant": "Q4_K_M","a_size": 1.93,
    },
    {
        "family":  "llama3.2 3b",
        "before":  "llama3.2:3b-instruct-q8_0",
        "after":   "llama3.2:3b",
        "b_quant": "Q8_0",  "b_size": 3.42,
        "a_quant": "Q4_K_M","a_size": 2.02,
    },
]

PROMPTS = [
    {
        "name": "fibonacci_dp",
        "text": (
            "Write a Python function fibonacci(n) using dynamic programming "
            "that returns the nth Fibonacci number. Include type hints."
        ),
        "num_predict": 250,
    },
    {
        "name": "binary_search_tree",
        "text": (
            "Write a Python class BinarySearchTree with insert, search, and "
            "inorder_traversal methods. Include type hints and docstrings."
        ),
        "num_predict": 400,
    },
    {
        "name": "async_retry",
        "text": (
            "Write a Python async function using aiohttp that retries failed "
            "HTTP requests with exponential backoff (max 3 retries). "
            "Include type hints and error handling."
        ),
        "num_predict": 350,
    },
]


def installed_models():
    with urllib.request.urlopen(TAGS_URL, timeout=10) as r:
        return {m["name"] for m in json.loads(r.read()).get("models", [])}


def call(model: str, prompt: str, num_predict: int) -> dict:
    payload = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.1, "num_predict": num_predict, "num_ctx": 2048},
    }).encode()
    req = urllib.request.Request(
        GEN_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as resp:
        d = json.loads(resp.read())
    d["_wall_s"] = time.time() - t0
    return d


def bench_model(model: str, prompts: list) -> dict:
    rows = []
    for p in prompts:
        try:
            d = call(model, p["text"], p["num_predict"])
            ec  = d.get("eval_count", 0)
            es  = d.get("eval_duration", 0) / 1e9
            tps = ec / es if es > 0 else 0.0
            rows.append({
                "prompt":     p["name"],
                "wall_s":     round(d["_wall_s"], 2),
                "load_s":     round(d.get("load_duration", 0) / 1e9, 2),
                "eval_tok":   ec,
                "eval_s":     round(es, 2),
                "tps":        round(tps, 1),
                "ok":         True,
            })
            print(f"      {p['name']:<22} {d['_wall_s']:6.1f}s  {tps:5.1f} tok/s")
        except Exception as exc:
            rows.append({"prompt": p["name"], "ok": False, "err": str(exc),
                         "wall_s": 0, "tps": 0})
            print(f"      {p['name']:<22} ERROR: {exc}")
    ok = [r for r in rows if r["ok"]]
    avg_tps  = round(sum(r["tps"]    for r in ok) / len(ok), 1) if ok else 0
    avg_wall = round(sum(r["wall_s"] for r in ok) / len(ok), 1) if ok else 0
    return {"rows": rows, "avg_tps": avg_tps, "avg_wall": avg_wall}


def main():
    installed = installed_models()
    results = []

    for pair in PAIRS:
        print(f"\n{'='*60}")
        print(f"  {pair['family']}")
        print(f"{'='*60}")

        pair_result = {"pair": pair, "before": None, "after": None}

        for role in ("before", "after"):
            model = pair[role]
            if model not in installed:
                print(f"  [{role.upper()}] {model} — NOT INSTALLED, skipping")
                continue
            quant = pair["b_quant"] if role == "before" else pair["a_quant"]
            size  = pair["b_size"]  if role == "before" else pair["a_size"]
            print(f"\n  [{role.upper()}] {model}  ({quant}  {size}GB)")
            pair_result[role] = bench_model(model, PROMPTS)

        results.append(pair_result)

    write_report(results)
    print_summary(results)


def write_report(results: list):
    lines = []
    lines.append("# TurboQuant Benchmark Report")
    lines.append("")
    lines.append("**Hardware:** Apple Silicon (local Ollama)")
    lines.append("**Date:** 2026-03-31")
    lines.append("**Method:** Ollama `/api/generate` — `eval_count / eval_duration` for tok/s")
    lines.append("**Prompts:** 3 Python code generation tasks (fibonacci DP, BST, async retry)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Summary — Speed Gain per Model Family")
    lines.append("")
    lines.append("| Model Family | BEFORE (Q8_0) | AFTER (TurboQuant) | tok/s BEFORE | tok/s AFTER | Speedup | Wall-time BEFORE | Wall-time AFTER | Time Saved |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|")

    for r in results:
        pair = r["pair"]
        b    = r["before"]
        a    = r["after"]
        if not b or not a:
            lines.append(f"| {pair['family']} | {pair['before']} | {pair['after']} | N/A | N/A | N/A | N/A | N/A | N/A |")
            continue
        speedup   = round(a["avg_tps"] / b["avg_tps"], 2) if b["avg_tps"] > 0 else 0
        time_save = round(b["avg_wall"] - a["avg_wall"], 1)
        lines.append(
            f"| {pair['family']} "
            f"| {pair['before']} ({pair['b_quant']} {pair['b_size']}GB) "
            f"| {pair['after']} ({pair['a_quant']} {pair['a_size']}GB) "
            f"| {b['avg_tps']} "
            f"| {a['avg_tps']} "
            f"| **{speedup}x** "
            f"| {b['avg_wall']}s "
            f"| {a['avg_wall']}s "
            f"| **{time_save}s** |"
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Per-Model Detailed Results")
    lines.append("")

    for r in results:
        pair = r["pair"]
        lines.append(f"### {pair['family']}")
        lines.append("")

        for role, label in [("before", "BEFORE"), ("after", "AFTER")]:
            model = pair[role]
            quant = pair["b_quant"] if role == "before" else pair["a_quant"]
            size  = pair["b_size"]  if role == "before" else pair["a_size"]
            data  = r[role]

            lines.append(f"#### {label}: `{model}` ({quant}, {size}GB)")
            lines.append("")
            if not data:
                lines.append("_Not installed — skipped._")
                lines.append("")
                continue

            lines.append("| Prompt | Wall Time | Load Time | Tokens Generated | Eval Time | tok/s |")
            lines.append("|---|---:|---:|---:|---:|---:|")
            for row in data["rows"]:
                if row["ok"]:
                    lines.append(
                        f"| {row['prompt']} "
                        f"| {row['wall_s']}s "
                        f"| {row['load_s']}s "
                        f"| {row['eval_tok']} "
                        f"| {row['eval_s']}s "
                        f"| {row['tps']} |"
                    )
                else:
                    lines.append(f"| {row['prompt']} | ERROR | — | — | — | — |")
            lines.append(f"| **Average** | **{data['avg_wall']}s** | — | — | — | **{data['avg_tps']}** |")
            lines.append("")

        # Delta section
        b = r["before"]
        a = r["after"]
        if b and a:
            speedup   = round(a["avg_tps"] / b["avg_tps"], 2) if b["avg_tps"] > 0 else 0
            time_save = round(b["avg_wall"] - a["avg_wall"], 1)
            size_save = round((1 - pair["a_size"] / pair["b_size"]) * 100, 1)
            lines.append("#### Delta (AFTER vs BEFORE)")
            lines.append("")
            lines.append(f"| Metric | Value |")
            lines.append(f"|---|---|")
            lines.append(f"| tok/s speedup | **{speedup}x faster** |")
            lines.append(f"| avg wall-time saved | **{time_save}s per task** |")
            lines.append(f"| model size reduction | **{size_save}% smaller** ({pair['b_size']}GB → {pair['a_size']}GB) |")
            lines.append(f"| quantization | {pair['b_quant']} → {pair['a_quant']} |")
            lines.append("")

        lines.append("---")
        lines.append("")

    lines.append("## Quantization Reference")
    lines.append("")
    lines.append("| Format | Bits/weight | Speed | Quality | Notes |")
    lines.append("|---|---|---|---|---|")
    lines.append("| F16 | 16 | Slowest | Best | Full precision baseline |")
    lines.append("| Q8_0 | 8 | Slow | Near-lossless | BEFORE TurboQuant |")
    lines.append("| Q4_K_M | 4 (mixed) | Fast | Good | Balanced TurboQuant |")
    lines.append("| Q4_0 | 4 | Fastest | Acceptable | Aggressive TurboQuant |")
    lines.append("")
    lines.append("## ModelRouter Configuration (recommended)")
    lines.append("")
    lines.append("```python")
    lines.append("# agent/utils/model_router.py")
    lines.append("_TIER_ACCURACY = ['llama3.1:8b', 'deepseek-coder:6.7b']   # plan tasks")
    lines.append("_TIER_SPEED    = ['deepseek-coder:1.3b', 'qwen2.5-coder:1.5b-base']  # code tasks")
    lines.append("_TIER_BALANCED = ['llama3.2:3b', 'qwen2.5:3b']            # debug tasks")
    lines.append("```")
    lines.append("")

    out = Path("docs/turboquant_results.md")
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"\n  Report written → {out}")


def print_summary(results: list):
    print(f"\n\n{'='*72}")
    print("  TURBOQUANT BEFORE vs AFTER — FINAL SUMMARY")
    print(f"{'='*72}")
    print(f"  {'Family':<22} {'BEFORE':>8}  {'AFTER':>8}  {'Speedup':>8}  {'Time saved':>11}  {'Size saved'}")
    print(f"  {'-'*22} {'-'*8}  {'-'*8}  {'-'*8}  {'-'*11}  ----------")

    for r in results:
        pair = r["pair"]
        b    = r["before"]
        a    = r["after"]
        if not b or not a:
            print(f"  {pair['family']:<22}  SKIPPED (model not installed)")
            continue
        speedup   = round(a["avg_tps"] / b["avg_tps"], 2) if b["avg_tps"] > 0 else 0
        time_save = round(b["avg_wall"] - a["avg_wall"], 1)
        size_save = round((1 - pair["a_size"] / pair["b_size"]) * 100, 1)
        print(
            f"  {pair['family']:<22} {b['avg_tps']:>7.1f}  {a['avg_tps']:>7.1f}"
            f"  {speedup:>7.2f}x  {time_save:>+10.1f}s  {size_save}% smaller"
        )

    print(f"\n  (positive time saved = AFTER is faster)")


if __name__ == "__main__":
    main()
