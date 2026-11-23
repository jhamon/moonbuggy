# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1020-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-23 | 0693367 | speed |    1.20 | 96 |     79.9 |
| 2026-11-23 | 0693367 | fixture |    9.08 | 29 |      3.2 |
