# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-03 | 07f2b44 | speed |    1.34 | 96 |     71.4 |
| 2026-11-03 | 07f2b44 | fixture |    8.92 | 29 |      3.3 |
