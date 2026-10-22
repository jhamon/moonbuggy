# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-10-22 | ea867ec | speed |    1.41 | 96 |     68.3 |
| 2026-10-22 | ea867ec | fixture |    9.74 | 29 |      3.0 |
