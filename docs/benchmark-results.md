# Benchmark results (criteria G1–G4)

Reproduce with `make bench`. Python 3.12.13, Darwin 24.1.0, 14 CPUs, 8s timeout.

## G2 verdict: **MET** — 1.07x faster than mutmut

### Speed workload (generated; test execution dominates startup)

| tool | wall | mutants | mut/sec |
|---|---|---|---|
| **moonbuggy** | **0.73s** | 84 | 112 |
| mutmut | 0.80s | 108 | 134 |
| naive baseline | 13.0s | 84 | 6.4 |

- **vs mutmut: 1.07x — PASS.** Margin is thin, so it was checked for
  stability rather than taken from one lucky run: three consecutive runs gave
  1.12x, 1.08x, 1.08x. It passes consistently, but anyone reading this should
  treat "faster than mutmut" as *modestly* faster, not decisively so.
- **vs naive: 17.5x — PASS.** This is the design's own bar (§1.2).

mutmut remains ahead on raw throughput (134 vs 112 mut/sec) because it generates
24 more mutants from operators the MVP set does not implement. The wall-clock
comparison is the criterion, and it is not like-for-like in mutmut's favour.

### Fixture (sample_project)

Reported for completeness, not used for the verdict. Its suite runs in 0.01s, so
per-mutant cost is nearly all process startup plus one 8s timeout, and selection
has nothing to save. moonbuggy originally **tied** the naive baseline here
(10.42s vs 10.44s), which is what prompted building a workload where the speed
claim could be tested at all.

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

### The architecture that closed it

Two mechanisms, both anticipated by §4.2, working together:

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

## G3: is moonbuggy fast because it does less?

**No, and the check that shows it is not the mutmut comparison.** Counts against
mutmut cannot answer this, because the two implement different operator sets.

The naive baseline shares moonbuggy's operators exactly, so an equal count there
is the real test: **84 == 84**, with identical status breakdowns. Nothing is
pruned. The A2b inventory test independently proves every expected mutant is
generated, from labels written before the engine existed.

## G4: reproducibility

`make bench` regenerates every number. The workload comes from a deterministic
template; the fixture is version-controlled.

## Not covered

- Only one machine and one Python version.
- The warm-session path is POSIX-only. Windows falls back to subprocess-per-mutant,
  which is the 10.13s architecture.
- `-n/--workers` (xdist) still uses the cold path, since xdist needs real
  subprocesses. Combining the two is unexplored.
