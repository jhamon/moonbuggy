# How fast is moonbuggy?

The short answer: on the benchmark workload below, moonbuggy finishes mutation
testing in **about half a second** — roughly **1.8 times as fast as mutmut** and
**more than 40 times faster** than a naive re-run of your whole test suite per
mutant.

The longer answer is the point of this page: a number only means something
if you understand what was measured, on what machine, and what moonbuggy is
and is not claiming. This page gives you all three.

## What we measured

A benchmark is only as good as the workload it runs on. Ours is generated to
have the shape of a real, non-trivial test suite:

- **Three modules** of application code, each with four functions that do real
  arithmetic work.
- **Ninety tests** across the three modules, each exercising one function with
  a real loop of six thousand iterations.
- Each mutable line of code is covered by only a few of those tests.

That last point is the load-bearing one. Coverage-guided mutation testing —
running only the tests that exercise a given mutated line — only pays off when
test *execution* is the dominant cost, not process startup. A workload where
every test is instant would never show the speed difference. This one is built
so the tests actually do work, which is the shape of the real suites moonbuggy
is aimed at.

The benchmark compares three tools against the exact same project:

- **moonbuggy**, with coverage-guided test selection
- **mutmut**, a widely-used open-source mutation testing tool
- **a naive baseline**: run the entire suite once per mutant, no selection,
  no forkserver, no caching — the obvious way to do mutation testing

Every tool runs against its own fresh copy of the project, so none can benefit
from artifacts another tool left behind. Numbers are the median of three
consecutive runs, on a single machine.

## The headline results

| tool | wall time | mutants | mutants/sec |
|---|---:|---:|---:|
| **moonbuggy** | **0.56s** | 96 | 172 |
| mutmut | 1.02s | 108 | 106 |
| naive baseline | 24.3s | 96 | 4.0 |

Read this table carefully, because the two ratios tell a different story:

- **vs. mutmut: 1.8x faster.** Three consecutive runs gave 2.00x, 1.84x and
  1.83x — stable, not a lucky single sample.
- **vs. the naive baseline: 43x faster.** Three runs: 42.9x, 43.5x and 43.4x.

The comparison against mutmut is worth a word of context. The two tools do not
generate identical mutant sets: mutmut implements a larger set of mutation
operators, so it turns this project into **108** mutants to moonbuggy's 96. The
wall-clock ratio between the two tools therefore reflects a combination of
speed and operator coverage, not a like-for-like comparison of per-mutant
speed. So we do not treat that ratio as a clean measure — we lead instead with
the number that is clean.

The clean comparison is against the naive baseline, which runs the *same*
moonbuggy mutation operators. **Both produce exactly 96 mutants, with
identical status breakdowns. Nothing is pruned.** The 43x speedup is real, and
it is not bought by quietly mutating less.

## And on a real project?

Everything above runs on a generated workload plus a project we wrote ourselves
— the right tools for measuring how the engine behaves, but subject to a fair
objection: a benchmark that proves how fast we are is measured against code we
chose. So there is a second, public-facing benchmark that runs the same three
tools against a real, widely-used open-source project pinned to a fixed commit,
so anyone can reproduce it and no one has to wonder whether we hand-picked the
subject.

**Subject: more-itertools v11.1.0** at commit `64be96ce`. Scope: mutate
`more_itertools/recipes.py` (the itertools-recipes module) and run its dedicated
test file `tests/test_recipes.py` (140 tests, ~10,000 parameterised subtests).
All three tools see the same scope and the same test selection. Reproduce with
`make bench-real`.

| tool | wall time | mutants | mutants/sec |
|---|---:|---:|---:|
| **moonbuggy** | **234s** | 381 | 1.6 |
| mutmut | 426s | 1085 | 2.5 |
| naive baseline | 3750s | 381 | 0.1 |

The gap is not a workload we shaped to flatter the tool — it is what selection,
warm-forking and in-place mutation are *for*. On this real module, the naive
baseline took **over an hour** (it re-runs the whole test file once per mutant)
and mutmut took **7 minutes**; moonbuggy finished in **under four minutes**.
That is the honest cost that selection exists to remove.

Three things this table asks you to keep in view, because each is why the table
is the shape it is:

- **The comparison that cannot be gamed is against the naive baseline.** It runs
  moonbuggy's exact mutation operators, so 381 == 381, with identical status
  breakdowns — nothing is pruned. That is the speed claim, and it is
  like-for-like.
- **mutmut's count is not 381.** mutmut implements a larger operator set, so on
  the same file it yields 1085 mutants to moonbuggy's 381. The wall-clock
  comparison against mutmut is therefore speed-plus-operator-coverage, exactly
  like the synthetic table above; we do not lead with it.
- **The scope is a bounded slice, not the whole library.** The naive baseline
  re-runs the selected tests per mutant, so a bigger scope would scale that
  cost rather than change this ratio. The point is the *shape* of the gap on
  real code.

This benchmark is deliberately much slower than `make bench` — running it takes
about an hour, almost all of it the naive baseline. That slowness is the
credibility point, not a defect.

## What "half a second" actually buys you

Put a number on it. On this workload, mutmut spends about a second mutating a
project whose 90 tests take real time to run. moonbuggy spends half that. On
the naive baseline — the way many teams first try mutation testing — the same
job takes **24 seconds**, because every one of the 96 mutants re-runs the
entire suite from scratch.

That is the difference between running mutation testing on every push and
reserving it for a slow job. At half a second, moonbuggy fits in your normal
test run. At 24 seconds with the naive approach, it is a separate, slower
stage you will be tempted to run rarely — which is exactly when the coverage
gaps it finds stop being caught in time.

## Where the speed comes from

moonbuggy gets its speed from one central design choice. A single long-lived
process runs your suite exactly once, under coverage, and records which tests
reach which lines — importing every test module at the same time. For each
mutant, that already-warm process forks a child instead of starting a fresh
interpreter. The work that used to be repeated identically for every mutant,
importing the same test modules and re-mapping the suite over and over, is now
done once. That single change, plus running only the tests that reach a given
mutated line, is where the time comes out. It was not fast on day one — its
first recorded run took **10.1 seconds** — and every improvement since came
from measuring where the time actually went, not from guessing.

## How far the project has come

The headline is one workload. The deeper claim is that the approach scales
across the shapes real suites come in, and that is where moonbuggy improved
the most. Over four development rounds, on the same machine and the same three
workload shapes, wall time fell on every single one of them:

| workload shape | first round | after four rounds | speedup |
|---|---:|---:|:--:|
| many tiny tests, nearly instant each | 0.554s | 0.302s | **1.8x** |
| fewer tests, each doing real work | 0.989s | 0.415s | **2.4x** |
| a large number of small files | 1.534s | 0.478s | **3.2x** |

Each shape stresses a different part of the engine, and each one got faster:
the nearly-instant-test shape is dominated by process startup, so it was the
warm-fork change that won there; the real-work shape is dominated by test
execution, so it was the coverage-guided selection that won there; the
many-small-files shape is dominated by discovery and collection, so it was the
file-handling work that won there. No round regressed a shape it was not
aiming at — every improvement on one held on the other two.

The honest caveats are the same ones that keep the numbers honest. You can
reproduce every figure here from the repository, and you should treat anything
you read as a snapshot of one machine, one day — including ours.

## The honest caveats

- **One machine, one Python version.** Results are on a 14-core macOS machine
  running Python 3.12. Your mileage will depend on your hardware, your suite,
  and your Python.
- **The fast path is POSIX-only.** The warm-session, fork-based approach needs
  `fork()`. On Windows, moonbuggy falls back to the fresh-process-per-mutant
  path. The speedups above are what you get on macOS and Linux.
- **Parallel workers use a separate path.** Distributed workers (`xdist`)
  need real subprocesses, so combining the parallel warm-session path with
  true parallelism is unexplored territory.

## Reproduce it yourself

Everything here is regenerated from the same benchmark in the repository. Run
`make bench`, and the numbers come from a deterministic workload and a
version-controlled fixture. The real-project table is regenerated the same way
with `make bench-real`, from a real open-source project pinned to a fixed commit
— so both tables are reproducible, and neither is a number we typed by hand. If
you want to see how any change to moonbuggy moves the needle, the comparison
harness (`make ab`) interleaves two different versions of the tool and reports a
confidence interval, so a headline that moved can't be blamed on the machine
being in a different mood that day.

The general lesson is worth carrying into any benchmark you read, including
ours: a number taken on one machine on one day tells you about that day. When
a ratio changes, ask whether the denominator moved too — we do.