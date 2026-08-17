# Spike A findings — in-memory mutation, pytest, and xdist

**Status:** resolved. Criteria B1 and B2 pass. Verified by `make check-spike`.

Section 4.2 of the design doc called this "the highest-uncertainty piece of the
whole design" and named three risks. It also cited a mutmut blog post
documenting an *abandoned* attempt at the same approach, so the possibility that
this simply would not work cleanly was live. Here is what each risk turned out
to be.

## Risk 1 — composing with pytest's assert-rewrite hook

**Expected:** a collision. Two meta path hooks competing to load the same
modules, with ours likely to displace pytest's and silently degrade every
assertion message in the run.

**Found:** not a collision at all, because the two hooks act on disjoint sets of
modules. pytest's hook rewrites test modules, conftest files, and registered
plugins. Ours rewrites the module under test. Neither is ever asked about the
other's files, so both sit on `sys.meta_path` and coexist without coordination.

**What keeps it that way:** `_MutationFinder` narrows to a single resolved file
path and returns `None` for everything else. A broader finder — one matching by
package, say — would start returning specs for test modules, take them away from
pytest's hook, and turn every rich assertion diff into a bare `AssertionError`.
The failure would be quiet: tests would still pass and fail correctly, only the
diagnostics would get worse.

That is why `test_assert_rewriting_still_produces_rich_output_under_mutation`
asserts on `+ where False = qualifies_for_bulk(10)`. That fragment is produced
only by assert rewriting; under `--assert=plain` it is absent. Checking that the
mutation was observed would pass just as well with rewriting broken.

## Risk 2 — xdist workers re-importing unmutated code

**Expected:** the serious one. Workers are separate processes that import from
disk independently, so a mutation applied in controller memory leaves workers
running original code and reporting a false `SURVIVED` — silent, and on a common
configuration.

**Found:** real, and avoided by never keeping the mutation in controller memory
in the first place. The active mutant's identity travels in an environment
variable. execnet gives each worker the controller's environment, and every
worker runs its own `pytest_configure`, so each installs the same mutation
independently before collection imports anything. No xdist hooks, no
serialisation of our own state, and the serial case is the same code path with
one process instead of three.

**The negative test matters more than the positive one.** A passing xdist test
proves nothing if it would also pass with propagation broken — the tests that
kill this mutant might simply have been assigned to the controller. So
`MOONBUGGY_SPIKE_CONTROLLER_ONLY` reproduces the bug on demand, and
`test_xdist_test_has_teeth` asserts the mutant *survives* when it is set. If that
test ever starts failing, the positive test has stopped detecting the failure
mode it exists for.

That environment variable is production code that exists only for a test, which
is a smell worth naming rather than hiding. The alternative — trusting an
unfalsifiable green test on the design's highest-severity silent-correctness
risk — seemed clearly worse.

## Risk 3 — linecache population

**Expected:** tracebacks quoting original source instead of mutated source.

**Found:** exactly as described, and handled in `install()` rather than deferred
to the reporting layer, because by the time a traceback is formatted the damage
is done. The entry is stored with `mtime` of `None`, which makes
`linecache.checkcache` treat it as not-read-from-a-file and leave it alone. A
real mtime would be compared against the unmutated file on disk and evicted
mid-run.

**A note on testing this**, since it took two wrong attempts. A traceback quotes
the line that *raised*. Mutating any other line in the module proves nothing —
the mutated text never appears in the traceback either way, so the test fails
while the mechanism works. It also cannot be tested through a failing assertion
in a test file, because that traceback never enters the module under test at
all. `tests/test_linecache.py` mutates the raising line directly, and since the
file on disk still holds the original text, mutated text in the traceback can
only have arrived via linecache.

## Consequences for Phase 1

- The design's recommendation of *function-level swap first, import hook as
  fallback* can be simplified. The import hook alone handles both
  function-scoped and module-level mutations, so Phase 1 starts with just the
  hook. Function-level swap is a later optimisation to be justified by profiling,
  not a correctness requirement.
- One mutant per process is now a load-bearing assumption, not just a
  convenience: it is what makes the xdist story work without any cross-process
  state. `uninstall_all()` exists for tests; it is not the production lifecycle.
- `_evict_already_imported` is what makes `install()` safe if the module under
  test was already imported. Without it a stale module object keeps its
  unmutated code and the mutation silently does nothing — the same false
  `SURVIVED` as the xdist bug, by a different route.

## Not covered by this spike

- Per-test timeout handling under xdist. The naive runner times out a whole
  suite run; the fast path will need finer granularity, since a hanging mutant
  under `-n 4` should not cost the full budget on every worker.
- Coverage-guided selection is Spike B and independent of everything here.
- `multiprocessing` inside code under test remains out of scope per 4.2.
