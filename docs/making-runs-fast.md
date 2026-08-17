# Making runs fast

**Audience:** your codebase is large enough that "run the whole suite once per
mutant" is not a plan.

Mutation testing is *N* test runs for *N* mutants. The naive version of that is
unusable on anything real: moonbuggy's own benchmark workload has 84 mutants and
a suite that takes 0.15 seconds, and the naive approach takes 13 seconds. The
whole design is about making that number smaller without making any status
wrong.

## What actually costs time

Measured, not guessed. `make profile` produces this breakdown, and the numbers
below are its median over five runs on three workload shapes:

| phase | fast tests | slow tests | many files |
|---|---:|---:|---:|
| running the selected tests | 35% | 38% | 44% |
| getting a process ready per mutant | — | 22% | 25% |
| the coverage pass | 44% | 25% | 17% |
| flakiness probe | — | 5% | 4% |
| everything else | 21% | 10% | 10% |

Two useful conclusions:

- **Most of a run is the tests themselves plus process handling.** Generation,
  reporting and cache I/O are together under 2%. Optimising them is wasted
  effort, and the [register of attempts][perf-hypotheses] has the measurements
  to prove it.
- **The bottleneck moves.** On a suite of fast tests, the single instrumented
  coverage pass is the largest phase. On a suite of slow tests, it is the tests.
  Advice that ignores which of these you have is not advice.

## The two levers

### Coverage-guided selection

A mutation on line 14 can only be caught by a test that executes line 14. So
moonbuggy runs the suite once under coverage, records which tests touched which
lines, and then runs each mutant against only its covering tests.

On the benchmark workload this is the difference between 84 × 90 test executions
and 84 × 3.

You do not configure this; it is how moonbuggy works. What is worth knowing is
when it does *not* help:

- **A test that touches everything.** An end-to-end test that exercises the
  whole system is selected for nearly every mutant, so nearly every mutant pays
  for it. Fast unit tests plus a few slow integration tests is the shape that
  benefits most.
- **Module-level code.** A line that runs at import time is attributed to no
  test, so moonbuggy widens selection for it to the whole suite. It has to:
  running nothing would report a survivor that is an artefact of bookkeeping.
  Module-level constants and configuration are therefore the most expensive
  mutants you have.

### The warm session

Forking a process is milliseconds. Starting a Python interpreter, importing
pytest, importing your test modules and collecting them is more like 90
milliseconds — per mutant.

So moonbuggy runs the suite once in a *host* process, and then forks one child
per mutant from that host, where every test module is already imported and every
assertion already rewritten. The child mutates the already-imported module in
place and runs its tests. Measured at roughly 12ms against 90ms.

The coverage pass and the host's warm-up are the same run, which is why the
table above has one "coverage pass" row and not two.

## Flags that matter

`--jobs N`
: How many mutants run at once. Defaults to the CPU count. On a shared or
  containerised machine, set it explicitly — the default reads the host's CPU
  count, which in a container is often not the count you are allowed.

`--flaky-probe 0`
: Turns off the extra unmutated suite run used to detect flaky tests. Saves
  4–5% of wall clock, and gives up the guarantee that a flaky test produces
  `SUSPICIOUS` rather than a confident wrong answer. Reasonable on a suite you
  know is deterministic. See [What `SUSPICIOUS` means](reading-the-output.md).

`--include` / `--exclude`
: Restrict which files are mutated, by path fragment. Repeatable. The fastest
  run is the one that mutates only what you changed.

`--timeout N`
: Seconds before a mutant is called `TIMEOUT`. Mutations that produce infinite
  loops are common — `n += 1` becoming `n -= 1` in a `while` — and each one
  costs the full timeout. If your tests are fast, lowering this from the default
  30 seconds is usually free.

`-n N`
: pytest-xdist workers *within* each mutant run. Almost always the wrong lever:
  it opts out of the warm session, so every mutant pays full process startup
  again. `--jobs` parallelises across mutants and is what you want.

## The cache

A second run only re-runs mutants whose outcome could have changed. The cache
key covers the mutant's identity, the full source of its module, and the
contents of every test file selected for it — so editing one module invalidates
that module's mutants and nothing else.

```{doctest}
>>> project = make_project({
...     "lib.py": "def total(values):\n    running = 0\n    for value in values:\n        running += value\n    return running\n",
...     "test_lib.py": "from lib import total\n\ndef test_sums():\n    assert total([1, 2, 3]) == 6\n",
... })
>>> first = moonbuggy(cwd=project)
>>> "cached=0" in first.stderr
True
>>> second = moonbuggy(cwd=project)
>>> int(second.stderr.split("cached=")[1].split()[0]) > 0
True
```

The key is deliberately coarser than it could be: it hashes the whole module
rather than the mutated function, because a mutant's behaviour can depend on
anything else in its module. A stale hit is much worse than a miss — it would
report a gap you have already closed — so the cache errs toward re-running.

`--no-cache` ignores it entirely; `--clear-cache` deletes it and starts cold.
Both are for measurement, not for daily use.

## Where the remaining time goes

Honestly: roughly a fifth of every run is process setup that could be removed by
running several mutants in one process. moonbuggy does not do that, because
restoring a mutated module between mutants is a step that can *partly* succeed,
and a partly-restored module means the next mutant is evaluated against the
wrong source and reported confidently. That trade is written up as H1 in the
[performance-hypothesis register][perf-hypotheses], along with everything else
that was tried and what it actually saved.

[perf-hypotheses]: https://github.com/jhamon/moonbuggy/blob/main/docs/development/perf-hypotheses.md
