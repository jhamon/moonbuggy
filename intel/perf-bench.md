# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-10-31 | 7f79c55 | speed |    1.20 | 96 |     80.0 |
| 2026-10-31 | 7f79c55 | fixture |    9.52 | 29 |      3.0 |
