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
| the coverage pass | 50% | 44% | 39% |
| running the selected tests | 15% | 26% | 26% |
| getting a process ready per mutant | 6% | 9% | 12% |
| flakiness probe | 0% | 0% | 0% |
| everything else | 29% | 21% | 23% |

Three useful conclusions:

- **The coverage pass is the largest phase on every shape.** It is one
  instrumented run of your suite, and it is what makes selection possible.
  Roughly half of it is coverage's own tracing rather than your tests, and
  every attempt to get that back has either cost more than it saved or made
  the line→test map unsafe — see [the register][perf-hypotheses].
- **Generation, reporting and cache I/O are together under 3%.** Optimising
  them is wasted effort, and the register has the measurements to prove it.
- **The bottleneck moves.** On a suite of fast tests almost nothing is your
  tests; on a suite of slow tests a quarter of the run is. Advice that ignores
  which of these you have is not advice.

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
place and runs its tests. Measured at roughly 8ms against 90ms.

The host does more than import. Anything every mutant would otherwise compute
identically is computed there once and inherited across the fork: the pytest
configuration, the module index the mutation is swapped into, the source text,
and a frozen heap the garbage collector will not re-walk.

The coverage pass and the host's warm-up are the same run, which is why the
table above has one "coverage pass" row and not two.

## Only what you changed

The fastest run is the one that skips almost everything. `--since` generates
mutants only for lines your branch has touched:

```console
$ moonbuggy --since origin/main
moonbuggy: Diff-scoped: only lines changed since origin/main (merge base 4f21c0a) were mutated -- 2 files, 31 lines.
moonbuggy: 7 mutants across 2 files
```

On a typical pull request that is a handful of mutants and seconds of runtime,
which is the difference between mutation testing as an audit you schedule and
mutation testing as a gate on every PR.

It is a **filter**, not a different tool. The mutants are the ones a full run
would have produced for those lines, with the same ids and the same verdicts —
so a scoped run fills and reads the *same cache* as a full one, and a mutant
already answered by last night's full run is not re-run here. `--since` is
deliberately not part of the [run fingerprint](#the-cache): how you reached a
mutant cannot change its answer.

It composes with `--include` and `--exclude` rather than replacing them, so
`--since origin/main --exclude generated/` means both.

### What is in scope

The diff is taken between the **merge base** of `<ref>` and your branch, and
your **working tree** — `git diff --unified=0 $(git merge-base <ref> HEAD)`.
Two consequences worth knowing:

- Commits that landed on `main` after you branched are not your changes and are
  not scoped in, which is what the merge base is for.
- Uncommitted edits *are* in scope, because the working tree is what moonbuggy
  mutates. A scope taken against `HEAD` would carry line numbers for a file
  that is not the one being read.

Untracked files are entirely in scope: a module you have just written is the
least-tested code in the tree, and `git diff` cannot see it. Deleted files and
deleted lines are in scope for nothing — there is nothing left to mutate.
Renames are scoped under the file's new path.

A branch that changed no source lines — a docs-only PR — exits `0` and writes
empty results, rather than failing a gate for having nothing to do.

### Reading a scoped report honestly

A scoped run says so twice, in the header and the footer:

```
moonbuggy  7 mutants across 2 files  (diff-scoped since origin/main)
...
1 survived, 6 killed in 4.2s -- 6/7 killed, 86%
Diff-scoped: only lines changed since origin/main (merge base 4f21c0a) were mutated -- 2 files, 31 lines.
Full records: .moonbuggy/results.jsonl
exit 1 -- survivors
```

That line is the point of the feature's honesty: `7/7 killed, 100%` on three
changed lines is not the same claim as a clean full run, and nothing should let
the two be confused.

### In CI

```yaml
- uses: actions/checkout@v5
  with:
    fetch-depth: 0          # --since needs the base branch and its history

- run: uv run --with moonbuggy moonbuggy --since origin/${{ github.base_ref }}
```

`fetch-depth: 0` is not optional. `actions/checkout` fetches a single commit by
default, which leaves `origin/main` either absent or with no merge base — and
moonbuggy exits `2` with a message saying so rather than quietly mutating
everything or nothing. The other exits-`2` cases are the same shape: not a git
repository at all, or a ref that does not resolve.

## Flags that matter

`--jobs N`
: How many mutants run at once. Defaults to the CPU count — one fewer
  under `-n/--workers`, where the parent process needs a core of its own.
  On a shared or
  containerised machine, set it explicitly — the default reads the host's CPU
  count, which in a container is often not the count you are allowed.

`--flaky-probe 0`
: Turns off the extra unmutated suite run used to detect flaky tests. It now
  saves close to nothing — the probe runs in its own process alongside the
  coverage pass, so it costs cores rather than wall clock — and it gives up
  the guarantee that a flaky test produces `SUSPICIOUS` rather than a
  confident wrong answer. Worth turning off only on a machine short of cores.
  See [What `SUSPICIOUS` means](reading-the-output.md).

`--include` / `--exclude`
: Restrict which files are mutated, by path fragment. Repeatable.

`--since REF`
: Mutate only the lines changed since a git ref, compared against the merge
  base. The fastest run is the one that mutates only what you changed — see
  [above](#only-what-you-changed).

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
key covers the mutant's identity, the full source of its module, the contents
of every test file selected for it, the `conftest.py` chain those test files
pull in, the mutated module's first-order imports (resolved statically to files
inside the project), and a fingerprint of the run itself — so editing one
module invalidates that module's mutants, editing a fixture invalidates every
mutant whose tests use it, and everything else keeps hitting.

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

The `conftest.py` chain and the first-order imports joined the key for the same
reason, and their cost in reuse was measured rather than assumed. The additions
are deliberately *shared* inputs: a `conftest.py` serves every test file under
it, and an imported helper serves every mutant in the module that imports it.
That would be a problem if it reset the key on each use — but the key hashes
their *bytes*, and those bytes are unchanged between two runs that changed
nothing. The steady-state hit rate is therefore exactly what it was: a clean
rerun still hits at the current rate (the doctest above asserts it). The only
time one of these inputs moves the key is when the file actually changed, which
is precisely when every verdict downstream of it has to be recomputed anyway.
Widening on a real edit is not lost reuse; it is the correctness the old key
was missing. What is *not* covered — transitive imports, `pytest.ini`, installed
dependency versions, and which tests inside an unchanged file were selected —
stays documented in `src/moonbuggy/cache.py`, under "What the key cannot see".

The run fingerprint is why changing the command line starts cold. It covers
`--pytest-arg` (in the order you gave them, because pytest's argument order is
meaningful), `--timeout`, and the interpreter running the tests. Each of those
can change a verdict from unchanged source: `--doctest-modules` adds tests that
did not exist, `-W error` turns a passing test into a failing one, a shorter
timeout turns a `KILLED` into a `TIMEOUT`, and another interpreter is another
set of installed packages. `-n/--workers` and `--jobs` are deliberately *not*
in it — they change how the work is spread across processes, not which tests
run or what they assert, so varying them keeps your cache.

`--no-cache` ignores it entirely; `--clear-cache` deletes it and starts cold.
Both are for measurement, not for daily use.

## Where the remaining time goes

Honestly: roughly a fifth of every run is process setup that could be removed by
running several mutants in one process. moonbuggy does not do that, because
restoring a mutated module between mutants is a step that can *partly* succeed,
and a partly-restored module means the next mutant is evaluated against the
wrong source and reported confidently. That trade is written up in the
[performance-hypothesis register][perf-hypotheses], along with everything else
that was tried and what it actually saved.

[perf-hypotheses]: https://github.com/jhamon/moonbuggy/blob/main/docs/development/perf-hypotheses.md
