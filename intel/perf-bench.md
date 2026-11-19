# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1019-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-19 | bb10f57 | speed |    1.06 | 96 |     90.8 |
| 2026-11-19 | bb10f57 | fixture |    9.17 | 29 |      3.2 |
