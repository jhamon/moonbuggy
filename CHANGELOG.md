# Changelog

All notable changes to moonbuggy are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `NO_COVERAGE`, a sixth status. A mutant on a line that no test executes is
  now reported under its own keyword instead of as a survivor. It is a finding,
  not a pass: it exits `1` exactly as `SURVIVED` does, it appears in
  `results.jsonl` and `results.txt` like any other status, and the human report
  gives it its own section ("N lines no test reaches") below the survivors.
  The two are separated because the fix is different — a survivor needs a
  stronger assertion in a test that already runs, an unreached line needs a
  test to exist at all.

### Changed

- **BREAKING (output contract): `grep SURVIVED` no longer returns every
  finding.** Lines no test reaches used to be reported as `SURVIVED` with
  `tests_run=0`; they are now `NO_COVERAGE`. Anything that greps, filters or
  counts survivors — a CI step, a triage script, a dashboard, an agent prompt —
  must match both keywords to see what it saw before:

  ```console
  $ grep -E '^(SURVIVED|NO_COVERAGE)' .moonbuggy/results.txt
  ```

  and the JSONL equivalent, `select(.status=="SURVIVED" or
  .status=="NO_COVERAGE")`. The **exit code is deliberately unchanged**: both
  statuses exit `1`, so a gate that only reads the exit code needs no edit and
  cannot have been silently loosened by this release. The status vocabulary is
  documented in [Reading the output](docs/reading-the-output.md), which now
  lists six keywords rather than five.

- The run summary line gained a `NO_COVERAGE=` count, and the human report's
  footer a `no_coverage` tally and a closing line that names both findings
  ("exit 1 -- survivors, and lines no test reaches"). The mutation score's
  denominator is unchanged: unreached lines still count against it, because a
  missing test is exactly the thing the score is measuring.

- `CACHE_VERSION` is bumped to 3. Entries written by an earlier version hold
  `SURVIVED` for these mutants and would replay under the old name, so old
  cache files are ignored rather than misread and the first run after
  upgrading is cold.

- The README and the quickstart now lead with the agent onboarding path:
  `uv run --with moonbuggy moonbuggy -h` needs no install, and the help screen
  is the whole interface. The human install path follows it rather than
  preceding it.

### Fixed

- The results cache now keys on the run itself, not only on the code. A run
  whose `--pytest-arg` values, `--timeout` or interpreter differ from the one
  that filled the cache no longer reads its verdicts. Previously the two runs
  collided: you could add `--pytest-arg=--doctest-modules`, rerun, and be
  handed the earlier run's `SURVIVED` for every mutant, with a suspiciously
  high `cached=` count as the only hint. `-n/--workers` and `--jobs` are
  deliberately not part of the key — they change how the work is distributed,
  not what any test asserts. Existing cache files are ignored rather than
  misread (`CACHE_VERSION` is bumped), so the first run after upgrading is
  cold.

## [0.1.2] - 2026-08-19

### Added

- A human report: at a terminal, survivors are grouped by file and line, each
  shown with the code delta and a caret ruler under exactly the span that
  changed, followed by a summary. The grep-friendly one-line-per-mutant agent
  format is unchanged and is still what you get when stdout is piped or
  redirected, so anything parsing moonbuggy's output keeps working.
- `--report MODE` selects the format explicitly. Selection otherwise checks
  `MOONBUGGY_REPORT`, then whether `CI` is set (agent format, since a CI run is
  rarely a place for a human report; `CI` counts as set for anything but an
  empty string, `0` or `false`), then whether stdout is a terminal.
- `--color WHEN` (auto, always, never; `NO_COLOR` is honoured), `--width N` to
  wrap the human report to a fixed number of columns, and `--no-progress` to
  suppress the live progress line.
- A live progress line on stderr while mutants run, so a long run shows what it
  is doing without polluting stdout.
- `make check-cli`, which runs the CLI end to end against real pytest
  subprocesses.

### Changed

- `--quiet` now reports the summary line for the human format too, rather than
  applying only to the agent format.

### Fixed

- The human report keeps a mutant's location and score intact at any terminal
  width, windowing long source lines rather than truncating the parts that
  identify the mutant.
- Long lines clip on character boundaries, so an escape sequence is never cut
  in half, and East Asian wide characters are budgeted by display width.
- The report footer names the actual results path rather than a hardcoded one,
  and a run whose `--output-dir` falls outside the project degrades the summary
  line instead of failing.
- A source file that cannot be decoded, and a Ctrl-C during a run, are both
  handled rather than raising.

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
