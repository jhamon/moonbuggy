# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1019-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-15 | 96b2048 | speed |    0.80 | 96 |    120.0 |
| 2026-11-15 | 96b2048 | fixture |    9.69 | 29 |      3.0 |
