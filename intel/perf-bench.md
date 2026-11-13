# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-13 | 978ff14 | speed |    1.02 | 96 |     94.0 |
| 2026-11-13 | 978ff14 | fixture |    8.73 | 29 |      3.3 |
