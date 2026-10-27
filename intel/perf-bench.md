# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 6.17.0-1022-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-10-27 | 380714b | speed |    1.35 | 96 |     71.4 |
| 2026-10-27 | 380714b | fixture |    9.04 | 29 |      3.2 |
