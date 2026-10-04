# Metrics Dashboard

CI-updated per commit. Each row is owned by a named bot; do not edit by hand.
A later CI run rewrites this file, and the rewrite itself is the drift-detection
signal. See `docs/competitive-intel.md` §4a for the re-ranking discipline.

Columns: correctness (oracle agreement, differential disagreement, FP/FN
history), performance (harness wall-clock, mutants/sec, hypothesis tag),
coverage (source line %), and gate status.

| date | commit | source_lines | test_lines | gate | owner | oracle_agree | diff_shared | diff_agree | diff_disagree | diff_unclassified | oracle_s | fast_suite_s | cov_pct | notes |
|------|--------|------------:|-----------:|------|-------|-------------:|------------:|-----------:|--------------:|------------------:|--------:|------------:|-------:|-------|
| 2026-10-04 | a1f3117 |  | | oracle+differential | @moonbuggy-boss | 444/923 | 923 | 444 (48.1%) | 573 |  | 0 | | n/a | Auto row via scripts/metrics_dashboard.py. Differential: 15 projects, 923 shared, 444 agree (48.1%), 573 disagreements, 0 unclassified. Oracle gate: 0 disagreements, 0 FP, 0 FN.
