# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-10-28 | 83dda93 | speed |    0.83 | 96 |    115.4 |
| 2026-10-28 | 83dda93 | fixture |    8.98 | 29 |      3.2 |
