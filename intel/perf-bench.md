# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1020-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-21 | 7df3f82 | speed |    1.01 | 96 |     95.3 |
| 2026-11-21 | 7df3f82 | fixture |   10.03 | 29 |      2.9 |
