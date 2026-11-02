# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-02 | f535ba7 | speed |    0.78 | 96 |    123.4 |
| 2026-11-02 | f535ba7 | fixture |    8.58 | 29 |      3.4 |
