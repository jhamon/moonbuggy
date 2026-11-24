# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1016-aws. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-24 | efe8c2e | speed |    0.82 | 96 |    116.7 |
| 2026-11-24 | efe8c2e | fixture |    8.85 | 29 |      3.3 |
