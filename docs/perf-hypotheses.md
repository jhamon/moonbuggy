# Performance hypotheses (milestone M2.2)

**Rule:** every entry states its predicted saving *before* the change is
attempted, and records the actual saving afterwards — including for attempts
that were abandoned. Wrong predictions stay in this document. The record of
what did *not* work is the part that compounds; deleting it leaves a document
that says every idea was good, which is both useless and false.

**Why the rule exists.** [benchmark-results.md](benchmark-results.md) records
two Phase 1 changes implemented on a hunch and measured as noise, with a note
that the profile which would have prevented it was cheap. So: profile first
(`make profile`), predict in writing, then measure with `make ab`, which
declines to call a winner when the confidence intervals overlap.

---

## The profile these are ranked against

`make profile`, commit `d04a777`, five runs per shape, median. Shares of total
wall clock including interpreter startup.

| phase | fast-tests | slow-tests | many-files |
|---|---:|---:|---:|
| in-child test execution | 34.9% | 38.1% | 43.8% |
| per-mutant fork | (see note) | 21.5% | 24.6% |
| coverage pass | 44.2% | 24.8% | 17.1% |
| flaky probe | — | 5.2% | 3.6% |
| interpreter startup | 8.7% | 4.8% | 3.1% |
| parent warm-up | 6.6% | 3.7% | 2.4% |
| planning | 3.8% | 2.1% | 2.5% |
| generation | 0.5% | 0.3% | 1.1% |
| reporting, discovery, cache I/O | <0.5% | <0.5% | <0.5% |
| **unattributed** | **0.5%** | **-0.7%** | **1.4%** |

Absolute medians: fast-tests 0.51s / 84 mutants, slow-tests 0.94s / 84 mutants,
many-files 1.47s / 560 mutants.

Two things the table is being honest about:

- The slow-tests column sums to slightly over 100%. Each phase is the median
  across five runs taken independently, so the columns are medians of medians
  rather than a decomposition of any single run. The error is well under 1% and
  it is left visible rather than normalised away.
- The fast-tests fork/execution split was measured before the split itself was
  fixed (it was collapsing everything into execution), so that one cell is not
  quoted. The other two columns are from the corrected profiler.

**What the profile says, in one line:** the money is in per-mutant work — fork
plus in-child execution is 60–68% of the run — and the coverage pass is the
second-largest single phase. Everything else is small enough that a *perfect*
fix would be invisible.

---

## Register, ranked by predicted saving

Predicted saving is of total wall clock on the shape named, before anything was
tried. Rank order is the order work proceeds in (M2.2.2); a departure is
recorded in the entry.

### H1 — Batch several mutants per grandchild fork

- **Phase:** per-mutant fork (21.5–24.6%)
- **Measured cost of that phase:** 0.20s of 0.94s (slow-tests), 0.36s of 1.47s
  (many-files)
- **Predicted saving:** 12–15% on many-files, 8% on slow-tests
- **Correctness risk:** **high**. Requires restoring a mutated module between
  mutants inside one process. A restore that is subtly incomplete leaves the
  previous mutation in place, and the next mutant is then evaluated against the
  wrong source — reported as a confident status, indistinguishable from a real
  one. This is the exact failure mode the whole design is organised around.
- **Status:** deferred, by documented override of rank order (M2.2.2). The
  predicted saving is the largest on the board and the risk is the only one in
  the register that can produce a wrong status rather than a slow run. Revisit
  only with a mechanism that can *prove* a restore was complete.
- **Actual saving:** not attempted.

### H2 — Faster coverage core (`COVERAGE_CORE=sysmon`)

- **Phase:** coverage pass (17.1–44.2%)
- **Measured cost of that phase:** 0.23s of 0.51s (fast-tests), 0.23s of 0.94s
  (slow-tests)
- **Predicted saving:** 8–12% on fast-tests, 5–8% on slow-tests
- **Correctness risk:** medium. `sys.monitoring` is a different measurement
  backend; if its per-test contexts differ at all from the trace backend's, the
  line→test map changes and selection changes with it. Gated by
  `make check-oracle`, which compares every fixture mutant against hand-written
  labels.
- **Actual saving:** _pending_

### H3 — Drop the parent's `import pytest` on the warm-session path

- **Phase:** parent warm-up (2.4–6.6%)
- **Measured cost of that phase:** 0.034s, near-constant across all shapes
- **Predicted saving:** 5–6% on fast-tests, 3% on slow-tests, 2% on many-files
- **Rationale:** `forkserver.warm_up()` imports pytest in the parent so that
  forked children inherit it. On the warm-session path the host imports pytest
  itself anyway, and the parent never runs a test — so the parent's import may
  be paid for nothing. If so it is a free 34ms.
- **Correctness risk:** low. The cold fallback path still needs it, so the
  question is only whether it can be moved rather than removed.
- **Actual saving:** _pending_

### H4 — Probe only the tests some mutant actually selects

- **Phase:** flaky probe (3.6–5.2%)
- **Measured cost of that phase:** 0.049s of 0.94s (slow-tests)
- **Predicted saving:** 2–3% on slow-tests, 1% elsewhere
- **Rationale:** the probe re-runs the whole suite to find flaky tests, but a
  flaky test only matters if some mutant selects it. On a project where
  selection is narrow, most of the probe is measuring tests whose stability
  nothing will depend on.
- **Correctness risk:** low, but it narrows the M1.4.3 guarantee: a test that
  becomes selected on a *later* run would not have been probed on this one.
  Since the cache is keyed on test file contents, that case comes with a cache
  miss anyway.
- **Actual saving:** _pending_

### H5 — Stop re-reading the module source once per mutant

- **Phase:** per-mutant fork
- **Measured cost of that phase:** part of the 0.20–0.36s fork bucket; the read
  itself is not separately measured, which is itself a reason to expect little
- **Predicted saving:** 1–2% on many-files, under 1% elsewhere
- **Rationale:** `inmemory.mutated_source` reads the file from disk for every
  mutant. 560 mutants over 40 files is 560 reads of 40 distinct files. The page
  cache makes each one cheap, which is why the prediction is small.
- **Correctness risk:** low; a cache keyed on path and mtime.
- **Actual saving:** _pending_

### H6 — Avoid the quadratic deep-copy in operator application

- **Phase:** generation (0.3–1.1%)
- **Measured cost of that phase:** 0.016s of 1.47s (many-files)
- **Predicted saving:** under 1% on all three shapes
- **Rationale:** every operator deep-copies the node it is mutating, so a
  single expression with *n* nested operators costs O(n²) node copies. Found
  while writing the M1.4.8 tests: a 6000-term expression took over a minute to
  generate. Real code is not shaped like that, which is why the predicted
  saving on these workloads is under 1% — but the pathological case is real and
  the fix is bounded.
- **Correctness risk:** medium. Operators must not mutate the node they are
  given; a shallower copy that shares structure would let one operator's
  mutation leak into another's.
- **Actual saving:** _pending_

---

## Results

Filled in as each is attempted. See the commit for each change; every one cites
its `make ab` result per M2.3.4.
