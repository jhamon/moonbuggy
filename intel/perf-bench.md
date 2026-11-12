# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-12 | 3d52f3f | speed |    1.33 | 96 |     72.1 |
| 2026-11-12 | 3d52f3f | fixture |    9.41 | 29 |      3.1 |
