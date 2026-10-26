# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 6.17.0-1022-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-10-26 | 745b6ab | speed |    1.19 | 96 |     80.7 |
| 2026-10-26 | 745b6ab | fixture |   10.86 | 29 |      2.7 |
