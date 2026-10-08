# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 6.17.0-1022-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-10-08 | 52a7f28 | speed |    0.83 | 96 |    115.0 |
| 2026-10-08 | 52a7f28 | fixture |    8.63 | 29 |      3.4 |
