# How fast is moonbuggy?

The short answer: **faster than the common alternatives, and honest about why.**
On the benchmark workload below, moonbuggy finishes mutation testing in
**0.5 seconds** — roughly **twice as fast as mutmut** and **nearly 40 times
faster than a naive re-run of your whole test suite per mutant**.

The longer answer is the point of this page: the number only means something
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
  no forkserver, no caching — the "obvious" way to do mutation testing

Every tool runs against its own fresh copy of the project, so none can benefit
from artifacts another tool left behind. Numbers are the median of three
consecutive runs, on one machine.

## The headline results

| tool | wall time | mutants | mutants/sec |
|---|---:|---:|---:|
| **moonbuggy** | **0.50s** | 84 | 168 |
| mutmut | 0.92s | 108 | 117 |
| naive baseline | 18.9s | 84 | 4.4 |

Read this table carefully, because the two ratios tell a different story:

- **vs. mutmut: 1.85x faster.** Three consecutive runs gave 1.89x, 1.85x,
  1.85x — stable, not a lucky single sample.
- **vs. the naive baseline: 38x faster.** Three runs: 38.2x, 38.0x, 37.9x.

The mutmut number deserves an honest caveat, and we want to be the ones to
give it to you: mutmut generates 24 more mutants than moonbuggy here, because
it implements a larger set of mutation operators. A tool that generates fewer
mutants *should* finish sooner. The wall-clock ratio is the primary claim, and
it has stopped pointing the way it used to point. But it is not a like-for-like
comparison of operator coverage.

The comparison against the naive baseline is the clean one. It runs the *same*
moonbuggy mutation operators — the tool that does less work would be doing
fewer mutants here, and it isn't. **Both tools produce exactly 84 mutants, with
identical status breakdowns. Nothing is pruned.** The 38x speedup is real and
it is not bought by quietly mutating less.

## What "twice as fast" means on a real suite

Put a number on it. On this workload, mutmut spends about a second mutating a
project whose 90 tests take real time to run. moonbuggy spends half that. On
the naive baseline — the way many teams first try mutation testing — the same
job takes **19 seconds** because every one of the 84 mutants re-runs the entire
suite from scratch.

That is the difference between running mutation testing on every push and
reserving it for a slow job. At 0.5 seconds, moonbuggy fits in your normal
test run. At 19 seconds with the naive approach, it is a separate, slower
stage you will be tempted to run rarely — which is exactly when the coverage
gaps it finds stop being caught in time.

## Where the speed comes from

moonbuggy is not fast by accident, and it was not fast on day one. Its first
recorded run took **10.1 seconds** — *twelve times slower than mutmut*. Every
step since came from measuring where the time actually went, not from guessing.
Four changes tell the story:

| change | wall time | why it helped |
|---|---:|---|
| baseline (fresh process per mutant) | 10.1s | this is where every tool starts |
| fork instead of a fresh process | 4.8s | the parent imports the test suite once; children inherit it |
| run mutants in parallel | 0.9s | each mutant is independent; the machine has 14 cores sitting idle |
| one warm session reused for everything | 0.73s | the coverage pass and the mutations were the same suite run, done twice |

That last row is the interesting one, and it is worth understanding because it
is the deeper idea:

**One warm process.** A single long-lived process runs your suite once, under
coverage. That single run does two jobs at once: it records which tests reach
which lines (the map that decides which tests to run for each mutant), and it
imports every test module. Then, for each mutant, the process *forks* a child
from that already-warm state. The work that used to be repeated identically
for every mutant — importing the same test modules again — is done once.

**In-place mutation.** A warm process has already imported the module under
test. For a test that did `from app.thing import compute`, the test holds the
function object directly, so an import hook can't help. moonbuggy swaps the
function's code object *in place*, changing what the test calls without any
re-import. This is what makes the warm process possible at all, not merely
cheaper: without it, a warm process couldn't mutate code that was already
imported.

Two mechanisms work together here, and each does half the job. When neither
applies — say a decorator has wrapped the function — moonbuggy refuses rather
than guesses. A mutation that quietly fails to apply would report a false
*survivor*, which looks exactly like a real finding and would send you chasing
a bug that isn't there. moonbuggy falls back to a cold, safe path instead.

## The honest caveats

A benchmark is a point on one machine, and we want the limits stated plainly:

- **One machine, one Python version.** Results are on a 14-core macOS machine
  running Python 3.12. Your mileage will depend on your hardware, your suite,
  and your Python.
- **The fast path is POSIX-only.** The warm-session, fork-based approach needs
  `fork()`. On Windows, moonbuggy falls back to the fresh-process-per-mutant
  path — the 10.1-second architecture. The speedups above are what you get on
  macOS and Linux.
- **Parallel workers use a separate path.** Distributed workers (`xdist`)
  need real subprocesses, so combining the parallel warm-session path with
  true parallelism is unexplored territory.

## Reproduce it yourself

Everything here is regenerated from the same benchmark in the repository.
Run `make bench`, and the numbers come from a deterministic workload and a
version-controlled fixture. If you want to see how any change to moonbuggy
moves the needle, the comparison harness (`make ab`) interleaves two different
versions of the tool and reports a confidence interval, so a headline that
moved can't be blamed on the machine being in a different mood that day.

The general lesson is worth carrying into any benchmark you read, including
ours: a number taken on one machine on one day tells you about that day. When
a ratio changes, ask whether the denominator moved too — we do.