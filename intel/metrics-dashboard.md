# Metrics Dashboard

CI-updated per commit. Each row is owned by a named bot; do not edit by hand.
A later CI run rewrites this file, and the rewrite itself is the drift-detection
signal. See `docs/competitive-intel.md` §4a for the re-ranking discipline.

Columns: correctness (oracle agreement, differential disagreement, FP/FN
history), performance (harness wall-clock, mutants/sec, hypothesis tag),
coverage (source line %), and gate status.

| date | commit | source_lines | test_lines | gate | owner | oracle_agree | diff_shared | diff_agree | diff_disagree | diff_unclassified | oracle_s | fast_suite_s | cov_pct | notes |
|------|--------|------------:|-----------:|------|-------|-------------:|------------:|-----------:|--------------:|------------------:|--------:|------------:|-------:|-------|
| 2026-08-31 | b15b103 | 12,287 | 9,784 | oracle+differential | @moonbuggy-qa | 29/29 | 382 | 45 (11.8%) | 386 | 0 | 13.70 | 6.65 | 63% | First measured row. Oracle: 21 KILLED, 6 SURVIVED, 1 TIMEOUT, 1 SKIPPED (75.0% kill rate). Diff: 10 projects, 941 moonbuggy mutants vs 1,170 mutmut; all 386 disagreements classified (337 genuine semantic diff, 49 ambiguous join). Full suite: 496 passed in 15.29s. |