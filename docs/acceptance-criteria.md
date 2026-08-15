# moonbuggy — Acceptance Criteria (Phase 0 + Phase 1)

## Status (as of the current commit)

| group | status |
|---|---|
| A — fixture and two-source oracle | A1–A4 met; **A5 not implemented** |
| B — Phase 0 spikes | met |
| C — mutation engine | met |
| D — execution and correctness | met |
| E — reporting | met |
| F — results cache | met |
| G — speed | **G2 NOT MET**; G1, G3, G4 met |
| H — packaging and zero-config | met |

**G2 is the one failure.** moonbuggy runs at 0.90x mutmut's wall clock on the
speed workload — close, but the criterion says lower, and it is not lower. It
*is* 14.1x faster than the naive baseline, which is the design doc's own stated
bar (§1.2), but the criterion Jen selected was beat-mutmut.

**A5 is not implemented.** The advisory mutmut cross-check was specified as
non-gating, and it was dropped in favour of finishing G and H. `make bench` does
run mutmut on the same fixture and reports its status totals, so the two tools'
aggregate verdicts are visible (mutmut: 19 killed / 5 survived / 2 timeout
against the oracle's 15 / 5 / 1 over a different mutant set) — but there is no
per-mutant differential and no written explanation of each disagreement, which
is what A5 asks for.

The cause is understood and recorded: mutmut reuses a warm pytest process, while
moonbuggy pays `pytest.main()` collection inside every forked child. Closing it
means adopting the same warm-process architecture, which is a design change
rather than a tuning pass. Full numbers and analysis in
[benchmark-results.md](benchmark-results.md).

Verify with `make check-all` (correctness, spikes, fresh install) and `make bench`
(the speed numbers, including the G2 failure).

---

## Context

The moonbuggy design doc describes a fast, agent-first Python mutation testing tool.
This document defines what "done" means for Phase 0 and Phase 1 of that design.

The design doc is deliberately open in places ("specific target multiplier TBD",
two carried open questions in §5.3). Before building, we need criteria an evaluator
who did not write the code can apply to decide whether the goal is met — each one
falsifiable by running something, not by reading the source and forming an opinion.

Scoping decisions made with the user:

- **Scope:** Phase 0 (spikes) + Phase 1 (MVP). Phases 2–3 are out.
- **Speed gate:** beat mutmut wall-clock on the same project (not an abstract multiplier).
- **Reference project:** a purpose-built fixture repo vendored into this repo, with a
  hand-labeled expected outcome per mutant.
- **Usability bar:** pip-installable, and a zero-config `moonbuggy` invocation works
  against a plain pytest project.

Each criterion below is written as a claim that is either true or false, paired with
how the evaluator checks it. "Verified by" means: a command exists in the repo that
produces the evidence; the evaluator runs it and reads the output.

---

## A. The evaluation fixture (prerequisite — build this first)

Everything else is checked against this, so it is itself an acceptance criterion.

- **A1.** The repo contains a fixture Python project (suggested `tests/fixtures/sample_project/`)
  with source modules, a pytest suite, and a `pytest.ini`/`pyproject` section enabling
  a pytest-xdist run.
- **A2.** The fixture has a **two-source oracle**. Neither source is moonbuggy's own
  fast path, which is the property that makes the criteria falsifiable.

  - **A2a — naive differential oracle (generated, no labeling).** A reference
    implementation writes each mutated source to a temp copy on disk and runs the
    full suite under plain `pytest`, recording pass/fail. This is the definition of
    a correct mutation result. It shares *no* code with the fast path — no import
    hook, no coverage-guided selection, no cache, no xdist — so agreement between
    the two is real evidence. It is slow by construction; that is acceptable at
    fixture size, and it doubles as the G1 benchmark baseline.
  - **A2b — hand-written label set (small, authored with the fixture).** A checked-in
    file listing the expected status for the A3 cases plus the expected *mutant
    inventory* per fixture module (which sites are mutable, and into what). This is
    what A2a cannot provide: both paths share the AST operators, so a missing or
    wrong mutant is invisible to a differential check — both sides agree and both
    are wrong.

  **Audit guard:** A2b is committed *before any engine code exists*, in its own
  commit. Any later edit to it must be a separate commit with a written reason.
  `git log --follow` on the oracle file makes retroactive relabeling visible rather
  than requiring trust. Labels are written from the fixture's test design, never by
  recording tool output.
- **A3.** The fixture deliberately includes, at minimum: one mutant killed only by a
  test in a different file from the mutated line; one genuinely surviving mutant; one
  mutant on a module-level (non-function-scoped) statement; one mutant that causes an
  infinite loop; one suppressed mutant.
- **A4.** A single documented command runs moonbuggy against the fixture and diffs
  actual output against both oracle sources, exiting non-zero on any mismatch.

- **A5.** *(advisory, non-gating)* Since mutmut already runs on the fixture for the
  G benchmark, the same run reports where its verdicts disagree with the oracle on
  the intersection where both tools generate a corresponding mutant. Disagreements
  do not fail the build, but each requires a written explanation — it is either a
  moonbuggy bug, an oracle error, or a genuine semantic difference between the tools,
  and all three are worth surfacing. mutmut is never authoritative: it has no concept
  of our suppression mechanism, its own timeout semantics, and a different operator
  set, so it can only speak to the cases least likely to be wrong anyway.

*Rationale:* without an oracle independent of the fast path, "the tool works" is
unfalsifiable — a mutation tester reporting plausible-looking nonsense looks identical
to a correct one. A2a supplies that independence mechanically; A2b covers the blind
spot A2a structurally cannot (mutant generation); A5 adds weak external corroboration
for free.

---

## B. Phase 0 — spike exit criteria

These gate architecture commitment. The doc (§8) says the spikes must resolve before
Phase 1 builds on them.

### B1. In-memory mutation coexists with pytest's assert-rewrite hook
A test asserts that, with moonbuggy's mutation active, a pytest run over the fixture
still produces pytest's rewritten assertion output (a rich diff, not a bare
`AssertionError`) **and** observes the mutated code. Both properties checked in the
same run — demonstrating the hooks compose rather than one displacing the other.

### B2. pytest-xdist workers execute the mutated code
A test runs the fixture suite under `-n 2` (or more) with a mutant active whose
killing test is known, and asserts the mutant is reported `KILLED`. A deliberately
introduced regression that stops propagating mutant identity to workers must make
this test fail — i.e. the test is proven to actually detect the §4.2 risk-2 failure
mode, not merely to pass. The evaluator confirms this by checking that a negative
test / documented manual check for it exists.

### B3. Coverage mechanism chosen with recorded numbers
A checked-in benchmark script runs the fixture's coverage pass under each candidate
(`sys.monitoring`, `coverage.py` contexts, and `sys.settrace` if implemented) and
prints wall-clock timings. A short written decision record in the repo names the
winner and cites those numbers. The evaluator can re-run the benchmark; the criterion
is that the decision is recorded and reproducible, not that a particular option won.

### B4. Operator and renderer seams designed before Phase 1 code
A written interface definition exists for (a) mutation operators and (b) report
renderers. Checkable property: every built-in operator and both renderers are
registered through that interface, and adding a new operator requires no edit to the
engine's core traversal/orchestration code. Evaluator check: add a trivial throwaway
operator touching only operator-module files, and confirm it appears in output.

*Phase 0 is complete when B1–B4 all hold.* No external plugin *loading* mechanism is
required — that is explicitly Phase 3.

---

## C. Phase 1 — mutation engine

- **C1.** All five MVP operators from §3.2 are implemented: comparison swap, boolean
  swap, arithmetic swap, constant mutation, boundary/off-by-one. Verified by a test
  per operator asserting the exact mutants generated for a small input.
- **C2.** Mutation is AST-based: a test confirms no mutation is applied inside string
  literals or comments, even when their text matches a mutable pattern.
- **C3.** Every mutant carries a stable identifier and precise `file`, `line` location.
  Stable means: re-running on unchanged source yields the same IDs. Verified by
  running twice and diffing IDs.
- **C4.** A suppression mechanism excludes a named mutant from future runs; the
  suppressed mutant is reported `SKIPPED` rather than omitted silently. Verified via
  the fixture's A3 suppressed case.

---

## D. Phase 1 — execution and correctness

- **D1.** The tool produces a line → covering-tests map from one instrumented pass over
  the fixture suite. Verified by asserting a known mapping (e.g. that the cross-file
  test from A3 appears as a coverer of the expected line).
- **D2.** For each mutant, only tests covering the mutated line are executed. Verified
  by an assertion on the executed-test count for a specific mutant — it is strictly
  fewer than the full suite, and includes the cross-file coverer.
- **D3.** No mutated source file is written to disk. Verified by hashing every source
  file before and after a full run and asserting no changes, plus asserting no stray
  temp copies remain.
- **D4.** Tracebacks from mutated code show the **mutated** source line, not the
  original — the `linecache` requirement in §4.2 risk 3. Verified by a test that
  triggers an error inside a mutated function and asserts the mutated text appears in
  the formatted traceback.
- **D5.** Full-run correctness: the A4 oracle command passes in both serial and xdist
  modes. Specifically, moonbuggy's fast path agrees with the naive differential oracle
  (A2a) on the status of every mutant, and its generated mutant inventory matches the
  hand-written expectation (A2b).
- **D6.** A mutant causing an infinite loop is reported `TIMEOUT` and the run
  completes. Verified via the A3 infinite-loop case, with a bounded overall runtime.
- **D7.** A run leaves the developer's environment unchanged: exit code reflects
  outcome, no orphaned worker processes, no leftover instrumentation on the suite.

*D5 is the single most important criterion in this document.* If it fails, nothing
else matters; if it passes only because the oracle was regenerated from moonbuggy's
own fast-path output, A2 has been violated — check `git log` on the oracle file.

---

## E. Phase 1 — reporting

- **E1.** JSONL is the canonical artifact: one JSON object per mutant, one per line,
  each line independently parseable. Verified by parsing every line and asserting a
  schema (required keys: `status`, `file`, `line`, `category`, `nearest_test`, `diff`,
  plus mutant id).
- **E2.** JSONL is written **streamingly** during the run — verified by killing a run
  partway and confirming the partial file still contains only complete, parseable lines.
- **E3.** The plaintext view is *derived from* the JSONL, not authored separately.
  Verified by regenerating plaintext from a JSONL file and asserting it matches the
  plaintext emitted during the run byte-for-byte.
- **E4.** Every plaintext line begins with one of exactly five fixed keywords:
  `KILLED`, `SURVIVED`, `TIMEOUT`, `SUSPICIOUS`, `SKIPPED`. Verified by asserting
  `grep -c SURVIVED` on the plaintext equals the count of survived records in the JSONL.
- **E5.** Plaintext is strictly one line per mutant, with no embedded newlines — the
  diff is not inlined (the §5.3 open question, resolved as the doc's current lean).
  Verified by asserting line count equals mutant count.
- **E6.** Non-status fields in plaintext are `key=value` tokens survivable by naive
  whitespace splitting; free prose does not appear.
- **E7.** A lookup path exists to retrieve a single mutant's full diff by id
  (e.g. `moonbuggy show <id>`), since E5 keeps it out of plaintext.
- **E8.** `nearest_test` is populated for every `SURVIVED` mutant and names a test that
  actually covers the mutated line. Verified against the fixture's known mapping.

---

## F. Phase 1 — persistent results cache

- **F1.** A second identical run reuses cached outcomes and executes materially fewer
  tests than the first. Verified by comparing reported executed-test counts across
  two consecutive runs.
- **F2.** The cache is keyed on the mutant plus the code it mutates: editing an
  unrelated file does not invalidate a mutant's entry, while editing the mutated
  function does. Verified by two targeted edits and re-runs.
- **F3.** Cached results are indistinguishable in output from freshly computed ones —
  the JSONL from a fully cached run matches a cold run's (modulo timing fields).
- **F4.** The cache can be bypassed/cleared via a documented flag, and a stale or
  corrupt cache file degrades to a cold run rather than crashing or reporting wrong
  statuses.

---

## G. Phase 1 — speed

- **G1.** A checked-in benchmark script runs, on the same fixture and machine:
  (a) moonbuggy, (b) mutmut, and (c) a naive full-suite-per-mutant baseline; and
  prints wall-clock for each.
- **G2.** moonbuggy's wall-clock is lower than mutmut's on that fixture. **This is the
  pass/fail speed gate.**
- **G3.** The benchmark also asserts the two tools find a comparable mutant population
  — moonbuggy is not faster merely by generating fewer mutants. Concretely: moonbuggy's
  mutant count is at least as large as mutmut's for the operators both support.
- **G4.** The measured numbers are recorded in the repo alongside the machine/version
  context, so the claim is auditable rather than folklore.

*Rationale for G3:* a speed comparison between mutation tools is meaningless without
holding work-done roughly constant; this is the criterion most likely to be gamed
accidentally.

---

## H. Packaging and zero-config usability

- **H1.** The package installs from the repo via `pip install .` into a clean virtualenv.
- **H2.** From a plain pytest project's root, bare `moonbuggy` with no flags and no
  config file performs a complete run and writes both artifacts. Verified against a
  second, *different* throwaway pytest project — not the fixture — to prove no
  fixture-specific assumptions leaked in.
- **H3.** A README documents: install, the zero-config invocation, where the two
  artifacts land, the five status keywords, the `show` lookup, suppression, and the
  cache-clearing flag.
- **H4.** Advanced options exist but are never required (§6.2 low-floor principle):
  at minimum, operator selection, path include/exclude, and timeout budget.
- **H5.** The tool degrades with a clear, actionable error — not a traceback — when
  run outside a pytest project or against a suite that fails before mutation begins.

---

## I. Explicit non-criteria

Stated so an evaluator does not fail the work for absent scope:

- No human dashboard or UI.
- No automatic equivalent-mutant detection (manual suppression only).
- No operators beyond the five in §3.2.
- No external plugin *loading* — only the internal seam (B4).
- No `nearest_test` reason taxonomy (§5.3 question 2, deferred).
- No support for `multiprocessing` inside code under test (xdist only).
- No Python < 3.12 support required; a `settrace` fallback is optional.
- No Rust components.

---

## J. Summary — the short version

The goal is complete when, on a clean machine:

1. `pip install .` succeeds, and bare `moonbuggy` runs against an arbitrary pytest
   project (**H1, H2**).
2. The fixture oracle check passes in both serial and xdist modes — the fast path
   agrees with the naive differential oracle on every mutant, and the generated
   mutant inventory matches the hand-written expectation (**D5**).
3. Tracebacks from mutated code show mutated source; no source file is modified on
   disk (**D3, D4**).
4. `grep SURVIVED` on the plaintext output returns exactly the survived mutants, and
   the plaintext is reproducible from the JSONL (**E3, E4**).
5. A repeat run is faster via the cache, with identical results (**F1, F3**).
6. The benchmark shows moonbuggy beating mutmut on the fixture at comparable mutant
   counts, with numbers recorded (**G2, G3, G4**).

Anything in §I being absent is not a failure.

---

## Verification

The whole document is intended to be executable as a checklist. Concretely, the repo
should end up with three evaluator-facing commands:

- `make check-oracle` — A4/D5, the correctness gate (serial and xdist).
- `make bench` — G1–G4, prints the three wall-clock numbers and the mutant counts.
- `make check-fresh-install` — H1/H2, installs into a clean venv and runs bare
  `moonbuggy` against a throwaway project.

Everything else (C, E, F, and the Phase 0 spikes B1–B2) is covered by the ordinary
`pytest` suite. An evaluator runs those four commands and reads §J.

## Build order

These are acceptance criteria, not an implementation plan. The implied sequence is
A → B → C–H: the fixture and its oracle (A) must exist before any other criterion
can be evaluated, and the Phase 0 spikes (B) gate the architecture the rest sits on.
