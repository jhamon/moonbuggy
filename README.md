# moonbuggy

Fast, agent-first mutation testing for Python.

Mutation testing measures whether your tests would actually notice if the code
broke. It makes small changes to your source — flipping a `<` to a `<=`, a
`True` to a `False` — and reruns the tests. A change no test objects to is a
gap: a missing test, a weak assertion, or a line nothing exercises.

Two things make moonbuggy different:

- **Speed.** It runs only the tests that actually cover each mutated line,
  applies mutations in memory rather than writing files, runs mutants in
  parallel forked processes, does the work every mutant shares once in a warm
  host rather than once per mutant, and caches results across runs. On a suite
  where test execution dominates, that is **38x faster than the naive
  approach** of rerunning everything per mutant, and **about 1.9x faster than
  mutmut**. Both figures come from `make bench`, which measures all three tools
  in one session on one machine; see
  [benchmark results](https://jhamon.github.io/moonbuggy/benchmark-results.html)
  for what they do and do not establish.
- **Output built for agents.** Results are JSON Lines, with a derived plaintext
  view whose every line starts with a fixed keyword, so `grep SURVIVED` works
  with no knowledge of the schema.

## Point an agent at it

This is the intended way to use moonbuggy, and it needs no install at all.
Show your agent the help screen and let it drive:

```bash
uv run --with moonbuggy moonbuggy -h
```

`uv run --with` fetches moonbuggy into a throwaway environment for that one
command, so nothing is added to your project. Everything an agent needs to
operate the tool is on that screen: there is no config file, no scaffolding
step, and no tutorial to read first.

From your project root, inside the virtualenv where your tests already run,
the first real invocation is one line:

```bash
uv run --with moonbuggy moonbuggy --include src/yourpkg --pytest-arg=-q
```

`--include` keeps the first run small while you are still deciding whether you
like the answers, and `--pytest-arg` passes anything your suite needs through
to every pytest run. Both are optional; bare `moonbuggy` discovers the rest.

## Install

Full documentation is published at
[jhamon.github.io/moonbuggy](https://jhamon.github.io/moonbuggy/).

Requires Python 3.12+ and pytest.

For repeated use, install it into your project instead:

```bash
pip install moonbuggy
```

## Use

From your project root, with no flags and no configuration:

```bash
moonbuggy
```

It discovers your source layout, runs one instrumented pass to build a
line→test map, then runs each mutant against only its covering tests.

Three result files land in `.moonbuggy/`:

| file | role |
|---|---|
| `results.jsonl` | canonical, one JSON object per mutant |
| `results.txt` | plaintext view, derived from the JSONL |
| `summary.json` | the run itself: counts, totals, wall time, effective config |

Two other things live in the same directory. `cache.json` is written by every
run that does not pass `--no-cache`, and is disposable. `accepted.toml` appears
once you run `moonbuggy accept`, and is the opposite — a checked-in record of
human decisions, which is why the gitignore snippet below un-ignores it.

`moonbuggy --json` prints that summary object to stdout and nothing else, so
nothing has to be parsed out of the human line:

```console
$ moonbuggy --json | jq '.counts.survived'
```

Exit code is `0` when there are no findings, `1` when there are survivors or
lines no test reaches, `2` when the run could not start, and `130` when the run
was interrupted — partial results are already on disk and are valid.

### Reading the output

Every plaintext line begins with one of exactly seven keywords:

| keyword | meaning |
|---|---|
| `KILLED` | a test assertion failed under the mutation — the good outcome |
| `KILLED_BY_ERROR` | a test errored out under the mutation — still a kill, but see below |
| `SURVIVED` | every selected test passed — a gap, or an equivalent mutant |
| `NO_COVERAGE` | no test executes the line at all, so nothing could object |
| `TIMEOUT` | the mutation caused a hang, killed by the time budget |
| `SUSPICIOUS` | pytest could not complete; needs a look |
| `SKIPPED` | suppressed: a `# moonbuggy: skip` marker, or a mutation inside a logging call |

`KILLED_BY_ERROR` is a kill: it counts toward the score and it is not a
finding, so it does not affect the exit code. What it tells you is what the
kill *proves*. A failed assertion proves a test checked the behaviour the
mutation changed. A test that raised `NameError` proves only that a test runs
the line — the mutant broke the code badly enough that touching it explodes,
and nothing was checked. The distinction is rare under the default operators
and common under the `deep` tier, where it is the difference between a
meaningful kill rate and a flattering one. Before 0.1.4 both were `KILLED`, so
**`grep KILLED` no longer catches every kill** — `grep -E '^KILLED'` does.

`SURVIVED` and `NO_COVERAGE` are both findings and both exit `1`. They are
separate keywords because the fix is different: a survivor needs a stronger
assertion in a test that already runs, and a `NO_COVERAGE` line needs a test to
exist at all (or the code to go). Before 0.2.0 the second was reported as
`SURVIVED` with `tests_run=0`, so **`grep SURVIVED` no longer catches uncovered
lines** — grep for both to get every finding:

```bash
grep -E '^(SURVIVED|NO_COVERAGE)' .moonbuggy/results.txt
```

Each line carries `key=value` tokens, including `nearest_test=` — the test to
extend to close that gap.

Lines are one per mutant and never contain the diff, so they stay grep- and
awk-friendly. To see a mutant in full:

```bash
moonbuggy show 'shipping.py:5:comparison_swap:0'
```

### Checking a fix without a full run

You wrote the test you think kills that mutant. Ask:

```bash
moonbuggy run 'shipping.py:5:comparison_swap:0'
```

`run` re-measures exactly that mutant with the same coverage-guided selection a
full run uses, and prints which tests were selected and which of them failed.
The verdict is always measured — a cached one would answer a different
question — and `results.jsonl` is left as the last full run wrote it. Exit code
matches a full run: `0` if the mutant is now killed, `1` if it still survives or
still has no coverage.

It takes several ids, and `-` reads them from stdin, so the whole outstanding
set goes back through in one command:

```bash
grep -E '^(SURVIVED|NO_COVERAGE)' .moonbuggy/results.txt | moonbuggy run -
```

### Asking why a mutant was handled the way it was

A survivor that will not die has two very different causes, and a result line
cannot tell them apart: selection never picked up your new test, or the verdict
came from the cache and nothing ran at all.

```bash
moonbuggy why 'shipping.py:5:comparison_swap:0'
```

`why` runs no mutant. It reports the decisions instead: which tests selection
picks and where that set came from, how many there are (that is the
`tests_run=` on the result line), and whether the cache already holds a verdict
for those exact inputs — with the key and the files that go into it, so you can
see what would invalidate it. If nothing is selected it says so outright, and
says that means no test reaches the line. `--json` gives the same thing as
JSONL for an agent.

That is the format you get when output is piped or redirected. At a terminal
you get a human report instead: survivors grouped by file and line, each with
the code delta and a caret under exactly what changed.

```bash
moonbuggy --report human
```

Format selection checks, in order: the `--report` flag, then
`MOONBUGGY_REPORT`, then whether `CI` is set in the environment (agent format,
since a CI run is rarely a place for a human report), then whether stdout is a
terminal. `CI` counts as set for anything but an empty string, `0` or
`false`, so the usual `CI=false` escape hatch works here too. Set
`MOONBUGGY_REPORT=agent` to pin the grep-friendly format
everywhere, including at a terminal — worth doing in an agent harness that
allocates a pty, where terminal detection would otherwise pick the human
report.

### Suppressing an equivalent mutant

Some mutants cannot be killed by any test because the mutated program is
genuinely equivalent — changing a cache size, say. Detecting these
automatically is undecidable, so moonbuggy does not try. Mark them in the
source:

```python
CACHE_SIZE = 128  # moonbuggy: skip -- tuning only, no observable behaviour
```

They are then reported `SKIPPED` rather than silently dropped, so the mutant
count stays honest.

### Mutants inside logging calls

Nothing asserts on the contents of a debug line, so a mutation inside one is
unkillable by construction:

```python
logger.debug("retrying in %ds", delay * 2)
```

Left alone, these dominate a survivor list. moonbuggy tags them `logging_call`
in `results.jsonl` and reports them `SKIPPED` by default. The guard *around* a
log call is untouched — `if attempts > 5:` above that line is a real finding
and stays one; only the call's own argument expressions are suppressed.

```bash
moonbuggy --logger-name audit          # your project wraps its logger
moonbuggy --include-logging-mutants    # you do assert on log output
```

`SKIPPED` leaves the score's denominator, so this shortens the list you read
without flattering the number. See
[Equivalent mutants](docs/equivalent-mutants.md#logging-calls).

### Accepting one you have already reviewed

A finding you have reviewed and decided is equivalent goes in a ledger, so
neither you nor the next reviewer pays for that triage twice. Either finding
status can be accepted — a `NO_COVERAGE` line you have decided is genuinely
untestable as readily as a `SURVIVED` mutant:

```bash
moonbuggy accept 'shipping.py:5:comparison_swap:0' --reason "both branches return the same value for every reachable input"
moonbuggy accept --list
moonbuggy accept --remove 'shipping.py:5:comparison_swap:0'
```

The ledger is `.moonbuggy/accepted.toml`, and it is meant to be committed —
most projects gitignore `.moonbuggy/`, so exclude `.moonbuggy/*` and add
`!.moonbuggy/accepted.toml` instead. Accepted mutants still run and are still
reported; they are counted separately rather than hidden. An acceptance expires
the moment its line changes, and is then reported as unexplained again.

`moonbuggy --fail-on-unexplained` exits non-zero only for findings that are
neither killed nor accepted, which is the flag that makes moonbuggy a CI gate
rather than an audit you read by hand. See
[Equivalent mutants](docs/equivalent-mutants.md).

### Choosing which operators run

`moonbuggy operators` lists every operator with its tier, its rough cost, and
one line on what it mutates — so you never have to guess a name, and neither
does an agent. `--json` gives the same listing as one JSON object.

```bash
moonbuggy operators
```

`--operators` then takes three shapes:

```bash
moonbuggy --operators comparison_swap,boundary   # exactly these two
moonbuggy --operators deep                       # the deep tier's members
moonbuggy --operators +statement_deletion        # the default tier, plus one
```

A bare list of names is an *exact* set — that has always been true and has not
changed. Tier names (`default`, `deep`, `all`) and the `+` prefix are syntax on
top of it. `default` is the cheap, high-signal operators, which is what a bare
`moonbuggy` runs; `deep` is for operators that are expensive or noisy enough to
be opted into deliberately. A name that does not exist is an error rather than
a run with no mutants in it.

The `deep` tier has four members. `statement_deletion` replaces a statement with `pass`,
which is the highest-yield mutation there is — a survivor means the statement
can be removed from the program and the suite still passes — and it costs
roughly one extra mutant per statement, which is why it is opt-in. It pairs
well with `--since`: the deep tier over changed lines only is affordable on
every pull request. Statements whose deletion provably changes nothing are
never mutated: docstrings, `pass`, `...`, `global`/`nonlocal`, imports, a bare
name or literal on its own line, and a local binding with a pure right-hand
side that nothing in the function reads again.

Expect crash-kills: deleting a binding leaves everything downstream raising
`NameError`. Those are reported `KILLED_BY_ERROR` rather than `KILLED` so the
kill rate keeps meaning something — see the keyword table above.

The other three are the *function-interface* operators. Every other operator
mutates something inside an expression; these mutate the boundary between a
function and its callers, which is a class of bug nothing else reaches.

- `argument_swap` exchanges two adjacent positional arguments —
  `resize(width, height)` → `resize(height, width)`. Adjacent pairs only, so an
  n-argument call costs n-1 mutants. It skips a starred position and a pair
  that is identical as source, but it cannot tell whether two *different*
  arguments are interchangeable, so expect some equivalent mutants and retire
  them with `moonbuggy accept`.
- `default_arg` turns a `None` parameter default into `0` —
  `def fetch(url, timeout=None)` → `timeout=0`. Only `None`: an integer or
  boolean default is already mutated by `constant_int` and `constant_bool` in
  the default tier, and generating the same mutant twice under two ids helps
  nobody.
- `kwarg_drop` removes an explicit keyword argument so the callee's default
  applies — `connect(host, timeout=30)` → `connect(host)`. It asks whether the
  value you passed actually matters. Expect crash-kills where the parameter
  turns out to be required.

All three are `deep` rather than `default` because there is no evidence yet
about how many of their survivors are real gaps and how many are noise — the
bar `docs/writing-an-operator.md` sets before an operator joins the set every
run pays for.

### Options

Nothing below is required.

```
--project PATH       project root (default: cwd)
--output-dir DIR     the results directory -- results.jsonl, results.txt and
                     summary.json (default: .moonbuggy, relative to the
                     project root)
--timeout SECONDS    before a mutant is called TIMEOUT (default: 30)
--pytest-arg ARG     extra argument passed to every pytest run, including the
                     baseline and each mutant (repeatable). Needed when your
                     real test command is not bare pytest --
                     `--pytest-arg=--doctest-modules`, say
--flaky-probe N      extra unmutated suite runs used to detect flaky tests; a
                     test whose outcome varies makes every mutant it covers
                     SUSPICIOUS (default: 1, 0 disables)
--operators SELECTION
                     which operators to run. A comma-separated list of names
                     is an exact set (comparison_swap,boundary); a tier name
                     stands for its members (default, deep, all); a `+` prefix
                     adds to the default tier (+statement_deletion).
                     `moonbuggy operators` lists them all
--include FRAGMENT   only mutate paths containing FRAGMENT (repeatable)
--exclude FRAGMENT   skip paths containing FRAGMENT (repeatable)
--since REF          only mutate lines changed since a git ref, compared
                     against the merge base (e.g. --since origin/main)
--include-logging-mutants
                     run mutants inside logging calls instead of skipping them
--logger-name NAME   also treat NAME as a logger receiver (repeatable)
--accept-file PATH   the accepted-equivalents ledger
                     (default: .moonbuggy/accepted.toml)
--fail-on-unexplained
                     exit 1 only for findings that are neither killed nor
                     accepted
--jobs N             mutants to run concurrently (default: CPU count, or one
                     fewer with -n/--workers)
-n, --workers N      pytest-xdist workers per mutant run
--source DIR         directory to mutate, if discovery guesses wrong
--no-cache           ignore and do not update the cache
--clear-cache        delete the cache, then run
--quiet              summary line only
--json               print the run summary to stdout as one JSON object and
                     nothing else (it is written to .moonbuggy/summary.json
                     either way)
--report MODE        'human' for a readable report with diffs, 'agent' for
                     one grep-friendly line per mutant (default: human at a
                     terminal, agent when piped; MOONBUGGY_REPORT overrides)
--color WHEN         auto, always, or never (default: auto; NO_COLOR is
                     honoured)
--width N            wrap the human report to N columns (default: detected)
--no-progress        do not draw the live progress line
```

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev,bench]'
```

| command | what it checks |
|---|---|
| `make test` | fast unit suite |
| `make check-oracle` | every mutant against the hand-written oracle |
| `make check-fast-path` | the fast path against that oracle, serial and under xdist |
| `make check-pytest-args` | `--pytest-arg` reaches every run, and keys the cache |
| `make check-spike` | in-memory mutation, assert rewriting, xdist |
| `make check-cli` | end-to-end CLI runs against real pytest subprocesses |
| `make check-mutmut` | advisory cross-check of the oracle against mutmut |
| `make bench` | moonbuggy vs mutmut vs naive |
| `make check-fresh-install` | clean install, zero-config run |
| `make check-all` | all of the above |

The project under `tests/fixtures/sample_project` is input data, not tests of
moonbuggy — a small pytest project whose 29 mutants have hand-written expected
outcomes in `oracle.toml`. Some of its tests hang or fail by design once
mutated, which is why the outer suite excludes it.

## Status

Phase 0 and Phase 1 of [the acceptance criteria](docs/development/acceptance-criteria.md)
are implemented and all criteria are met. Speed numbers and the four measured
iterations behind them are in
[docs/benchmark-results.md](docs/benchmark-results.md).

Design notes: [spike A](docs/development/spike-a-findings.md) (in-memory mutation, xdist),
[spike B](docs/development/spike-b-findings.md) (coverage mechanism).
