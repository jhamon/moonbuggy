# moonbuggy

Fast, agent-first mutation testing for Python.

Mutation testing measures whether your tests would actually notice if the code
broke. It makes small changes to your source — flipping a `<` to a `<=`, a
`True` to a `False` — and reruns the tests. A change no test objects to is a
gap: a missing test, a weak assertion, or a line nothing exercises.

Two things make moonbuggy different:

- **Speed.** It runs only the tests that actually cover each mutated line,
  applies mutations in memory rather than writing files, runs mutants in
  parallel forked processes, and caches results across runs. On a suite where
  test execution dominates, that is **14x faster than the naive approach** of
  rerunning everything per mutant.
- **Output built for agents.** Results are JSON Lines, with a derived plaintext
  view whose every line starts with a fixed keyword, so `grep SURVIVED` works
  with no knowledge of the schema.

## Install

Requires Python 3.12+ and pytest.

```bash
pip install .
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

Exit code is `0` when nothing survived, `1` when there are survivors, and `2`
when the run could not start.

### Reading the output

Every plaintext line begins with one of exactly five keywords:

| keyword | meaning |
|---|---|
| `KILLED` | a test failed under the mutation — the good outcome |
| `SURVIVED` | every selected test passed — a gap, or an equivalent mutant |
| `TIMEOUT` | the mutation caused a hang, killed by the time budget |
| `SUSPICIOUS` | pytest could not complete; needs a look |
| `SKIPPED` | suppressed, or filtered out by configuration |

So the thing you usually want is:

```bash
grep SURVIVED .moonbuggy/results.txt
```

Each line carries `key=value` tokens, including `nearest_test=` — the test to
extend to close that gap.

Lines are one per mutant and never contain the diff, so they stay grep- and
awk-friendly. To see a mutant in full:

```bash
moonbuggy show 'shipping.py:5:comparison_swap:0'
```

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

### Options

Nothing below is required.

```
--timeout SECONDS    before a mutant is called TIMEOUT (default: 30)
--operators NAMES    comma-separated subset, e.g. comparison_swap,boundary
--include FRAGMENT   only mutate paths containing FRAGMENT (repeatable)
--exclude FRAGMENT   skip paths containing FRAGMENT (repeatable)
--jobs N             mutants to run concurrently (default: CPU count - 1)
-n, --workers N      pytest-xdist workers per mutant run
--source DIR         directory to mutate, if discovery guesses wrong
--no-cache           ignore and do not update the cache
--clear-cache        delete the cache, then run
--quiet              summary line only
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
| `make bench` | moonbuggy vs mutmut vs naive |
| `make check-fresh-install` | clean install, zero-config run |
| `make check-all` | all of the above |

The project under `tests/fixtures/sample_project` is input data, not tests of
moonbuggy — a small pytest project whose 22 mutants have hand-written expected
outcomes in `oracle.toml`. Some of its tests hang or fail by design once
mutated, which is why the outer suite excludes it.

## Status

Phase 0 and Phase 1 of [the acceptance criteria](docs/acceptance-criteria.md)
are implemented, with one criterion **not met**: moonbuggy does not beat mutmut
on wall clock (0.90x). It is 14x faster than the naive baseline, and the
remaining gap has a known cause — mutmut reuses a warm pytest process, while
moonbuggy pays collection inside every fork. See
[docs/benchmark-results.md](docs/benchmark-results.md).

Design notes: [spike A](docs/spike-a-findings.md) (in-memory mutation, xdist),
[spike B](docs/spike-b-findings.md) (coverage mechanism).
