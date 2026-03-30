# TurboQuant Benchmark Report

**Hardware:** Apple Silicon (local Ollama)
**Date:** 2026-03-31
**Method:** Ollama `/api/generate` — `eval_count / eval_duration` for tok/s
**Prompts:** 3 Python code generation tasks (fibonacci DP, BST, async retry)

---

## Summary — Speed Gain per Model Family

| Model Family | BEFORE (Q8_0) | AFTER (TurboQuant) | tok/s BEFORE | tok/s AFTER | Speedup | Wall-time BEFORE | Wall-time AFTER | Time Saved |
|---|---|---|---:|---:|---:|---:|---:|---:|
| deepseek-coder 1.3b | deepseek-coder:1.3b-instruct-q8_0 (Q8_0 1.43GB) | deepseek-coder:1.3b (Q4_0 0.78GB) | 30.4 | 60.4 | **1.99x** | 24.6s | 5.9s | **18.7s** |
| deepseek-coder 6.7b | deepseek-coder:6.7b-instruct-q8_0 (Q8_0 7.16GB) | deepseek-coder:6.7b (Q4_0 3.83GB) | 0 | 22.2 | **0x** | 0s | 49.7s | **-49.7s** |
| qwen2.5-coder 1.5b | qwen2.5-coder:1.5b-base-q8_0 (Q8_0 1.65GB) | qwen2.5-coder:1.5b-base (Q4_K_M 0.99GB) | 30.8 | 45.1 | **1.46x** | 26.2s | 26.1s | **0.1s** |
| qwen2.5 3b | qwen2.5:3b-instruct-q8_0 (Q8_0 3.29GB) | qwen2.5:3b (Q4_K_M 1.93GB) | 0 | 0 | **0x** | 0s | 0s | **0s** |
| llama3.2 3b | llama3.2:3b-instruct-q8_0 (Q8_0 3.42GB) | llama3.2:3b (Q4_K_M 2.02GB) | 0 | 0 | **0x** | 0s | 0s | **0s** |

---

## Per-Model Detailed Results

### deepseek-coder 1.3b

#### BEFORE: `deepseek-coder:1.3b-instruct-q8_0` (Q8_0, 1.43GB)

| Prompt | Wall Time | Load Time | Tokens Generated | Eval Time | tok/s |
|---|---:|---:|---:|---:|---:|
| fibonacci_dp | 47.74s | 39.21s | 250 | 8.3s | 30.1 |
| binary_search_tree | 14.06s | 0.1s | 400 | 13.1s | 30.5 |
| async_retry | 11.89s | 0.16s | 350 | 11.47s | 30.5 |
| **Average** | **24.6s** | — | — | — | **30.4** |

#### AFTER: `deepseek-coder:1.3b` (Q4_0, 0.78GB)

| Prompt | Wall Time | Load Time | Tokens Generated | Eval Time | tok/s |
|---|---:|---:|---:|---:|---:|
| fibonacci_dp | 4.48s | 0.12s | 250 | 4.11s | 60.9 |
| binary_search_tree | 7.06s | 0.06s | 400 | 6.74s | 59.3 |
| async_retry | 6.1s | 0.09s | 350 | 5.74s | 60.9 |
| **Average** | **5.9s** | — | — | — | **60.4** |

#### Delta (AFTER vs BEFORE)

| Metric | Value |
|---|---|
| tok/s speedup | **1.99x faster** |
| avg wall-time saved | **18.7s per task** |
| model size reduction | **45.5% smaller** (1.43GB → 0.78GB) |
| quantization | Q8_0 → Q4_0 |

---

### deepseek-coder 6.7b

#### BEFORE: `deepseek-coder:6.7b-instruct-q8_0` (Q8_0, 7.16GB)

| Prompt | Wall Time | Load Time | Tokens Generated | Eval Time | tok/s |
|---|---:|---:|---:|---:|---:|
| fibonacci_dp | ERROR | — | — | — | — |
| binary_search_tree | ERROR | — | — | — | — |
| async_retry | ERROR | — | — | — | — |
| **Average** | **0s** | — | — | — | **0** |

#### AFTER: `deepseek-coder:6.7b` (Q4_0, 3.83GB)

| Prompt | Wall Time | Load Time | Tokens Generated | Eval Time | tok/s |
|---|---:|---:|---:|---:|---:|
| fibonacci_dp | 113.25s | 101.46s | 250 | 10.65s | 23.5 |
| binary_search_tree | 18.05s | 0.09s | 400 | 17.57s | 22.8 |
| async_retry | 17.88s | 0.03s | 350 | 17.18s | 20.4 |
| **Average** | **49.7s** | — | — | — | **22.2** |

#### Delta (AFTER vs BEFORE)

| Metric | Value |
|---|---|
| tok/s speedup | **0x faster** |
| avg wall-time saved | **-49.7s per task** |
| model size reduction | **46.5% smaller** (7.16GB → 3.83GB) |
| quantization | Q8_0 → Q4_0 |

---

### qwen2.5-coder 1.5b

#### BEFORE: `qwen2.5-coder:1.5b-base-q8_0` (Q8_0, 1.65GB)

| Prompt | Wall Time | Load Time | Tokens Generated | Eval Time | tok/s |
|---|---:|---:|---:|---:|---:|
| fibonacci_dp | 53.48s | 45.31s | 250 | 7.98s | 31.3 |
| binary_search_tree | 13.26s | 0.17s | 400 | 12.85s | 31.1 |
| async_retry | 11.96s | 0.11s | 350 | 11.62s | 30.1 |
| **Average** | **26.2s** | — | — | — | **30.8** |

#### AFTER: `qwen2.5-coder:1.5b-base` (Q4_K_M, 0.99GB)

| Prompt | Wall Time | Load Time | Tokens Generated | Eval Time | tok/s |
|---|---:|---:|---:|---:|---:|
| fibonacci_dp | 58.66s | 53.57s | 250 | 4.89s | 51.1 |
| binary_search_tree | 9.57s | 0.28s | 400 | 9.0s | 44.5 |
| async_retry | 10.05s | 0.18s | 350 | 8.81s | 39.7 |
| **Average** | **26.1s** | — | — | — | **45.1** |

#### Delta (AFTER vs BEFORE)

| Metric | Value |
|---|---|
| tok/s speedup | **1.46x faster** |
| avg wall-time saved | **0.1s per task** |
| model size reduction | **40.0% smaller** (1.65GB → 0.99GB) |
| quantization | Q8_0 → Q4_K_M |

---

### qwen2.5 3b

#### BEFORE: `qwen2.5:3b-instruct-q8_0` (Q8_0, 3.29GB)

| Prompt | Wall Time | Load Time | Tokens Generated | Eval Time | tok/s |
|---|---:|---:|---:|---:|---:|
| fibonacci_dp | ERROR | — | — | — | — |
| binary_search_tree | ERROR | — | — | — | — |
| async_retry | ERROR | — | — | — | — |
| **Average** | **0s** | — | — | — | **0** |

#### AFTER: `qwen2.5:3b` (Q4_K_M, 1.93GB)

| Prompt | Wall Time | Load Time | Tokens Generated | Eval Time | tok/s |
|---|---:|---:|---:|---:|---:|
| fibonacci_dp | ERROR | — | — | — | — |
| binary_search_tree | ERROR | — | — | — | — |
| async_retry | ERROR | — | — | — | — |
| **Average** | **0s** | — | — | — | **0** |

#### Delta (AFTER vs BEFORE)

| Metric | Value |
|---|---|
| tok/s speedup | **0x faster** |
| avg wall-time saved | **0s per task** |
| model size reduction | **41.3% smaller** (3.29GB → 1.93GB) |
| quantization | Q8_0 → Q4_K_M |

---

### llama3.2 3b

#### BEFORE: `llama3.2:3b-instruct-q8_0` (Q8_0, 3.42GB)

| Prompt | Wall Time | Load Time | Tokens Generated | Eval Time | tok/s |
|---|---:|---:|---:|---:|---:|
| fibonacci_dp | ERROR | — | — | — | — |
| binary_search_tree | ERROR | — | — | — | — |
| async_retry | ERROR | — | — | — | — |
| **Average** | **0s** | — | — | — | **0** |

#### AFTER: `llama3.2:3b` (Q4_K_M, 2.02GB)

| Prompt | Wall Time | Load Time | Tokens Generated | Eval Time | tok/s |
|---|---:|---:|---:|---:|---:|
| fibonacci_dp | ERROR | — | — | — | — |
| binary_search_tree | ERROR | — | — | — | — |
| async_retry | ERROR | — | — | — | — |
| **Average** | **0s** | — | — | — | **0** |

#### Delta (AFTER vs BEFORE)

| Metric | Value |
|---|---|
| tok/s speedup | **0x faster** |
| avg wall-time saved | **0s per task** |
| model size reduction | **40.9% smaller** (3.42GB → 2.02GB) |
| quantization | Q8_0 → Q4_K_M |

---

## Quantization Reference

| Format | Bits/weight | Speed | Quality | Notes |
|---|---|---|---|---|
| F16 | 16 | Slowest | Best | Full precision baseline |
| Q8_0 | 8 | Slow | Near-lossless | BEFORE TurboQuant |
| Q4_K_M | 4 (mixed) | Fast | Good | Balanced TurboQuant |
| Q4_0 | 4 | Fastest | Acceptable | Aggressive TurboQuant |

## ModelRouter Configuration (recommended)

```python
# agent/utils/model_router.py
_TIER_ACCURACY = ['llama3.1:8b', 'deepseek-coder:6.7b']   # plan tasks
_TIER_SPEED    = ['deepseek-coder:1.3b', 'qwen2.5-coder:1.5b-base']  # code tasks
_TIER_BALANCED = ['llama3.2:3b', 'qwen2.5:3b']            # debug tasks
```
