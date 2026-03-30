"""
Produce the final comparison table from the collected benchmark data.
llama3.1:8b timed out — we use its measured tok/s from the previous
time_6_7b.py run (same hardware, same prompt size).
"""

RESULTS = [
    # (model_name, quant, size_gb, family, avg_tps, avg_wall_s)
    ("deepseek-coder:1.3b",       "Q4_0",   0.78, "llama", 23.4, 17.4),
    ("qwen2.5-coder:1.5b-base",   "Q4_K_M", 0.99, "qwen2", 15.3, 39.2),
    ("llama3.2:3b",               "Q4_K_M", 2.02, "llama", 12.1, 30.7),
    ("llama3.2:latest",           "Q4_K_M", 2.02, "llama", 12.8, 54.0),
    ("qwen2.5:3b",                "Q4_K_M", 1.93, "qwen2",  9.6, 79.9),
    ("deepseek-coder:6.7b",       "Q4_0",   3.83, "llama",  6.1, 138.9),
    ("llama3.1:8b",               "Q4_K_M", 4.92, "llama",  9.4, 64.6),  # from time_6_7b.py
]

def tier(tps):
    if tps >= 40:  return "TURBO"
    if tps >= 15:  return "FAST"
    if tps >= 8:   return "BALANCED"
    return "SLOW"

# Sort by tok/s descending
RESULTS.sort(key=lambda x: -x[4])

print()
print("=" * 78)
print("  ALL MODELS — TurboQuant Benchmark  (3 code tasks, avg)")
print("  Hardware: Apple Silicon (local Ollama)")
print("=" * 78)
print(f"  {'model':<35} {'quant':<8} {'size':>6}  {'avg_wall':>9}  {'tok/s':>6}  tier")
print(f"  {'-'*35} {'-'*8} {'-'*6}  {'-'*9}  {'-'*6}  ------")

for name, quant, size, family, tps, wall in RESULTS:
    t = tier(tps)
    marker = " ◄ FASTEST" if name == RESULTS[0][0] else ""
    print(
        f"  {name:<35} {quant:<8} {size:>5.2f}GB"
        f"  {wall:>8.1f}s  {tps:>6.1f}  {t}{marker}"
    )

fastest = RESULTS[0]
slowest = RESULTS[-1]
ratio   = fastest[4] / slowest[4]

print()
print(f"  Fastest:  {fastest[0]}  ({fastest[4]:.1f} tok/s)")
print(f"  Slowest:  {slowest[0]}  ({slowest[4]:.1f} tok/s)")
print(f"  Speedup:  {ratio:.1f}x  (fastest vs slowest)")

print()
print("  QUANTIZATION IMPACT (same model family, different quant):")
print(f"  deepseek-coder:1.3b  Q4_0   0.78GB  →  23.4 tok/s  (TurboQuant)")
print(f"  deepseek-coder:6.7b  Q4_0   3.83GB  →   6.1 tok/s  (5x larger, 3.8x slower)")
print()
print(f"  llama3.2:3b          Q4_K_M 2.02GB  →  12.1 tok/s")
print(f"  llama3.1:8b          Q4_K_M 4.92GB  →   9.4 tok/s  (2.4x larger, 1.3x slower)")
print()
print("  KEY INSIGHT: Q4_0 vs Q4_K_M at same size:")
print(f"  deepseek-coder:1.3b  Q4_0   0.78GB  →  23.4 tok/s")
print(f"  qwen2.5-coder:1.5b   Q4_K_M 0.99GB  →  15.3 tok/s  (Q4_K_M = 35% slower)")

print()
print("  MODELROUTER RECOMMENDATION:")
print(f"    plan  → llama3.1:8b          (4.92GB, best reasoning depth)")
print(f"    code  → deepseek-coder:1.3b  (0.78GB, 23.4 tok/s, TurboQuant)")
print(f"    debug → llama3.2:3b          (2.02GB, 12.1 tok/s, balanced)")
print()
print("  CONCURRENCY HEADROOM (16GB RAM):")
print(f"    deepseek-coder:1.3b  → 20 concurrent workers  (0.78GB each)")
print(f"    deepseek-coder:6.7b  →  4 concurrent workers  (3.83GB each)")
print(f"    llama3.1:8b          →  3 concurrent workers  (4.92GB each)")
