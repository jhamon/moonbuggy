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

---

# Second round: H7–H12

The M2.4 conclusion above — "the remaining cost is irreducible without a named
architectural change" — was wrong, and this round is the record of how. It was
right that in-child test execution and per-mutant fork dominated the profile.
It was wrong to read "per-mutant fork" as meaning `fork()`. Most of that bucket
was not the fork at all: it was **work repeated identically inside every
grandchild that the warm host could have done once, before forking.** Three of
the four wins here are the same move applied to three different pieces of that
work.

The first profile ranked phases. What it could not show is that a phase is
made of per-mutant constants, because the profiler measures the host and the
parent, not what happens inside a grandchild. Profiling one warm
`pytest.main` — the thing a grandchild actually does — is what found H10 and
H12, and it took one `cProfile` run:

| in one warm `pytest.main` | share |
|---|---:|
| `_prepareconfig` (config, plugin registration, entry points) | 57% |
| — of which `_mark_plugins_for_rewrite` (walks every installed dist's files) | 26% |
| `gc_collect_harder` (two `gc.collect()` from the unraisable plugin) | 20% |
| collection of the selected node ids | 12% |
| the mutant's own tests | small |

**Lesson for the next round: profile the process that repeats, not only the
process that runs.**

## Register

### H7 — Build the module-to-swap index once, not per mutant

- **Phase:** per-mutant fork
- **Premise, measured first:** `_apply_in_place` found the module to mutate by
  scanning `sys.modules` and calling `Path.resolve()` on every entry. Measured
  at **4.7ms in a process with 250 modules loaded** — more than a fast mutant's
  tests take, paid once per grandchild, scaling with mutant count.
- **Predicted saving:** 10–20% on many-files, 3–6% on the other two
- **Correctness risk:** low. `module_at()` keeps the scan as a fallback, so a
  module imported after the index was built is still found — a miss there
  would be a mutation that silently does not apply, which is a false SURVIVED.
  First entry wins, matching the scan it replaces, because `sys.modules` keeps
  insertion order.
- **Attempted:** yes, adopted (commit `19beea8`).
- **Actual saving:** **faster on all three shapes.**

  | shape | baseline | candidate | verdict |
  |---|---|---|---|
  | fast-tests | 0.547s [0.535, 0.555] | 0.505s [0.501, 0.516] | **faster 1.08x** |
  | slow-tests | 0.992s [0.979, 1.012] | 0.881s [0.881, 0.897] | **faster 1.13x** |
  | many-files | 1.583s [1.579, 1.590] | 1.364s [1.346, 1.379] | **faster 1.16x** |

  The prediction was right, and right for the stated reason. It is the same
  move as H5 (prewarm in the host, inherit across the fork) applied to a much
  more expensive constant — which is the argument for having kept H5's
  near-zero result on the record.

### H8 — Stop losing 2ms per grandchild to the poll loop

- **Phase:** per-mutant fork
- **Premise:** `_fork_grandchildren` slept 2ms between reap scans, so each
  mutant sat up to 2ms between writing its result and its concurrency slot
  being refilled — and spent a core polling while it did.
- **Predicted saving:** 5–9% on many-files, 2–4% elsewhere
- **Correctness risk:** low but not nil. Replaces the reap condition with
  "the result pipe is readable", and moves timeout enforcement onto the
  `select` timeout. Gated by `make check-robustness`, which is where the
  timeout, self-exiting-test and crash-recovery cases live; all 21 passed.
- **Attempted:** yes, implemented in full and measured. **Discarded.**
- **Actual saving:** **indistinguishable on all three shapes.**

  | shape | baseline | candidate | verdict |
  |---|---|---|---|
  | fast-tests | 0.506s [0.504, 0.508] | 0.501s [0.500, 0.507] | indistinguishable |
  | slow-tests | 0.898s [0.895, 0.900] | 0.895s [0.886, 0.898] | indistinguishable |
  | many-files | 1.382s [1.375, 1.393] | 1.376s [1.372, 1.377] | indistinguishable |

  **The prediction confused latency with throughput.** The 2ms is real, and it
  is almost entirely hidden: while one grandchild's slot sits idle, the other
  eight are still running, so the delay only costs wall clock in the final
  wave. Multiplying 2ms by the mutant count assumed the mutants were serial.
  They are the one thing in this system that is not.

  Discarded rather than kept-because-tidy. Unlike H5, this one is not
  "strictly less work" — it is *different* work, with the timeout guarantee
  resting on new machinery. A rewrite of the reaping loop that buys nothing
  measurable is not worth the surface area.

### H9 — Dispatch the most expensive mutants first

- **Phase:** per-mutant fork
- **Premise:** grandchildren are dispatched in mutant order; longest-first
  (LPT) scheduling would shorten the tail of the final wave.
- **Predicted saving:** 2–4% on slow-tests
- **Attempted:** no. **Refuted by measurement before implementing**, the same
  way H4 was. The distribution of tests selected per mutant:

  | shape | mutants | selected-test counts |
  |---|---:|---|
  | slow-tests | 84 | 7 (×42), 8 (×42) |
  | many-files | 560 | 0 (×400), 1 (×80), 2 (×80) |

  **There is no cost spread to reorder.** On slow-tests every mutant selects 7
  or 8 tests. On many-files 400 of 560 select nothing at all, and those are
  settled during planning without ever being dispatched, so they are not in
  the schedule LPT would reorder. LPT beats FIFO in proportion to the variance
  in job cost, and the variance here is about 14% on one shape and absent on
  the other.
- **Actual saving:** none available; not implemented.

### H10 — Freeze the warm host's heap before forking

- **Phase:** in-child test execution (which is where a grandchild's
  `pytest.main` overhead lands, not only the tests)
- **Premise, measured first:** `pytest.main` calls `gc.collect()` twice on its
  way out, from the unraisable-exception plugin's unconfigure hook. In a warm
  host holding pytest, coverage and the whole suite that walks ~25000 tracked
  objects. `cProfile` put the pair at **20% of one warm `pytest.main`**, and
  every grandchild paid it — for objects it inherited and cannot have changed.
- **Predicted saving:** 10–20%, largest on the mutant-heavy shapes
- **Correctness risk:** low, and bounded rather than absent. `gc.freeze()`
  moves the current heap into a permanent generation collection skips.
  Garbage the mutant's own tests create is still tracked and still collected,
  so the unraisable plugin still sees the only garbage that can say anything
  about this mutant. A reference cycle created *before* the freeze will never
  be collected in a grandchild, so a `__del__` on one of those will not run —
  those are the host's own infrastructure objects, none of which the mutation
  has touched. Deliberately not `-p no:unraisableexception`, which would have
  been faster still and would have been rejected on H2's grounds: a mutant
  that manifests only as an unraisable exception would be reported SURVIVED.
- **Attempted:** yes, adopted (commit `9dea0c4`).
- **Actual saving:** **the largest single win in either round.**

  | shape | baseline | candidate | verdict |
  |---|---|---|---|
  | fast-tests | 0.534s [0.524, 0.537] | 0.465s [0.461, 0.469] | **faster 1.15x** |
  | slow-tests | 0.919s [0.906, 0.926] | 0.698s [0.690, 0.705] | **faster 1.32x** |
  | many-files | 1.376s [1.374, 1.392] | 0.933s [0.918, 0.952] | **faster 1.47x** |

  Larger than predicted, and the micro-benchmark says why the prediction was
  low. One warm `pytest.main` went 18.2ms to 13.2ms in isolation — 27%. But
  `gc.collect()` costs more when the machine is busy and the pages are shared
  across nine concurrent grandchildren, and copy-on-write traffic from the
  collector writing to inherited object headers is a cost the isolated
  measurement never sees at all.

### H11 — Import coverage once, in the parent, before forking

- **Phase:** coverage pass (32.7–38.3% after H7/H10/H12 — the largest phase in
  the re-taken profile)
- **Premise, measured first:** the warm-session path imported `coverage`
  **twice, serially, both on the critical path**: the host imports it when
  pytest-cov starts the instrumented run, and the parent imports it again in
  `read_coverage_data` once the host has handed back its evidence. `import
  coverage` measured at ~36ms.
- **Predicted saving:** 8% on fast-tests, 4–6% on the other two
- **Correctness risk:** low. Importing coverage starts no measurement and
  builds no `Coverage` object, so the host still builds its own from scratch;
  and coverage is not the project under mutation, so the rule the parent
  actually has to obey — never import the code being mutated — is untouched.
- **Attempted:** yes, adopted (commit `4275384`).
- **Actual saving:** **faster on all three, and smaller than predicted.**

  | shape | baseline | candidate | verdict |
  |---|---|---|---|
  | fast-tests | 0.431s [0.430, 0.431] | 0.416s [0.414, 0.417] | **faster 1.04x** |
  | slow-tests | 0.624s [0.623, 0.624] | 0.610s [0.605, 0.613] | **faster 1.02x** |
  | many-files | 0.814s [0.812, 0.816] | 0.801s [0.795, 0.801] | **faster 1.02x** |

  About 14ms, not the 36ms predicted: `coverage`'s submodules are imported
  lazily, so the two imports were never duplicating the whole cost — only the
  part both paths reach.

  **This is the exact inverse of H3, and the two do not contradict each
  other.** H3 removed a parent import of work *the host was going to do
  anyway*, and the import simply moved. H11 adds a parent import of work *the
  parent was going to do anyway*, and one of the two copies genuinely
  disappears. The test is not "should the parent import less" but "how many
  times does this import happen on the critical path".

### H12 — Stop recomputing the assert-rewrite plugin list per mutant

- **Phase:** in-child test execution
- **Premise, measured first:** to decide which installed plugins to mark for
  assertion rewriting, pytest walks the file list of every installed
  distribution. `cProfile` put `_consider_importhook` at **26% of one warm
  `pytest.main`**. The answer is identical for every mutant in the run.
- **Tried once before, and reverted.**
  [benchmark-results.md](../benchmark-results.md) records `--assert=plain` as
  one of two Phase 1 changes that "measured as noise" — the same flag, giving
  the opposite verdict. Both readings are correct, for two reasons that
  compound:

  - **It was measured against a different architecture.** Phase 1 had no warm
    session; each mutant was a cold fork that imported the test modules
    itself, so the flag's saving was buried under ~139ms per mutant of import
    work that the warm session has since removed. What was noise beside 139ms
    is 26% of a 18.2ms warm `pytest.main`.
  - **It was measured with an instrument that could not resolve it.** Phase 1
    predates `make ab`, and `ab_compare.py`'s own docstring names those single
    runs as the reason it exists. "Measured as noise" then meant "one run did
    not obviously move", not "seven interleaved runs put the intervals on top
    of each other".

  Worth stating rather than quietly re-adopting: a change rejected once is not
  rejected forever, but the entry that rejected it should say what changed.
  What changed here is the architecture it sits in and the instrument it is
  measured with — not the argument for it.
- **Predicted saving:** 8–15%
- **Correctness risk:** low, on two independent grounds, and only the warm
  grandchild passes `--assert=plain`. The host imported and rewrote every test
  module during the coverage pass, so the mutant run re-imports none of them;
  and the host's rewrite hook is still in the grandchild's `sys.meta_path`
  across the fork, so a module that did import late would be rewritten by it
  regardless. The cold path keeps rewriting — there the child really does
  import test modules fresh, and it is the fallback rather than the hot path.
  Gated by `make check-oracle`, `make check-robustness` and
  `make check-properties` (500 examples per invariant); all passed.
- **Attempted:** yes, adopted (commit `5c240ac`).
- **Actual saving:** **faster on all three shapes**, at the low end of the
  prediction because H10 had already landed and the two overlap.

  | shape | baseline | candidate | verdict |
  |---|---|---|---|
  | fast-tests | 0.459s [0.456, 0.461] | 0.445s [0.444, 0.450] | **faster 1.03x** |
  | slow-tests | 0.688s [0.684, 0.690] | 0.638s [0.635, 0.641] | **faster 1.08x** |
  | many-files | 0.927s [0.915, 0.930] | 0.841s [0.835, 0.844] | **faster 1.10x** |

  In isolation the flag took one warm `pytest.main` from 18.2ms to 14.1ms, and
  both together took it to 8.4ms — less than half. Order matters for the
  attribution: measured before H10, H12 would have scored higher and H10
  lower. Each is credited with what it was worth *given what had already
  landed*, which is what the A/Bs above actually measured.

## Results

| # | hypothesis | predicted | actual | outcome |
|---|---|---|---|---|
| H7 | index the module to swap once | 10–20% | 1.08–1.16x | adopted |
| H8 | reap on the pipe, not a 2ms poll | 5–9% | indistinguishable | **discarded** |
| H9 | longest-mutant-first dispatch | 2–4% | 0% available | refuted by measurement |
| H10 | `gc.freeze()` the warm host | 10–20% | 1.15–1.47x | adopted |
| H11 | import coverage once, in the parent | 4–8% | 1.02–1.04x | adopted |
| H12 | `--assert=plain` in the grandchild | 8–15% | 1.03–1.10x | adopted |

**Scoreboard for the predictions: two right (H7, H12), one right about the
direction and low about the size (H10), one right about the direction and high
about the size (H11), one wrong because it multiplied a per-mutant latency by
the mutant count when the mutants run nine at a time (H8), one wrong about its
premise (H9).**

Both of the failures were failures of the same kind the first round had:
reasoning about a cost without measuring whether the thing it multiplies is
actually there. Both were caught before they cost anything — H9 by measuring
the premise for the price of one script, H8 by the A/B refusing to call a 5ms
difference a win.

## Outcome

**Cumulative effect of everything adopted, `569a53a` → `d3270f6`**, measured by
one interleaved `make ab` rather than by multiplying the four individual
results together:

| shape | before | after | verdict |
|---|---|---|---|
| fast-tests | 0.554s [0.550, 0.562] | 0.430s [0.427, 0.435] | **faster 1.29x** |
| slow-tests | 0.989s [0.978, 1.002] | 0.623s [0.618, 0.625] | **faster 1.59x** |
| many-files | 1.534s [1.523, 1.556] | 0.810s [0.808, 0.815] | **faster 1.89x** |

No adopted change regressed any shape, which is why all three ran on every
A/B (M2.4.2).

### What the re-taken profile says now

`make profile` on `d3270f6`, five runs per shape, median:

| phase | fast-tests | slow-tests | many-files |
|---|---:|---:|---:|
| coverage pass | 44.5% | 38.3% | 32.7% |
| in-child test execution | 15.6% | 28.8% | 34.5% |
| flaky probe | 9.4% | 8.3% | 6.8% |
| per-mutant fork | 4.1% | 6.7% | 8.8% |
| interpreter startup | 8.0% | 5.6% | 4.2% |
| planning | 1.0% | 3.2% | 4.7% |
| generation, reporting, discovery, cache I/O | <0.5% | <0.5% | ~2% |
| **unattributed** | **16.6%** | **8.4%** | **6.2%** |

Absolute medians: fast-tests 0.421s / 84 mutants, slow-tests 0.645s / 84
mutants, many-files 0.833s / 560 mutants. The fast-tests column is from a
later run than the other two — the shapes are profiled independently, so this
is three medians rather than one decomposition, the same caveat the first
profile carried.

**Per-mutant fork has gone from 21.5–24.6% to 6.7–8.8%, and the coverage pass
is now the largest phase on every shape.** That reverses the M2.4 ranking, and
it also removes most of what made H1 attractive: batching several mutants per
fork was predicted at 12–15% when the fork bucket was a quarter of the run. It
is now under 9%, and H1's risk — an incomplete restore between mutants
producing a confident wrong status — has not changed at all. **H1 should stay
deferred, and by a wider margin than before.**

### A gate this round does not fix

`make profile` now **fails M2.1.2** ("phases cover ≥ 95% of wall clock") on all
three shapes. That is worth stating precisely, because the obvious reading is
wrong.

The unattributed remainder in **absolute** terms, same machine, same harness:

| shape | `569a53a` | `d3270f6` |
|---|---:|---:|
| fast-tests | 0.0542s | 0.0582s, 0.0699s |
| slow-tests | 0.0576s | 0.0544s |
| many-files | 0.0581s | 0.0513s |

**No time moved into the unattributed bucket.** It sits between 51ms and 70ms
on both sides — two independent profile runs of `d3270f6` are quoted for
fast-tests to show how much of that range is just run-to-run spread. It is
process startup and teardown that the profiler has no span for, chiefly
moonbuggy's own import chain, of which `import pytest` alone is 48ms. The gate
fails because the denominator shrank by 29–47%, not because anything became
less well understood. On this machine `569a53a` already failed it on two of the
three shapes.

The fix is a span covering the import chain and interpreter teardown, so the
phase table accounts for a cost that is now a tenth of a fast run. Recorded as
the next piece of work rather than done, because it is a change to the
measuring instrument and this round's changes should be re-measured with the
instrument they were taken with.
