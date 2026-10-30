# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-10-30 | 5e8366d | speed |    0.98 | 96 |     97.6 |
| 2026-10-30 | 5e8366d | fixture |    8.67 | 29 |      3.3 |
