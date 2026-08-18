# Changelog

All notable changes to moonbuggy are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-08-17

### Changed

- Runs are a further 1.20x to 1.28x faster, on top of the two rounds below,
  with no change to any result — checked by diffing every mutant's status and
  tests-run count across all three benchmark shapes, 728 mutants, on every
  change that touched the mutant path. Five changes, each measured on its own:
  the warm host performs the test collection once, before forking, so each
  mutant filters an inherited collection instead of repeating it (a warm
  mutant run is 6.3ms to 2.3ms); the coverage pass and the flakiness probe skip
  assertion rewriting, whose only product is a failure message moonbuggy never
  reads; the host indexes only the modules that can actually be mutated rather
  than every module it has loaded; it does its job-independent preparation
  while the parent is still planning, instead of afterwards; and the process
  exits without running interpreter finalisation, after flushing.

  The profiler now reports moonbuggy's own import chain as a phase. It had
  been 51–70ms of unattributed time in every profile for two rounds, and
  naming it is what made the last of those changes findable.

  See [docs/development/perf-hypotheses.md](docs/development/perf-hypotheses.md)
  for the register, including the three hypotheses refuted or rejected before
  any code was written, the one implemented and discarded, and the one adopted
  that measures exactly zero and says so.
- Runs are a further 1.12x to 1.30x faster, on top of the round below, with no
  change to any result — checked by diffing every mutant's status and
  tests-run count across all three benchmark shapes, 728 mutants, on every
  change that touched the mutant path. Four changes, each measured on its own:
  the flakiness probe now runs in its own process alongside the coverage pass
  instead of after it, so it costs cores rather than wall clock; the warm host
  builds the pytest configuration every mutant needs once, before forking,
  rather than each mutant rebuilding an identical one; each mutant's run
  collects only the test files its selected node ids name, instead of building
  a collector for every file in the suite and discarding all but two; and the
  host freezes its inherited heap at startup as well as before forking.
  Mutants also now run one per core rather than one fewer. Against mutmut on
  the speed workload this is 1.85x, and against the naive baseline 38x. See
  [docs/development/perf-hypotheses.md](docs/development/perf-hypotheses.md)
  for the register, including the four hypotheses that were measured and
  rejected and the one that was implemented, measured and discarded.
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
