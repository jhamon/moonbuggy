# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 6.17.0-1022-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-10-05 | 500c54a | speed |    1.06 | 96 |     90.2 |
| 2026-10-05 | 500c54a | fixture |    9.61 | 29 |      3.0 |
