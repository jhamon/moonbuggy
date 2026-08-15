# Spike B findings — coverage mechanism for the line→test map

**Status:** benchmarked, decision recorded, one dependency open (see below).
Reproduce with `.venv/bin/python scripts/bench_coverage.py`.

Section 4.1 offered three candidates and expected `sys.monitoring` to win on
overhead, with `coverage.py` dynamic contexts as a possible reuse shortcut. The
numbers do not support that expectation.

## Numbers

Generated workload: 10 modules, 200 modules of real arithmetic, 200 tests.
Best of 3 runs, Python 3.12.13, macOS (Darwin 24.1.0).

| mechanism | wall | overhead | lines mapped | attributions |
|---|---|---|---|---|
| baseline (no instrumentation) | 0.17s | — | — | — |
| `sys.monitoring` (ours) | 0.79s | 4.65x | 1200 | 3600 |
| `coverage.py` contexts | 0.44s | 2.59x | 1200 | 3600 |

Both produce **identical maps**, which is the cross-check that makes the timings
comparable — a mechanism that is fast because it records less is not a candidate.

The workload is generated rather than reusing `sample_project`, which runs in
0.01s. Measuring instrumentation overhead against a suite that does no work
measures process startup and nothing else.

## Why the expectation was wrong

PEP 669 lowers the cost of *delivering* an event, but the callback is still a
Python function invoked per line. coverage.py's tracer is C. On a line-heavy
workload the per-event Python call dominates, and no amount of tuning on our
side closes a gap that is fundamentally interpreted-vs-compiled. Beating it
would mean writing a C tracer, which is the same trade the design already
rejected for a Rust rewrite (1.3): significant effort against a cost that is not
the actual bottleneck.

Note the coverage pass runs **once per session**, while mutant execution runs
once per mutant. Even a 4.65x coverage pass is a rounding error next to the
thing it enables. This decision is about maintenance cost as much as speed.

## The bug this spike found

The first run showed 12.52x overhead and a map containing exactly **one** file
where coverage.py found ten.

CPython compares and hashes code objects **by value** — name, flags, first line
number, bytecode, constants — and `co_filename` is *not* among them. The ten
generated modules were structurally identical, so `compute_0` in `mod_3` was
equal to and hashed the same as `compute_0` in `mod_0`. A cache keyed on the
code object merged all ten.

Both symptoms had the same cause. Coverage was misattributed, and every lookup
hit a hash collision that forced a full bytecode comparison — which is where
most of the 12.52x came from. Keying the cache on `co_filename`, the actual
input to the decision, fixed both: 4.65x and a correct map.

This is not a synthetic-workload artifact. In a real project it is two copies of
the same small helper in different modules: coverage for one is attributed to
the other, the wrong tests are selected for a mutant, and the result is a false
SURVIVED. No external symptom. Regression test:
`test_identical_functions_in_different_files_do_not_collide`.

Worth stating plainly: the benchmark found this, and it found it because it
checked map *content* and not just wall-clock. A timing-only benchmark would
have reported 12.52x and sent us tuning a correctness bug.

## Decision

**Reuse `coverage.py` dynamic contexts for the coverage pass** — 1.8x faster,
mature, and less of our own code to maintain on a foundational component.

**Conditional on one unresolved integration point.** coverage.py's
`dynamic_context = test_function` produces contexts like
`test_mod_0.test_0_0` — module-and-function form, not pytest node ids
(`tests/test_mod_0.py::test_0_0`). Selection has to hand node ids back to
pytest, so those strings need translating, and the translation is ambiguous
exactly where it matters: parametrized tests (`test_x[a]` vs `test_x[b]`), tests
in classes, and same-named tests in different files.

`pytest-cov`'s `--cov-context=test` is reported to record real node ids by
hooking pytest directly. **That has not been verified here** and is the next
action before this decision is final. If it does not hold, our collector wins on
correctness despite being slower, because it gets `item.nodeid` for free.

## Consequences

- `src/moonbuggy/linemap.py` and `covplugin.py` stay for now: they are the
  measured alternative, they are the fallback if the node-id question goes the
  other way, and they are the only mechanism with no third-party dependency.
  If coverage.py is confirmed, they should be deleted rather than kept as a
  dormant second implementation — two coverage mechanisms is a maintenance
  burden with no user-visible benefit.
- `coverage` moves from the `bench` extra to a real dependency if confirmed.
- The PEP 669 "disable lines already seen" optimisation does not apply to this
  pass either way. Returning `DISABLE` after first sighting stops the line being
  recorded for *later* tests, which is precisely the attribution the map exists
  to capture. It would make the pass faster and the results wrong.

## Not covered

- coverage.py 7.x can use `sys.monitoring` as its own core (`core=sysmon`).
  Not benchmarked; might narrow or widen the gap.
- Neither mechanism was measured under xdist, where the map has to be merged
  across worker processes.
- `sys.settrace`, the pre-3.12 fallback, was not benchmarked. The project
  requires 3.12+, so it is only relevant if that floor is ever lowered.
