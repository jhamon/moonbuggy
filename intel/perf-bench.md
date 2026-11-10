# Perf bench
Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.

Bench gate: **PASS**. Gate host: Linux 7.0.0-1017-azure. The gate compares runs on the same host only; a host change re-primes the baseline.

| date | commit | suite | wall_s | mut | mut/sec |
|------|--------|------|------:|----:|--------:|
| 2026-11-10 | 6bf0268 | speed |    1.34 | 96 |     71.5 |
| 2026-11-10 | 6bf0268 | fixture |    9.03 | 29 |      3.2 |
