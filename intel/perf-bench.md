# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 6.17.0-1022-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-10-19 | ed055b5 | speed |    1.36 | 96 |     70.4 |
| 2026-10-19 | ed055b5 | fixture |    9.29 | 29 |      3.1 |
