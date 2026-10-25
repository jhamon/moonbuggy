# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 6.17.0-1022-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-10-25 | 7ca54b4 | speed |    1.35 | 96 |     71.3 |
| 2026-10-25 | 7ca54b4 | fixture |    9.49 | 29 |      3.1 |
