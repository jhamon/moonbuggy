# Benchmark results (criteria G1–G4)

Reproduce with `make bench`. Numbers below: Python 3.12.13, Darwin 24.1.0,
14 CPUs, 8s timeout.

## G2 verdict: **NOT MET**

moonbuggy does not beat mutmut on wall clock. It is close, and the gap has a
known cause, but the criterion as written is not satisfied and should not be
reported as satisfied.

### Speed workload (generated; test execution dominates startup)

| tool | wall | mutants | mut/sec | breakdown |
|---|---|---|---|---|
| moonbuggy | 0.90s | 84 | 93.7 | KILLED=12 SURVIVED=72 |
| mutmut | 0.81s | 108 | 133.1 | KILLED=36 SURVIVED=72 |
| naive baseline | 13.1s | 84 | 6.4 | KILLED=12 SURVIVED=72 |

- **vs mutmut: 0.90x — FAIL.** Also behind on normalised throughput (94 vs 133
  mutants/sec), so this is not an artifact of mutant counts.
- **vs naive: 14.1x — PASS.** This is the design's own stated bar (§1.2: "fast
  is measured relative to naive mutation testing"), and the coverage-guided
  selection lever works exactly as intended. But the criterion Jen selected was
  beat-mutmut, and that is the one that counts.

### Fixture (sample_project)

| tool | wall | mutants | mut/sec |
|---|---|---|---|
| moonbuggy | 2.1s | 22 | 10.4 |
| mutmut | 15.5s | 26 | 1.7 |
| naive baseline | 10.4s | 22 | 2.1 |

Not the basis of the verdict. The fixture's suite runs in 0.01s, so per-mutant
cost is almost entirely process startup plus one 8s timeout, and selection has
nothing to save. moonbuggy originally **tied** the naive baseline here
(10.42s vs 10.44s), which is what prompted building a workload where the speed
claim could be tested at all.

## What was done about it

The first measurement had moonbuggy at 10.13s, **12x slower than mutmut**.
Selection was working; the per-mutant `python -m pytest` subprocess was the
entire cost — roughly 120ms of interpreter startup, pytest import and collection
against a few milliseconds of actual test execution. We were measuring process
creation.

Two changes, 11x total:

1. **Fork instead of spawn** (10.13s → 4.80s). The parent imports pytest once
   and nothing else; each child inherits it, applies its mutation and runs its
   own tests. The parent must never import the module under test — every child
   would inherit an unmutated copy and mutations would silently do nothing,
   which is the same false-SURVIVED failure mode as the xdist bug by a third
   route.
2. **Fork in parallel** (4.80s → 0.90s). Mutants are independent by
   construction, so they need no coordination. Measured at 13, 20 and 28
   concurrent jobs: 0.93s, 0.90s, 0.89s. Saturated.

## Why the gap remains

mutmut reuses a **warm pytest process**. It generates all mutants up front as
switchable branches guarded by an environment variable, then runs tests in a
session that is already imported and collected. Its per-mutant cost is close to
the cost of the tests themselves.

moonbuggy still pays `pytest.main()` collection inside every fork. That is the
whole of the remaining difference, and concurrency cannot hide it.

Closing it means adopting the same warm-process architecture: collect once, then
apply mutations to an already-collected session. That is a real design change,
not a tuning pass, and it interacts with the in-memory import hook — the module
under test would already be imported by the time a mutation is applied, which is
precisely the situation `_evict_already_imported` exists to handle and which the
current one-mutant-per-process model was chosen to avoid.

Worth noting the design doc did not anticipate this. §4.3 lists per-mutant
process cost nowhere; it treats coverage-guided selection and in-memory mutation
as the two levers. Both are implemented and both work — selection alone is worth
14x — but on suites with fast tests, process overhead dominates both.

## G3: mutant counts

moonbuggy 84, mutmut 108, on the same source. The 24-mutant difference is the
deliberately narrow MVP operator set (§3.2), not a benchmark trick — but it does
mean the wall-clock comparison is not like-for-like, which is why mut/sec is
reported and why moonbuggy loses on that too.

moonbuggy and the naive baseline share an operator set exactly (84 each,
identical status breakdowns), so that comparison isolates the selection lever
with nothing else varying.

## G4: reproducibility

`make bench` regenerates every number here. The workload is generated from a
seed-free deterministic template, so it does not drift; the fixture is
version-controlled.
