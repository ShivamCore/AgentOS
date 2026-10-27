# Benchmarking

Every number in this repository must trace back to a raw JSON file in `benchmarks/results/` produced by a script in `benchmarks/`. Numbers without such a file are not claims.

## Hardware and software for the results below

Apple M4 (10 cores), 16 GB RAM, macOS 26.2 arm64, Python 3.12.13, Ollama 0.40.6. Measured 2026-10-27/28 on a laptop with normal background load.

## 1. DAG scheduler (`benchmarks/bench_dag.py`)

**Workload.** Synthetic graphs of 16 nodes. Each step blocks for 50 ms, modelling an I/O-bound step such as waiting on a model or a container. Shapes:

- `wide`: 16 independent nodes;
- `chain`: a 16-node linear chain;
- `layered`: 4 layers of 4, each node depending on every node of the previous layer.

Each configuration was repeated 5 times; medians are reported. `ideal` is the critical-path time for the given worker count. `overhead = wall - ideal`.

```bash
python benchmarks/bench_dag.py --reps 5 --out benchmarks/results/dag_scheduler.json
```

| Workload | Workers | Median wall (s) | p95 (s) | Ideal (s) | Overhead (ms) | Speedup vs 1 worker |
|---|---:|---:|---:|---:|---:|---:|
| wide | 1 | 0.863 | 0.867 | 0.80 | 63.3 | 1.00 |
| wide | 2 | 0.438 | 0.439 | 0.40 | 37.8 | 1.97 |
| wide | 4 | 0.218 | 0.221 | 0.20 | 18.2 | 3.96 |
| wide | 8 | 0.111 | 0.111 | 0.10 | 10.8 | 7.79 |
| chain | 1–8 | 0.859–0.864 | ≤0.871 | 0.80 | 59–64 | 1.00 |
| layered | 2 | 0.435 | 0.437 | 0.40 | 34.7 | 1.99 |
| layered | 4 | 0.221 | 0.221 | 0.20 | 20.7 | 3.92 |
| layered | 8 | 0.221 | 0.221 | 0.20 | 20.8 | 3.92 (width is 4) |

Failure handling: in a `wide` graph at 4 workers where one root fails once and succeeds on retry, the median is 0.271 s, against 0.216 s without the failure. That is about one extra step duration. Completion rate was 100%.

**Interpretation.** Scheduling overhead is about 4 ms per sequential wave (poll interval plus thread handoff). Speedup tracks graph width up to `max_workers`. A chain gets no speedup, as expected. These numbers describe the scheduler only. In a real run, LLM calls are serialized by default (`OLLAMA_MAX_CONCURRENT=1`), so end-to-end speedup from parallel branches is limited to the non-LLM part of each step, unless Ollama is configured for parallel requests.

## 2. Inference (`benchmarks/bench_inference.py`)

**Method.**

1. Record the exact model tag, digest, family, parameter size and quantization from Ollama.
2. **Cold:** unload the model (`keep_alive: 0`), then time one request. Reported on its own and never averaged with warm runs.
3. One untimed warm-up request.
4. **Warm:** 3 timed requests for each of 3 fixed prompts (`benchmarks/prompts.json`), with `temperature=0`, `seed=42`, `num_predict=200`, streaming.
5. Measured per request: client TTFT and end-to-end latency; Ollama's `eval_count / eval_duration` (decode tok/s) and `load_duration`.
6. Failed requests are recorded with their error text. Nothing is estimated.

```bash
python benchmarks/bench_inference.py --model phi3.5 --reps 3 --out benchmarks/results/inference_phi3.5.json
```

**Result: `phi3.5:latest`** (phi3 family, 3.8B, Q4_0, digest `61819fb3…`)

| Measurement | Value |
|---|---|
| Cold first request | 5.74 s end-to-end; 0.78 s load; TTFT 0.98 s; 42.0 tok/s decode |
| Warm decode tok/s (n=9) | median 40.9 (p10 39.0, p90 41.8) |
| Warm end-to-end latency for 200 tokens | 4.82 s / 4.96 s / 5.25 s median per prompt (n=3 each) |
| Warm TTFT (n=9) | median 0.034 s (p90 0.107 s) |
| Success rate | 9/9 warm, 1/1 cold |

**Caveats.**

- The cold load (0.78 s) ran with the model file probably still in the OS page cache. A truly cold disk read would be slower.
- Repeating an identical prompt lets Ollama reuse its prompt cache, so warm TTFT mostly excludes prompt processing.
- n=3 per prompt is a small sample.
- Only one model was installed, so **no comparison was made**.

## 3. Quantization comparisons

None are reported. A valid comparison needs two tags of the **same** base model differing only in quantization (e.g. `llama3.2:3b-instruct-q4_K_M` vs `llama3.2:3b-instruct-q8_0`), run with this harness on the same machine in the same session. The harness warns when compared models differ in family or parameter size.

Output quality is **not** evaluated by any script in this repository, so no quality claim (including "no regression") is made.

## 4. Withdrawn claims

The README previously stated "2× faster inference … Zero quality regression", "47.74s → 4.48s", and a deepseek-coder 6.7b row of "11.8 tok/s · 95.0s avg". The re-audit found:

- the 6.7b "before" runs had all errored, so that row had no measurement behind it;
- 47.74s was a cold request (39.21s of load) compared against warm requests;
- `scripts/turbo_compare.py` labelled a 6.7b → 1.3b **model swap** as "TurboQuant";
- no quality evaluation existed;
- `scripts/bench_results.py` printed hard-coded numbers.

Those scripts were moved to `.attic/scripts/`. The original report is kept, with a warning header, at `docs/archive/turboquant_results_2026-03-31.md`.

## 5. Not yet measured

- End-to-end task latency and success rate over a fixed task set with a real model. One real run is recorded in `docs/engineering-audit.md`; that is anecdotal, not a benchmark.
- Sandbox (Docker) per-command overhead. Docker is not available on the measurement machine.
