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

Two files land in `.moonbuggy/`:

| file | role |
|---|---|
| `results.jsonl` | canonical, one JSON object per mutant |
| `results.txt` | plaintext view, derived from the JSONL |

Exit code is `0` when there are no findings, `1` when there are survivors or
lines no test reaches, and `2` when the run could not start.

### Reading the output

Every plaintext line begins with one of exactly six keywords:

| keyword | meaning |
|---|---|
| `KILLED` | a test failed under the mutation — the good outcome |
| `SURVIVED` | every selected test passed — a gap, or an equivalent mutant |
| `NO_COVERAGE` | no test executes the line at all, so nothing could object |
| `TIMEOUT` | the mutation caused a hang, killed by the time budget |
| `SUSPICIOUS` | pytest could not complete; needs a look |
| `SKIPPED` | suppressed, or filtered out by configuration |

`SURVIVED` and `NO_COVERAGE` are both findings and both exit `1`. They are
separate keywords because the fix is different: a survivor needs a stronger
assertion in a test that already runs, and a `NO_COVERAGE` line needs a test to
exist at all (or the code to go). Before 0.1.3 the second was reported as
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

### Accepting one you have already reviewed

A survivor you have reviewed and decided is equivalent goes in a ledger, so
neither you nor the next reviewer pays for that triage twice:

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

### Options

Nothing below is required.

```
--timeout SECONDS    before a mutant is called TIMEOUT (default: 30)
--operators NAMES    comma-separated subset, e.g. comparison_swap,boundary
--include FRAGMENT   only mutate paths containing FRAGMENT (repeatable)
--exclude FRAGMENT   skip paths containing FRAGMENT (repeatable)
--since REF          only mutate lines changed since a git ref, compared
                     against the merge base (e.g. --since origin/main)
--accept-file PATH   the accepted-equivalents ledger
                     (default: .moonbuggy/accepted.toml)
--fail-on-unexplained
                     exit 1 only for findings that are neither killed nor
                     accepted
--jobs N             mutants to run concurrently (default: CPU count - 1)
-n, --workers N      pytest-xdist workers per mutant run
--source DIR         directory to mutate, if discovery guesses wrong
--no-cache           ignore and do not update the cache
--clear-cache        delete the cache, then run
--quiet              summary line only
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
| `make check-spike` | in-memory mutation, assert rewriting, xdist |
| `make check-cli` | end-to-end CLI runs against real pytest subprocesses |
| `make check-mutmut` | advisory cross-check of the oracle against mutmut |
| `make bench` | moonbuggy vs mutmut vs naive |
| `make check-fresh-install` | clean install, zero-config run |
| `make check-all` | all of the above |

The project under `tests/fixtures/sample_project` is input data, not tests of
moonbuggy — a small pytest project whose 22 mutants have hand-written expected
outcomes in `oracle.toml`. Some of its tests hang or fail by design once
mutated, which is why the outer suite excludes it.

## Status

Phase 0 and Phase 1 of [the acceptance criteria](docs/development/acceptance-criteria.md)
are implemented and all criteria are met. Speed numbers and the four measured
iterations behind them are in
[docs/benchmark-results.md](docs/benchmark-results.md).

Design notes: [spike A](docs/development/spike-a-findings.md) (in-memory mutation, xdist),
[spike B](docs/development/spike-b-findings.md) (coverage mechanism).
