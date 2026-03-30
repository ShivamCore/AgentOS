"""
Direct Ollama API timing test for deepseek-coder:6.7b.
Uses Ollama's own eval_count / eval_duration for accurate tok/s.
"""
import json
import sys
import time
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "deepseek-coder:6.7b"

TESTS = [
    {
        "name": "simple_reverse",
        "prompt": "Write a Python function to reverse a string. Return only code.",
        "num_predict": 150,
    },
    {
        "name": "binary_search_tree",
        "prompt": (
            "Write a complete Python binary search tree class with insert, delete, "
            "search, and inorder traversal. Include type hints and docstrings."
        ),
        "num_predict": 800,
    },
    {
        "name": "async_http_client",
        "prompt": (
            "Write a complete async Python HTTP client class using aiohttp with: "
            "connection pooling, retry with exponential backoff, timeout, and error handling. "
            "Include full type hints."
        ),
        "num_predict": 1000,
    },
    {
        "name": "dag_scheduler",
        "prompt": (
            "Write a Python DAG task scheduler: parse tasks with dependencies, "
            "detect cycles with DFS, return topological order, support parallel groups. "
            "Full implementation with type hints."
        ),
        "num_predict": 900,
    },
]


def call_ollama(prompt: str, num_predict: int) -> dict:
    payload = json.dumps({
        "model": MODEL,
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


def main():
    print(f"Model: {MODEL}")
    print("=" * 68)

    results = []
    for t in TESTS:
        name = t["name"]
        prompt = t["prompt"]
        num_predict = t["num_predict"]
        print(f"\nTEST: {name}")
        print(f"  prompt_chars:  {len(prompt)}")
        print(f"  max_tokens:    {num_predict}")
        try:
            d = call_ollama(prompt, num_predict)

            wall_s        = d["_wall_s"]
            total_dur_s   = d.get("total_duration", 0) / 1e9
            load_dur_s    = d.get("load_duration", 0) / 1e9
            prompt_eval_s = d.get("prompt_eval_duration", 0) / 1e9
            eval_count    = d.get("eval_count", 0)
            eval_dur_s    = d.get("eval_duration", 0) / 1e9
            tps           = eval_count / eval_dur_s if eval_dur_s > 0 else 0.0
            response      = d.get("response", "")
            preview       = response[:100].replace("\n", " ").strip()

            print(f"  wall_time:     {wall_s:.2f}s")
            print(f"  total_dur:     {total_dur_s:.2f}s  (Ollama internal)")
            print(f"  load_dur:      {load_dur_s:.2f}s  (model load)")
            print(f"  prompt_eval:   {prompt_eval_s:.2f}s")
            print(f"  eval_count:    {eval_count} tokens")
            print(f"  eval_dur:      {eval_dur_s:.2f}s")
            print(f"  tokens/sec:    {tps:.1f}  (generation only)")
            print(f"  response_chars:{len(response)}")
            print(f"  preview:       {preview!r}")

            results.append({
                "name": name, "wall_s": wall_s, "total_s": total_dur_s,
                "load_s": load_dur_s, "eval_count": eval_count,
                "eval_s": eval_dur_s, "tps": tps,
                "chars": len(response), "ok": True,
            })
        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append({"name": name, "ok": False, "err": str(exc)})

    print("\n" + "=" * 68)
    print("SUMMARY")
    print("=" * 68)
    print(f"  {'test':<24} {'wall':>7}  {'load':>6}  {'eval_tok':>8}  {'tok/s':>6}  status")
    print(f"  {'-'*24} {'-'*7}  {'-'*6}  {'-'*8}  {'-'*6}  ------")
    for r in results:
        if r["ok"]:
            print(
                f"  {r['name']:<24} {r['wall_s']:>6.1f}s"
                f"  {r['load_s']:>5.1f}s"
                f"  {r['eval_count']:>8}"
                f"  {r['tps']:>6.1f}"
                f"  PASS"
            )
        else:
            print(f"  {r['name']:<24}  FAIL  {r.get('err','')}")

    ok = [r for r in results if r["ok"]]
    if ok:
        avg_tps = sum(r["tps"] for r in ok) / len(ok)
        avg_wall = sum(r["wall_s"] for r in ok) / len(ok)
        print(f"\n  avg wall time:  {avg_wall:.1f}s")
        print(f"  avg tok/sec:    {avg_tps:.1f}")


if __name__ == "__main__":
    main()
