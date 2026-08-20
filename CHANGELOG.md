# Changelog

All notable changes to moonbuggy are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **A mutation policy for logging calls.** A mutation inside the arguments of a
  logging call -- `logger.debug("retrying in %ds", delay * 2)` -- is unkillable
  by construction: nothing asserts on the contents of a debug line. Those
  mutants are now tagged `logging_call` in `results.jsonl` and, by default,
  reported `SKIPPED` instead of `SURVIVED`. In the session that prompted this,
  two thirds of the survivors in a retry region were arithmetic inside
  `logger.debug(...)` arguments.

  **A condition *around* a log call is still a finding.** Only the call's own
  argument expressions qualify: in `if attempts > 5: logger.debug(...)` the `>`
  is reported exactly as before. A real call nested in a log line
  (`logger.info("%s", compute(n + 1))`) is not suppressed either -- `compute`
  runs, so its arguments matter.

  A logger is recognised as a level method (`debug`, `info`, `warning`,
  `error`, `critical`, `exception`, `log`) called on a name like `log`,
  `logger`, `logging`, `LOG`, `LOGGER`, their underscore-prefixed spellings, or
  any attribute chain ending in one -- `self.logger.debug(...)` counts.
  `--logger-name NAME` (repeatable) adds names for a project that wraps its
  logger; `--include-logging-mutants` runs them anyway and keeps the tag, for a
  project that does assert on log output. Both flags are accepted by
  `moonbuggy`, `moonbuggy run <id>` and `moonbuggy why <id>`.

  **Mutant ids are unchanged** -- the policy labels mutants, it does not filter
  them -- so nothing in `.moonbuggy/cache.json` or in an accepted-equivalents
  ledger is invalidated, and `CACHE_VERSION` is unchanged. A suppressed mutant
  is settled before the cache is consulted, so a cached verdict can never be
  replayed for one.

  `SKIPPED` leaves the score's denominator, as it always has, so suppressing
  these does not flatter the kill rate. The human report says how many were
  suppressed and how to see them.

- **A sixth operator, `condition_negation`**, which wraps the test of an
  `if`/`elif`, of a conditional expression, and of each comprehension `if`
  clause in `not (...)`. Conditions that are not comparisons, boolean chains or
  literals were previously **unmutated entirely**: `if is_valid(x):`,
  `if flag:` and `if not ready:` produced no mutant at all, so a suite could be
  completely blind to `is_valid` and moonbuggy would report nothing. Predicate
  helpers are the ordinary way people write conditionals, not an exotic shape.

  **This raises the mutant count on every existing project**, and the first run
  after upgrading will surface survivors that were never reported before. They
  are not new gaps; they are gaps that were previously invisible. Cached
  verdicts for existing mutants are unaffected — mutant ids are unchanged, so
  nothing already in `.moonbuggy/cache.json` or in an accepted-equivalents
  ledger is invalidated. Only the new ids have to be measured.

  `while` tests are deliberately **not** negated. `while queue:` →
  `while not queue:` never terminates when the loop was not entered to begin
  with — the shape of any empty-input test — so the mutant burns the whole
  `--timeout` rather than failing fast, and a loop-heavy module would pay that
  many times over for a mutation nobody plausibly writes. Literal tests are not
  negated either: `if True:` already yields `if False:` from `constant_bool`.

- **Operator context, and targeted yields**, for anyone writing an operator.
  An operator may now implement `mutations_in_context(node, context)` instead
  of `mutations(node)` and be told where the node sits — its parent, the field
  it occupies, its index in that field, the chain of enclosing nodes, and the
  nearest enclosing node of a given type. Either form may yield a
  `(target, replacement)` pair to rewrite a node other than the one it was
  handed, which is how an operator reaches the test of a compound statement,
  or a child of an `ast.arguments`/`ast.comprehension` node that carries no
  source position of its own.

  Both are additions. The five operators that predate them are unchanged and
  still take a bare node, adding an operator is still adding one file, and
  `all_operators()` and `@register` are exactly what they were. See
  [Writing an operator](docs/writing-an-operator.md).

- **A machine-readable run summary**, so nothing has to be parsed out of a
  human sentence. Every run now writes `summary.json` beside `results.jsonl`,
  and `moonbuggy --json` prints that same object to stdout and nothing else.
  It carries counts for all six statuses, the accepted/unexplained split from
  the ledger, total mutants with cached and measured split out, wall time, the
  exit code, whether the run was diff-scoped and against what, and the run's
  **effective configuration** — operators, include/exclude, `--pytest-arg`,
  timeout and the rest. That last part is what makes a results directory
  self-describing: it is the same set of inputs the cache key covers, so two
  results files that disagree can be told apart by their inputs. A diff-scoped
  run with nothing to mutate reports zeroes rather than an empty stream.

  Nothing was added to `results.jsonl`: a run has exactly one summary, that
  file has exactly one kind of line, and keeping them apart means no consumer
  needs a discriminator to tell them apart. `results.txt` is byte for byte
  what it was, with or without `--json`.

- **A schema version on every results record.** Each line of `results.jsonl`
  now carries `"schema": 2`, and the summary reports both its own `schema` and
  the `record_schema` of the records beside it, so a consumer can key off a
  number rather than off which fields happen to be present. Lines written
  before the accepted-equivalents ledger existed are schema 1; moonbuggy
  upgrades them to today's shape as it reads them, which is what lets its own
  reporter index `accepted`/`accept_reason` instead of guessing at them.

- `moonbuggy why <id>`: explain how a mutant is handled, without running it.
  Two very different problems look identical from a result line — selection
  never picked up your new test, or the verdict came from the cache and nothing
  ran — and telling them apart used to mean reading moonbuggy's source or
  spending minutes on a controlled experiment. `why` prints the file, line and
  diff `show` gives, plus **which tests selection chooses and where that set
  came from**, the count behind the `tests_run=` token, and **whether the
  results cache already holds a verdict for those exact inputs** — with the
  key, the files whose contents go into it, and the `--pytest-arg`/`--timeout`/
  interpreter that make up the rest of it. When nothing is selected it says so
  outright and says that means no test reaches the line, which is what makes a
  run report `NO_COVERAGE`.

- `moonbuggy why` **reads the cache and never writes to it**, and never runs
  the mutant: re-measuring is `moonbuggy run`'s job, and a `why` that measured
  would be a slower `run` rather than a different command. It costs one
  coverage pass, serving every id named in the invocation, and always exits `0`
  — an explanation is not a finding, so a `why` in a CI script cannot fail the
  build. `--json` emits one object per mutant in the JSONL shape
  `results.jsonl` uses, including a `next_run` prediction (`skipped`,
  `suspicious`, `cache`, `no_coverage` or `measure`) that follows the planner's
  own order of decisions.

- `moonbuggy run <id>`: re-run one mutant, or several, without a full run.
  `moonbuggy show <id>` could print a mutant but not execute it, so checking
  whether a new test kills a survivor meant hand-applying the mutation and
  running pytest yourself. `run` is `show` with the run attached — the same
  coverage pass, the same coverage-guided selection, the same runner — and it
  prints the two things a one-line report cannot carry: every test that was
  selected, and every one of them that failed. It honours `--pytest-arg`,
  `--timeout`, `-n` and `--flaky-probe`, and its exit code mirrors a full
  run's: `0` when every target was killed, `1` for a `SURVIVED` or a
  `NO_COVERAGE`, `2` when it could not run at all.

- `moonbuggy run` **never serves a cached verdict for its target**, because
  re-measuring is the entire point of the command. It does store the fresh
  verdict, under the same fingerprinted key a full run uses, so verifying a fix
  makes the next full run shorter rather than longer; `--no-cache` turns the
  write off. It leaves `results.jsonl` and `results.txt` alone — they are the
  record of a *run*, and rewriting one line of them from a single-mutant
  measurement would leave a file whose summary no longer describes it.

- `moonbuggy run` takes **several ids, and `-` to read them from stdin**, so a
  round of new tests can be checked against every outstanding finding at once:
  `grep -E '^(SURVIVED|NO_COVERAGE)' .moonbuggy/results.txt | moonbuggy run -`.
  Whole result lines are accepted as well as bare ids, and piped output is the
  same one-line-per-mutant format `results.txt` uses, so one `run` pipes into
  the next.

- `moonbuggy accept <id> --reason "..."`: an accepted-equivalents ledger. A
  survivor a human has reviewed and decided is equivalent is recorded in
  `.moonbuggy/accepted.toml` (or `--accept-file`) with the reason for the
  decision, so neither you nor the next reviewer pays for that triage again.
  `moonbuggy accept --list` prints the ledger and `moonbuggy accept --remove
  <id>` takes an entry back out. The ledger is a checked-in file: it records
  human decisions about your code, not run output, which is why it does not
  move with `--output-dir` -- and why `moonbuggy accept` warns you when git
  would ignore it.

- Accepted mutants **still run and are still reported**. They move out of the
  human report's punch list into an "Accepted as equivalent" section that
  carries each reason, they are counted separately in the footer
  ("3 accepted as equivalent, 0 unexplained"), and in `results.jsonl` each one
  carries `accepted` and `accept_reason`. Hiding them would let a real
  regression through behind an old decision.

- An acceptance **expires when its line changes**. Each entry stores a
  fingerprint of the mutation it was made for; edit that line and the entry is
  stale, reported by id, and counted as unexplained rather than honoured. The
  fingerprint covers the mutated line rather than the whole module, so an
  unrelated edit to a comment does not expire every acceptance in the file --
  a stated trade, since a change elsewhere in the module can still undermine an
  equivalence argument without expiring the entry.

- An acceptance **survives a line insertion above it**. Mutant ids are
  `path:line:operator:index`, so an id alone would evaporate on an unrelated
  edit; content alone could not tell two identical lines apart. Acceptance keys
  on the id first, then on exactly one same-file mutant with the same
  fingerprint. Two equally good candidates are refused rather than guessed.

- `--fail-on-unexplained`: exit `1` only for findings that are neither killed
  nor accepted -- the flag that makes moonbuggy a CI gate rather than an audit
  you read by hand. Without it the exit codes are exactly what they were, so
  adding a ledger never silently turns a red build green. Exit `2` still means
  the run could not happen; an unreadable accept file is now one of the reasons
  for it.

- `--since <ref>`: diff-scoped runs. `moonbuggy --since origin/main` generates
  mutants only for the lines your branch changed, compared against the merge
  base — a handful of mutants and seconds of runtime on a typical pull request,
  which is what makes mutation testing affordable on every PR rather than as a
  scheduled audit. It is a filter on generation, so the mutants are the ones a
  full run would produce for those lines, with the same ids and the same
  verdicts, and it composes with `--include`/`--exclude` rather than replacing
  them. The scope includes uncommitted edits and untracked files, because the
  working tree is what moonbuggy mutates; deletions scope in nothing, and a
  rename is scoped under the file's new path. A run with no changed source
  lines exits `0` with empty results instead of failing a gate for a docs-only
  branch. Not a git repository, an unresolvable ref, or a shallow clone with no
  merge base each exit `2` with a message naming the fix — in CI that is
  usually `fetch-depth: 0` on `actions/checkout`. See
  [Making runs fast](docs/making-runs-fast.md).

- A diff-scoped run says so, in the human report's header and its footer:
  "Diff-scoped: only lines changed since origin/main (merge base 4f21c0a) were
  mutated -- 2 files, 31 lines." A 100% kill rate on three mutants is never
  reported in a form that could be mistaken for a clean full run. The agent
  format's per-mutant lines and its final summary line are unchanged; the
  scope is announced on stderr before the run.

- `--since` is deliberately **not** part of the cache fingerprint. How a run
  reached a mutant cannot change that mutant's verdict, so diff-scoped and full
  runs fill and read the same cache — a mutant answered by last night's full
  run is not re-run by today's PR run.

- `NO_COVERAGE`, a sixth status. A mutant on a line that no test executes is
  now reported under its own keyword instead of as a survivor. It is a finding,
  not a pass: it exits `1` exactly as `SURVIVED` does, it appears in
  `results.jsonl` and `results.txt` like any other status, and the human report
  gives it its own section ("N lines no test reaches") below the survivors.
  The two are separated because the fix is different — a survivor needs a
  stronger assertion in a test that already runs, an unreached line needs a
  test to exist at all.

### Changed

- **`results.jsonl` record schema 3**, adding `logging_call`. Records written
  by an older version are upgraded on read as usual, with `logging_call` false
  -- a version with no logging policy recognised none of them. `suppressed` now
  means "settled without running" for either reason; `logging_call` says which.

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

- **`moonbuggy -h` now explains the two things a run does that are invisible
  from the outside.** The help screen is the advertised onboarding path, so
  anything an agent must reason about before acting has to survive there, and
  two things did not: what a cache hit depends on, and what decides
  `tests_run`. Both were previously discoverable only by running a controlled
  experiment. The epilog now says that a verdict is reused only when the
  mutant, its module's full source, every selected test file, and the run's
  `--pytest-arg`/`--timeout`/interpreter are all unchanged — and that one
  instrumented pass builds a line-to-test map, rebuilt every run and never
  written to disk, from which each mutant runs only the tests that execute its
  line. `tests_run=0` is named as `NO_COVERAGE`, the status it has carried
  since this release.

- Every flag the parser accepts now carries help text. `--output-dir` was
  listed with a bare metavar and nothing beside it on all five commands, which
  is the one shape of help worse than no help at all; `moonbuggy show`'s
  positional argument was likewise unexplained. A test now fails the build if
  any option is added without help.

- `--jobs` is documented as defaulting to the CPU count, one fewer under
  `-n/--workers`. The README said "CPU count - 1" and `-h` said "CPU count";
  both were half right, because the two runner paths genuinely differ — the
  xdist path holds a core back for the parent, the warm-session path does not,
  since there the parent is blocked on a pipe for the whole interval.

- `make check-fast-path` and `make check-pytest-args` run two suites that were
  in no gate at all. Both files are `slow`-marked, so the default `make test`
  deselects them, and no target named them — meaning CI never ran the D5
  fast-path oracle (the load-bearing correctness test in the project) or the
  `--pytest-arg` cache-key regression test. Both are now in `make check-all`.

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
