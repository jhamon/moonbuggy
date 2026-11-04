# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-04 | 1d6aba5 | speed |    1.37 | 96 |     70.2 |
| 2026-11-04 | 1d6aba5 | fixture |    8.88 | 29 |      3.3 |
