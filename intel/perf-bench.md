# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-05 | 5802a10 | speed |    1.33 | 96 |     72.0 |
| 2026-11-05 | 5802a10 | fixture |    9.12 | 29 |      3.2 |
