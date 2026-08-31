# Benchmark results

Reproduce with `make bench`. Python 3.12.13, Darwin 24.1.0, 14 CPUs, 8s timeout.

## Verdict: 1.85x faster than mutmut

### Speed workload (generated; test execution dominates startup)

Median of three consecutive runs.

| tool | wall | mutants | mut/sec |
|---|---|---|---|
| **moonbuggy** | **0.50s** | 84 | 168 |
| mutmut | 0.92s | 108 | 117 |
| naive baseline | 18.9s | 84 | 4.4 |

- **vs mutmut: 1.85x — PASS.** Three consecutive runs gave 1.89x, 1.85x,
  1.85x.
- **vs naive: 38.0x — PASS.** Three consecutive runs gave 38.2x, 38.0x,
  37.9x. This is the improvement the design sets out to demonstrate.

moonbuggy is now ahead on raw throughput as well (168 vs 117 mut/sec), which it
was not at the previous recording. That is worth stating plainly rather than
celebrating: mutmut generates 24 more mutants from operators the MVP set does
not implement, so the comparison is still not like-for-like, and a tool doing
less work per run should be expected to finish sooner. The wall-clock figure is
the criterion; the throughput figure is the honest caveat on it, and it has
stopped pointing the other way.

### Do not subtract these ratios from the previous ones

This table previously read 1.49x against mutmut and 30.1x against naive, and
1.07x / 17.5x before that. The latest tuning round is part of why the numbers
moved, but **it is not the whole of why.** Every tool in the comparison
moved:

| tool | two recordings ago | previous recording | now |
|---|---:|---:|---:|
| moonbuggy | 0.73s | 0.66s | 0.50s |
| mutmut | 0.80s | 0.99s | 0.92s |
| naive baseline | 13.0s | 19.9s | 18.9s |

Between the last two recordings moonbuggy got 24% faster on this workload while
mutmut got 7% faster and the naive baseline 5% faster, on a machine in a
different state again. The controlled figure for what this round actually
changed is the interleaved A/B comparison — **1.12x, 1.20x and 1.30x on
the three shapes** — not the movement in this table. Neither mutmut nor
`naive.py` shares a single line with anything in that round touched: `naive.py` is a
`subprocess.run` per mutant and reaches none of the forkserver, codeswap or
coverage-pass code that changed.

So:

- **The ratios above are honest as ratios.** All three tools are measured in
  one session on one machine, and they are stable across three consecutive
  runs.
- **The ratios are not a controlled measurement of the earlier tuning round.** For
  that, use `make ab`, which interleaves two git refs and reports a bootstrap
  interval: it puts the round at **1.29x (fast-tests), 1.59x (slow-tests) and
  1.89x (many-files)**. That is the number to quote for "how much did this
  round help".

The general point is the one `ab_compare.py` exists to enforce: a comparison
taken on one machine on one day tells you about that day, and a headline ratio
that moved is not evidence about your code until you know whether the
denominator moved too.

### Fixture (sample_project)

Reported for completeness, not used for the verdict. Its suite runs in 0.01s, so
per-mutant cost is nearly all process startup plus one 8s timeout, and selection
has nothing to save. moonbuggy originally **tied** the naive baseline here
(10.42s vs 10.44s), which is what prompted building a workload where the speed
claim could be tested at all.

| tool | wall | mutants |
|---|---|---|
| moonbuggy | 8.46s | 22 |
| mutmut | 15.61s | 26 |
| naive baseline | 11.79s | 22 |

Most of moonbuggy's 8.46s is the single 8s timeout the fixture contains
deliberately, which is why this table says almost nothing about speed.

## How it got there — four measurements, 14x

The first run had moonbuggy at 10.13s, **12x slower than mutmut**. Every step
after that came from measuring rather than guessing, and one guess was wrong
enough to be worth recording.

| change | wall | why |
|---|---|---|
| baseline (subprocess per mutant) | 10.13s | — |
| fork instead of spawn | 4.80s | parent imports pytest once; children inherit it |
| fork in parallel | 0.90s | mutants are independent; serial forking idled 13 cores |
| single warm session | 0.73s | coverage pass and warm-up were the same suite run, done twice |

**The wrong guess.** After parallelising, I assumed the remaining ~139ms per
mutant was `pytest.main()` overhead and spent two changes trying to trim it
(`-p no:cov`, `--assert=plain`). Both measured as noise. Timing `pytest.main()`
directly in a warm process gave **12ms**, not 139ms — so the cost was never
pytest's session, it was importing the test modules that each fresh fork had to
redo. Profiling the phases then showed the coverage pass alone was 34% of the
run. Two changes wasted; the lesson is that the profile was cheap and I should
have taken it first.

> **`--assert=plain` was later adopted, and this paragraph still stands.** It
> now measures as a real 1.03–1.10x. Nothing here was wrong: the flag *was*
> noise in the architecture it was tried in, where 139ms of per-mutant test
> imports dwarfed it. Once the warm session removed those imports, the same
> flag was 26% of what remained. A change rejected against one architecture is
> not rejected against its successor — which is an argument for keeping
> rejections on the record with their reasoning attached, rather than only
> their verdict.

## The second round

A later round of six changes, four adopted, is recorded in the repository's
development notes rather than published here, since it is a development record. Its
finding, in one line: most of what the profile called "per-mutant fork" was
not `fork()` but
work repeated identically inside every grandchild that the warm host could do
once beforehand. Measured by interleaved A/B at **1.29x on fast-tests, 1.59x on
slow-tests and 1.89x on many-files**, with per-mutant fork falling from
21.5–24.6% of the run to 6.7–8.8%.

### The architecture that closed it

Two mechanisms working together:

1. **A single warm session.** One forked host runs the suite *under coverage* —
   which simultaneously builds the line→test map and imports every test module.
   The parent reads the coverage data, plans which tests each mutant needs, and
   sends the jobs back. The host forks a grandchild per mutant from that warm
   state. Previously the coverage pass and the warm-up were two separate full
   runs of the same suite.
2. **In-place mutation** (`codeswap.py`). A warm process has already imported
   the module under test, so the import hook cannot help — a test that did
   `from app.thing import compute` holds the function object directly. Swapping
   that object's `__code__` changes what the test calls with no re-import. This
   is the design doc's "function-level swap", and it turns out to be what makes
   a warm process *possible*, not merely cheaper. Module-level mutations use a
   second mechanism: exec the mutated statement in the module's `__dict__`,
   which works because functions read globals dynamically at call time.

Where neither applies — a decorator has replaced the function object, say —
`codeswap` raises rather than guessing, and the whole batch falls back to cold
forks with the import hook. A mutation that quietly fails to apply reports a
false SURVIVED, which is indistinguishable from a real finding.

## Is moonbuggy fast because it does less?

**No, and the check that shows it is not the mutmut comparison.** Counts against
mutmut cannot answer this, because the two implement different operator sets.

The naive baseline shares moonbuggy's operators exactly, so an equal count there
is the real test: **84 == 84**, with identical status breakdowns. Nothing is
pruned. An inventory test independently proves every expected mutant is generated,
from labels written before the engine existed.

## Reproducibility

`make bench` regenerates every number. The workload comes from a deterministic
template; the fixture is version-controlled.

## Not covered

- Only one machine and one Python version.
- The warm-session path is POSIX-only. Windows falls back to subprocess-per-mutant,
  which is the 10.13s architecture.
- `-n/--workers` (xdist) still uses the cold path, since xdist needs real
  subprocesses. Combining the two is unexplored.
