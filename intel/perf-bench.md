# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1016-aws. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-18 | 0bd8604 | speed |    0.84 | 96 |    114.2 |
| 2026-11-18 | 0bd8604 | fixture |    9.10 | 29 |      3.2 |
