# Performance hypotheses (milestone M2.2)

**Rule:** every entry states its predicted saving *before* the change is
attempted, and records the actual saving afterwards — including for attempts
that were abandoned. Wrong predictions stay in this document. The record of
what did *not* work is the part that compounds; deleting it leaves a document
that says every idea was good, which is both useless and false.

**Why the rule exists.** [benchmark-results.md](../benchmark-results.md) records
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
- **Attempted:** yes. **Rejected before measuring**, on the backend's own
  warning:

  > `CoverageWarning: Dynamic contexts aren't supported with core=sysmon;
  > context data may be incomplete (no-sysmon-context)`

  Per-test contexts *are* the line→test map, and the map is the input to
  selection. "Context data may be incomplete" means a covering test can go
  missing, which means running too few tests, which means a false SURVIVED —
  indistinguishable from a real finding. No wall-clock number could justify
  that, so none was collected.
- **Actual saving:** none; not adopted. The prediction of 8–12% was probably
  right about the speed and entirely beside the point.

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
- **Attempted:** yes, adopted (commit `00ac3e0`).
- **Actual saving:** **1.5% on slow-tests, nothing on the other two.**

  | shape | baseline | candidate | verdict |
  |---|---|---|---|
  | fast-tests | 0.521s [0.514, 0.526] | 0.518s [0.513, 0.534] | indistinguishable |
  | slow-tests | 0.960s [0.957, 0.969] | 0.946s [0.936, 0.954] | **faster 1.02x** |
  | many-files | 1.471s [1.457, 1.498] | 1.485s [1.467, 1.499] | indistinguishable |

  **The prediction was wrong about the mechanism.** The import is *moved into
  the host*, not removed: the host pays it the instant the parent stops. So the
  5–6% predicted on fast-tests — where parent warm-up was 6.6% of the run —
  never had anywhere to come from. The residual win on one shape is most likely
  a smaller parent image to fork, which this did not measure and does not claim
  to have shown. Adopted because it is a real win on one shape and a regression
  on none.

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
- **Attempted:** yes. **Refuted by measurement before implementing.** The union
  of tests selected by *some* mutant was measured on all three shapes:

  | shape | tests | selected by some mutant |
  |---|---:|---:|
  | fast-tests | 90 | 90 (100%) |
  | slow-tests | 90 | 90 (100%) |
  | many-files | 120 | 120 (100%) |

  **The prediction confused two different things.** Selection is narrow *per
  mutant* — that is the whole speed lever — but the union across all mutants is
  the entire suite, because every test covers some mutable line. There is
  nothing for a narrowed probe to skip. This one is worth keeping in the
  register precisely because the reasoning felt obviously right and was not.
- **Actual saving:** none available; not implemented. The restructure it would
  have needed — a second round trip in the warm-session protocol — was never
  paid for.

### H5 — Stop re-reading the module source once per mutant

- **Phase:** per-mutant fork
- **Measured cost of that phase:** part of the 0.20–0.36s fork bucket; the read
  itself is not separately measured, which is itself a reason to expect little
- **Predicted saving:** 1–2% on many-files, under 1% elsewhere
- **Rationale:** `inmemory.mutated_source` reads the file from disk for every
  mutant. 560 mutants over 40 files is 560 reads of 40 distinct files. The page
  cache makes each one cheap, which is why the prediction is small.
- **Correctness risk:** low; a cache keyed on path, mtime and size.
- **Attempted:** yes, adopted (commit `2e1a5c5`). Implementing it turned up the
  reason the obvious version would have done nothing: a process-local cache
  cannot help when every mutant runs in its own forked process and reads
  exactly once. It pays only because the warm host fills it *before* forking.
- **Actual saving:** **indistinguishable on all three shapes.** Predicted 1–2%
  on many-files; the measurement cannot resolve a difference that small from
  seven runs, which is the honest answer rather than a disappointing one.
  Kept: it is strictly less work, it costs one dictionary, and the A/B shows no
  regression on any shape.

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
- **Attempted:** yes, adopted (commit `d661118`). `replace_operator()` in the
  operator registry makes the shallow copy the path of least resistance, so the
  risk is managed by making the correct thing the easy thing rather than by a
  rule nobody rereads.
- **Actual saving:** **indistinguishable on all three shapes** — exactly as
  predicted, which is the one prediction in this register that was right.

  Kept anyway, and the reason is not wall clock on these workloads:

  | case | before | after |
  |---|---:|---:|
  | 1000-term chained expression | 3.0s | 1.0s |
  | moonbuggy's own fast test suite | 5.0s | 2.1s |

  **The quadratic is not gone.** `ast.unparse` of a deeply nested node is still
  O(subtree), and it now dominates: 6000 terms still takes 37s. Splicing only
  the changed token instead of unparsing the whole node would remove it, at the
  cost of changing the operator seam's contract. Recorded as the next lever
  rather than done, because nothing has yet shown it matters on real code.

---

## Results

Six registered, five attempted (M2.2.4 asks for six and four).

| # | hypothesis | predicted | actual | outcome |
|---|---|---|---|---|
| H1 | batch mutants per fork | 12–15% | — | deferred: only entry whose failure mode is a wrong status |
| H2 | `COVERAGE_CORE=sysmon` | 8–12% | — | rejected: incomplete per-test contexts would cause false SURVIVED |
| H3 | drop parent `import pytest` | 3–6% | 1.5% on one shape | adopted |
| H4 | probe only selected tests | 2–3% | 0% available | refuted by measurement |
| H5 | cache module source reads | 1–2% | indistinguishable | adopted |
| H6 | shallow copy in operators | <1% | indistinguishable | adopted |

**Scoreboard for the predictions themselves: one right (H6), one wrong about
the mechanism (H3), one wrong about the premise (H4), one right about the speed
and wrong about whether speed was the question (H2).** That is the point of
writing them down first.

## M2.4 — outcome

**M2.4.1.** Cumulative effect of everything adopted, `3937ded` → `2e1a5c5`:
one shape improved by a statistically significant 1.5%, two unchanged. That is
a real result and a small one, so the second half of M2.4.1 is the honest
reading:

> **The remaining cost is irreducible without a named architectural change.**

The profile says where it lives. In-child test execution plus per-mutant fork
is 60–68% of every run, and both are floors rather than overheads:

- **In-child test execution** is the user's own tests. Making it smaller means
  running fewer of them, and selection already runs the minimum that can kill
  a given mutant. There is nothing left to cut that is not correctness.
- **Per-mutant fork** is one `fork()` plus one `pytest.main()` per mutant. The
  named architectural change that would remove it is H1 — running several
  mutants in one process — and H1 is deferred because an incomplete restore
  between mutants produces a confident wrong status. That is the trade, stated
  plainly: roughly 20% of wall clock is being paid for the guarantee that every
  mutant is evaluated in a process that has seen no other mutation.
- **The coverage pass** (17–25%) is already shared with the warm-up; it was two
  full suite runs in Phase 1 and is now one.
- **The flaky probe** (3.6–5.2%) is the price of M1.4.3, and is a flag away
  from zero for anyone who does not want the guarantee.

**M2.4.2.** No accepted change regressed any shape at all, let alone by 10%.
Every A/B above ran all three shapes for exactly this reason.

**M2.4.3.** `make check-oracle` and `make check-all` pass on `2e1a5c5`.
