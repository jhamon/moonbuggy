# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1019-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-16 | 989e5c4 | speed |    1.33 | 96 |     72.2 |
| 2026-11-16 | 989e5c4 | fixture |    9.04 | 29 |      3.2 |
