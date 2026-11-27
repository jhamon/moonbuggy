# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1018-aws. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-27 | 136318b | speed |    0.83 | 96 |    115.3 |
| 2026-11-27 | 136318b | fixture |    9.14 | 29 |      3.2 |
