# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1020-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-22 | 0da44b1 | speed |    1.01 | 96 |     94.8 |
| 2026-11-22 | 0da44b1 | fixture |    9.27 | 29 |      3.1 |
