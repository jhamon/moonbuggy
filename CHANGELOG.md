# Changelog

All notable changes to moonbuggy are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Runs are 1.29x to 1.89x faster, depending on workload shape, with no change
  to any result. Four changes, each measured on its own against all three
  benchmark shapes: the warm host now builds its module-to-swap index once
  before forking rather than scanning `sys.modules` per mutant; it freezes its
  heap, so each mutant's `pytest.main` no longer garbage-collects 25000
  inherited objects on the way out; mutant runs skip recomputing which
  installed plugins need assertion rewriting, an answer identical for every
  mutant; and `coverage` is imported once in the parent instead of once there
  and once in the host. See
  [docs/development/perf-hypotheses.md](docs/development/perf-hypotheses.md)
  for the register, including the two hypotheses that were measured and
  rejected.

## [0.1.0] - 2026-08-16

First published release.

### Added

- Fast mutation testing driven by per-line coverage: only the tests covering a
  mutated line are rerun, mutations are applied in memory rather than on disk,
  mutants run in parallel forked workers, and results are cached across runs.
- JSON Lines results with a derived plaintext view whose every line starts with
  a fixed status keyword, so `grep SURVIVED` works without knowing the schema.
- Zero-configuration operation: source layout and test suite are discovered
  from the project root.
- Five mutation operator families: arithmetic, boolean, boundary, comparison,
  and constant.
