# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-10-21 | 7bd8e28 | speed |    1.08 | 96 |     88.7 |
| 2026-10-21 | 7bd8e28 | fixture |    9.00 | 29 |      3.2 |
