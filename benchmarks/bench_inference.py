#!/usr/bin/env python3
"""
Ollama inference benchmark with explicit methodology.

For each model (exact tag, digest and quantization are recorded from Ollama):
  1. COLD: unload the model (keep_alive=0), then time one request. Reported
     separately; never mixed with warm numbers.
  2. WARM-UP: ``--warmup`` untimed requests.
  3. WARM: ``--reps`` timed requests per prompt, streaming, capturing
       ttft_s       time to first streamed token (client-side)
       latency_s    end-to-end request time (client-side)
       eval_tps     eval_count / eval_duration (Ollama's counters)
       load_s       Ollama's load_duration (should be ~0 when warm)
Fixed prompts live in benchmarks/prompts.json; generation is capped with
num_predict and temperature 0 so output lengths are comparable.
Failures (timeouts, OOM, missing model) are recorded as failures with the
error text. Nothing is estimated or substituted.

A quantization comparison is only meaningful between tags of the SAME base
model (e.g. llama3.2:3b-instruct-q4_K_M vs llama3.2:3b-instruct-q8_0). The script
warns when compared models have different families or parameter sizes.

    python benchmarks/bench_inference.py --model phi3.5 --reps 3 --out benchmarks/results/inference.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time

from datetime import UTC, datetime

import requests

HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
PROMPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts.json")


def hardware() -> dict:
    info = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
    }
    if sys.platform == "darwin":
        for key in ("machdep.cpu.brand_string", "hw.memsize"):
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                info[key] = subprocess.run(
                    ["sysctl", "-n", key], capture_output=True, text=True, timeout=5
                ).stdout.strip()
    return info


def model_info(model: str) -> dict:
    tags = requests.get(f"{HOST}/api/tags", timeout=10).json().get("models", [])
    for m in tags:
        if m["name"] in (model, f"{model}:latest"):
            d = m.get("details", {})
            return {
                "name": m["name"],
                "digest": m.get("digest"),
                "size_bytes": m.get("size"),
                "family": d.get("family"),
                "parameter_size": d.get("parameter_size"),
                "quantization": d.get("quantization_level"),
                "format": d.get("format"),
            }
    raise SystemExit(
        f"model {model!r} is not installed (installed: {[m['name'] for m in tags]})"
    )


def generate(model: str, prompt: str, num_predict: int, timeout: float) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "keep_alive": "10m",
        "options": {"temperature": 0, "seed": 42, "num_predict": num_predict},
    }
    t0 = time.perf_counter()
    ttft = None
    final: dict = {}
    try:
        with requests.post(
            f"{HOST}/api/generate", json=payload, stream=True, timeout=(5, timeout)
        ) as r:
            if r.status_code >= 400:
                return {
                    "ok": False,
                    "error": f"HTTP {r.status_code}: {r.text[:300]}",
                    "latency_s": time.perf_counter() - t0,
                }
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    return {
                        "ok": False,
                        "error": chunk["error"],
                        "latency_s": time.perf_counter() - t0,
                    }
                if ttft is None and chunk.get("response"):
                    ttft = time.perf_counter() - t0
                if chunk.get("done"):
                    final = chunk
                    break
    except requests.RequestException as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_s": time.perf_counter() - t0,
        }
    latency = time.perf_counter() - t0
    ec, ed = final.get("eval_count", 0), final.get("eval_duration", 0)
    return {
        "ok": bool(final),
        "error": None if final else "stream ended without done",
        "latency_s": latency,
        "ttft_s": ttft,
        "eval_count": ec,
        "eval_tps": (ec / (ed / 1e9)) if ec and ed else None,
        "prompt_eval_count": final.get("prompt_eval_count"),
        "load_s": final.get("load_duration", 0) / 1e9,
    }


def unload(model: str) -> None:
    requests.post(
        f"{HOST}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60
    )
    time.sleep(1.0)


def stats(xs: list[float]) -> dict | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    p = lambda q: xs[min(len(xs) - 1, round(q * (len(xs) - 1)))]  # noqa: E731
    return {
        "n": len(xs),
        "median": statistics.median(xs),
        "p10": p(0.10),
        "p90": p(0.90),
        "min": xs[0],
        "max": xs[-1],
    }


def bench_model(
    model: str, prompts: list[dict], reps: int, warmup: int, timeout: float
) -> dict:
    info = model_info(model)
    unload(model)
    cold = generate(model, prompts[0]["prompt"], prompts[0]["num_predict"], timeout)
    for _ in range(warmup):
        generate(model, prompts[0]["prompt"], 8, timeout)
    per_prompt = {}
    for p in prompts:
        runs = [
            generate(model, p["prompt"], p["num_predict"], timeout) for _ in range(reps)
        ]
        ok = [r for r in runs if r["ok"]]
        per_prompt[p["name"]] = {
            "runs": runs,
            "failures": len(runs) - len(ok),
            "latency_s": stats([r["latency_s"] for r in ok]),
            "ttft_s": stats([r["ttft_s"] for r in ok]),
            "eval_tps": stats([r["eval_tps"] for r in ok]),
            "eval_count": stats([r["eval_count"] for r in ok]),
            "load_s": stats([r["load_s"] for r in ok]),
        }
    all_ok = [r for v in per_prompt.values() for r in v["runs"] if r["ok"]]
    return {
        "model": info,
        "cold_first_request": cold,
        "warm": per_prompt,
        "warm_overall": {
            "eval_tps": stats([r["eval_tps"] for r in all_ok]),
            "ttft_s": stats([r["ttft_s"] for r in all_ok]),
            "success_rate": len(all_ok)
            / max(1, sum(len(v["runs"]) for v in per_prompt.values())),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--prompts", default=PROMPTS)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    with open(args.prompts) as f:
        prompts = json.load(f)
    version = requests.get(f"{HOST}/api/version", timeout=5).json().get("version")
    results = [
        bench_model(m, prompts, args.reps, args.warmup, args.timeout)
        for m in args.model
    ]
    fams = {(r["model"]["family"], r["model"]["parameter_size"]) for r in results}
    warnings = []
    if len(fams) > 1:
        warnings.append(
            "Compared models differ in family/parameter size: differences are NOT attributable to "
            "quantization alone."
        )
    out = {
        "benchmark": "ollama_inference",
        "timestamp": datetime.now(UTC).isoformat(),
        "ollama_version": version,
        "hardware": hardware(),
        "method": {
            "reps": args.reps,
            "warmup": args.warmup,
            "temperature": 0,
            "seed": 42,
            "stream": True,
        },
        "warnings": warnings,
        "results": results,
    }
    for r in results:
        c = r["cold_first_request"]
        w = r["warm_overall"]
        print(
            f"{r['model']['name']} ({r['model']['quantization']}, {r['model']['parameter_size']})"
        )
        print(
            f"  cold first request: ok={c['ok']} latency={c['latency_s']:.2f}s load={c.get('load_s', 0):.2f}s"
        )
        if w["eval_tps"]:
            print(
                f"  warm eval tok/s median={w['eval_tps']['median']:.1f} (n={w['eval_tps']['n']}), "
                f"TTFT median={w['ttft_s']['median']:.3f}s, success={w['success_rate']:.0%}"
            )
    for wmsg in warnings:
        print("WARNING:", wmsg)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(out, f, indent=2)
        print(f"raw results: {args.out}")


if __name__ == "__main__":
    main()
