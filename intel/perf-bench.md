# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1016-aws. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-17 | 1fb4998 | speed |    0.83 | 96 |    115.6 |
| 2026-11-17 | 1fb4998 | fixture |    9.36 | 29 |      3.1 |
