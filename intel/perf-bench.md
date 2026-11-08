# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-08 | 3c40a14 | speed |    1.05 | 96 |     91.7 |
| 2026-11-08 | 3c40a14 | fixture |    8.80 | 29 |      3.3 |
