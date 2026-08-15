# moonbuggy — next milestones (Phase 2)

**Status:** proposed, not started.
**Baseline:** Phase 0 + Phase 1 complete, all criteria in
[acceptance-criteria.md](acceptance-criteria.md) met. 135 tests. G2 passes at
1.07x over mutmut, 17.5x over naive.

Four milestones, each written the same way as the Phase 1 criteria: every
acceptance criterion is a claim an evaluator can mark clearly true or clearly
false by running something, not by forming an opinion.

The four are independent and can run in any order. §5 recommends one.

---

## M1 — Verification depth

**Goal:** stop relying on a 22-mutant fixture as the only real correctness
evidence. Three techniques, each aimed at a class of bug the current suite
structurally cannot find.

*The M1.1 slot is intentionally vacant. It held self-mutation — running
moonbuggy on its own source — which was dropped for now. It is the sharpest
available test, but the code under mutation would be the mutation engine itself,
so a defective mutant could corrupt the run meant to detect it, and the failure
would read as a finding rather than an error. Doing it safely needs a pinned
separate install mutating a separate checkout, which is enough machinery to
deserve its own milestone rather than a subsection of this one. The slot is left
empty rather than renumbered so existing references to M1.2/M1.3/M1.4 keep
meaning what they meant.*

### M1.2 Property-based testing (Hypothesis)

Generate source and ASTs rather than hand-writing cases. The current operator
tests assert on examples chosen by the same person who wrote the operators,
which is exactly the blind spot Hypothesis exists to cover.

Invariants to test, each as a separate property:

- **M1.2.1** *Every generated mutant compiles.* For any parseable input module,
  every mutant's spliced source passes `ast.parse` and `compile`. A mutation
  that produces a `SyntaxError` would surface as `SUSPICIOUS` and look like a
  finding.
- **M1.2.2** *Mutation never changes string or comment content.* For any input,
  the multiset of string-literal values and comment text is identical before and
  after every mutation. This is criterion C2 generalised past the one
  hand-written case.
- **M1.2.3** *Ids are stable and unique.* Generating twice from identical source
  yields identical id sequences; no id repeats within a module.
- **M1.2.4** *Splicing round-trips.* For any single-line mutation, replacing the
  mutated fragment with the original fragment reproduces the source byte for
  byte.
- **M1.2.5** *Line attribution is correct.* Every mutant's reported line number
  contains its reported original text.
- **M1.2.6** *Scope classification is sound.* No mutant inside a `def` body is
  ever flagged `module_level`, and no mutant at module top level is ever flagged
  otherwise — checked against an independently computed scope map.
- **M1.2.7** Each property runs at least 500 examples in CI and the strategy
  generates non-trivial programs — nested functions, classes, comprehensions,
  decorators, async defs, chained comparisons, augmented assignment.
- **M1.2.8** Any failure Hypothesis finds is added to the example suite as a
  regression test with its shrunk input, and the underlying bug fixed.

### M1.3 Differential testing against mutmut at scale

Extend A5 from an advisory check on one fixture to a broad correctness net.

- **M1.3.1** A harness runs both tools over at least 10 projects (the 5 from M4
  plus the fixture plus generated workloads) and emits a per-mutant
  correspondence table for the operators both implement.
- **M1.3.2** Every disagreement is classified as: *moonbuggy bug*, *mutmut bug*,
  *genuine semantic difference*, or *not actually the same mutant*. Zero
  unclassified.
- **M1.3.3** Every disagreement classified *moonbuggy bug* has a failing
  regression test added before the fix.
- **M1.3.4** The classification table is checked into `docs/differential.md`
  with counts per category.
- **M1.3.5** The harness is re-runnable and reports drift if a later run
  produces a disagreement not in the table.

### M1.4 Robustness and fault injection

Hostile inputs. The requirement throughout is that moonbuggy **degrades visibly
rather than lying** — a wrong status is far worse than a refusal, because a
false `SURVIVED` is indistinguishable from a real finding.

Each scenario below is a fixture project plus an assertion about behaviour:

| scenario | required behaviour |
|---|---|
| **M1.4.1** source file with a syntax error | named in a clear error; other files still processed |
| **M1.4.2** module with import-time side effects (writes a file, opens a socket) | either handled or refused with a clear message; never a wrong status |
| **M1.4.3** genuinely flaky test (fails ~50% at random) | run completes; affected mutants reported `SUSPICIOUS`, never a confident `KILLED`/`SURVIVED` |
| **M1.4.4** test suite that is already red before mutation | clear message that the baseline is failing; no mutation results claimed |
| **M1.4.5** code under test spawning threads | run completes; no hang beyond timeout; no leaked threads into the parent |
| **M1.4.6** test that calls `sys.exit()` / `os._exit()` | run completes; that mutant classified, not silently lost |
| **M1.4.7** non-UTF8 / unusual encoding source file | handled or refused with a clear message; never mis-mutated |
| **M1.4.8** very large file (>10k lines) and deeply nested code | completes within a stated bound; no recursion error from the AST walk |
| **M1.4.9** empty project / no tests / no source | clear actionable message, exit 2 |
| **M1.4.10** `conftest.py` with fixtures having side effects between tests | selection still correct; verified against the naive oracle on that fixture |

- **M1.4.11** Every scenario above is an automated test, not a manual check.
- **M1.4.12** No scenario produces a traceback as its user-facing output.
- **M1.4.13** A crash-recovery check: killing a run mid-flight leaves a valid
  partial JSONL and a cache that a subsequent run reads without error.

---

## M2 — Performance research (profile-first)

**Goal:** replace guesswork with measurement. The Phase 1 benchmark doc records
two changes that were implemented on a hunch and measured as noise, and notes
the profile that would have prevented it was cheap. This milestone makes taking
that profile the mandatory first step.

### M2.1 Phase profiler

- **M2.1.1** `make profile` produces a wall-clock breakdown of a run by named
  phase: discovery, generation, coverage pass, warm-session startup, per-mutant
  fork, in-child test execution, cache I/O, reporting.
- **M2.1.2** The phases sum to at least 95% of measured wall clock. Unattributed
  time is reported explicitly rather than absorbed silently — an "other" bucket
  hiding 30% is how the last round went wrong.
- **M2.1.3** The profiler runs against at least three workload shapes (fast
  tests / slow tests / many small files) and reports each separately, since the
  bottleneck demonstrably moves between them.
- **M2.1.4** Profiling overhead is measured and stated; the profiled run is
  within 20% of an unprofiled one, or the discrepancy is explained.

### M2.2 Ranked hypothesis register

- **M2.2.1** `docs/perf-hypotheses.md` lists candidate optimisations, each with:
  the phase it targets, the measured cost of that phase, a **predicted** saving
  stated *before* implementation, and an estimated risk to correctness.
- **M2.2.2** Hypotheses are ranked by predicted saving, and work proceeds in
  rank order unless a documented reason overrides it.
- **M2.2.3** After each attempt, the register records **actual** saving next to
  the prediction, including for abandoned attempts. Wrong predictions stay in
  the document — the record of what did *not* work is the part that compounds.
- **M2.2.4** At least 6 hypotheses registered, at least 4 attempted.

### M2.3 A/B measurement harness with significance

The 0.90s-vs-0.92s episode showed single runs cannot distinguish a real change
from noise.

- **M2.3.1** `make ab BASELINE=<ref> CANDIDATE=<ref>` measures both over at
  least 7 runs and reports median, min, and a 95% confidence interval.
- **M2.3.2** The harness declares a winner only when the confidence intervals do
  not overlap; otherwise it reports "indistinguishable" and says so plainly.
- **M2.3.3** Fed two identical refs, it reports "indistinguishable" — a
  self-check that the harness is not manufacturing wins. This must be an
  automated test.
- **M2.3.4** Every performance change merged after this milestone cites an A/B
  result in its commit message.

### M2.4 Outcome

- **M2.4.1** Either a cumulative wall-clock improvement significant under M2.3.2
  on at least one workload shape, **or** a written finding that the remaining
  cost is irreducible without a named architectural change. Both outcomes pass —
  a search that honestly finds nothing is a result, and pretending otherwise is
  how benchmarks get gamed.
- **M2.4.2** No accepted change regresses any other workload shape by more than
  10% without that trade being stated in the register.
- **M2.4.3** `make check-oracle` and `make check-all` still pass after every
  accepted change. Speed work must never be allowed to buy time with silence.

---

## M3 — Sphinx documentation

**Goal:** documentation that teaches mutation testing, not just an API dump.
Hypothesis is the model: a reader arrives knowing nothing, gets something
working in five minutes, and is walked up to the sophisticated material without
ever hitting a cliff.

### M3.1 Build infrastructure

- **M3.1.1** `make docs` builds HTML into `docs/_build/html` from a Sphinx
  project under `docs/`.
- **M3.1.2** The build runs with `-W` (warnings as errors) and completes with
  zero warnings. Broken cross-references fail the build.
- **M3.1.3** `make docs-linkcheck` passes with no broken internal links.
- **M3.1.4** A `docs` extra in `pyproject.toml` installs everything the build
  needs; a clean venv can build the docs from a fresh checkout.
- **M3.1.5** Nothing is published anywhere. Local build only.

### M3.2 Docstring coverage

- **M3.2.1** Every public module, class, and function in `src/moonbuggy/` has a
  docstring. Measured by an automated check (`interrogate` or equivalent) at
  100% for public API, wired into `make docs`.
- **M3.2.2** Every public function docstring documents its parameters, its
  return value, and any exception it raises deliberately.
- **M3.2.3** A single documented style (Google or NumPy) is used throughout and
  enforced by the build.
- **M3.2.4** Private helpers are exempt from the coverage gate but any that
  encode a non-obvious decision keep their explanatory comment.
- **M3.2.5** Docstrings state *what and why*; the design rationale currently
  living in module docstrings survives into the rendered site rather than being
  flattened into signatures.

### M3.3 Narrative documentation

Pages required, each with a stated audience:

| page | audience | must contain |
|---|---|---|
| **M3.3.1** Quickstart | never used mutation testing | install → run → read one survivor → fix it, in under 10 minutes |
| **M3.3.2** What mutation testing is | knows pytest, not mutation testing | why coverage is not enough, worked example of a 100%-covered function with a passing test that catches nothing |
| **M3.3.3** Tutorial | has run it once | a realistic module walked end to end: run, triage survivors, add tests, re-run, watch the score move |
| **M3.3.4** Reading the output | agent authors and CLI users | every status keyword, every plaintext token, the JSONL schema, worked `grep`/`jq` recipes |
| **M3.3.5** Equivalent mutants | anyone with a stubborn survivor | why detection is undecidable, how to recognise one, how to suppress it honestly |
| **M3.3.6** Making runs fast | large-codebase users | how selection and caching work, what makes a suite slow to mutate, which flags matter |
| **M3.3.7** Architecture | contributors | the pipeline, the warm session, in-place mutation, and why each exists |
| **M3.3.8** Writing an operator | contributors | the seam, a complete worked example, how to test it |
| **M3.3.9** Troubleshooting | stuck users | every error message the CLI can emit, with its cause and fix |

- **M3.3.10** Every code example in every page is executable and verified by
  `make docs-test` (doctest or equivalent). No example may be aspirational.
- **M3.3.11** The quickstart is validated on a machine with no prior moonbuggy
  install, following only what the page says.
- **M3.3.12** The API reference is generated by autodoc, not hand-maintained.

---

## M4 — Open-source defect hunt

**Goal:** find real gaps in real code, as evidence the tool produces findings
worth a maintainer's attention. This is also the harshest test of the reporting
format: survivors in unfamiliar code are only useful if the output explains
itself.

**Targets:** five small pure-Python libraries with fast deterministic pytest
suites and no compiled extensions. Proposed: `more-itertools`, `boltons`,
`humanize`, `sqlparse`, `tomli`. Substitutions allowed if one proves
unsuitable, with the reason recorded.

**Explicit constraint: nothing is posted anywhere.** No issues, no pull
requests, no discussions, no emails. Findings stay in a local document for Jen
to review. This is a standing constraint on the whole milestone, not a step in
it.

- **M4.1** A reproducible harness pins each target to a specific commit or
  release tag, installs it into an isolated venv, and confirms its own suite is
  green before mutating. A red baseline invalidates every result from it.
- **M4.2** All five targets complete a run, or any that cannot is documented
  with the specific blocker and replaced.
- **M4.3** For each target the harness records: mutant count, status breakdown,
  wall clock, mutation score, and moonbuggy's version and commit.
- **M4.4** `docs/oss-findings.md` exists and contains, for every survivor judged
  a probable real gap: the project and pinned version, `file:line`, the mutant
  diff, the covering tests that failed to catch it, a plain-English explanation
  of what could break unnoticed, a **suggested test**, and a confidence rating.
- **M4.5** Every finding is classified: *probable real gap*, *equivalent
  mutant*, *intentional/untested-by-design*, or *moonbuggy bug*. Zero
  unclassified. The last category matters most — running against unfamiliar code
  is the best chance to find our own defects, and a finding we cause is a bug
  report on us.
- **M4.6** At least 10 findings across all five targets are triaged to this
  standard. If fewer than 10 survivors are found in total, that is recorded as
  the result rather than padded.
- **M4.7** Every *probable real gap* is verified by hand: apply the mutation
  locally, run the project's full suite, confirm it passes. An unverified
  finding is a guess.
- **M4.8** Any *moonbuggy bug* found gets a failing regression test in our suite
  before it is fixed.
- **M4.9** The findings document states plainly that nothing was reported
  upstream and that maintainers have not been contacted.
- **M4.10** Aggregate observations are recorded: which operators produced the
  most real findings, which produced the most noise, and what that suggests
  about the MVP operator set (§3.2) and about post-MVP operator priorities.

---

## 5. Sequencing

Recommended order, and why:

1. **M1.4 (robustness) first.** M4 runs moonbuggy against unfamiliar code, which
   is exactly where hostile-input bugs surface. Doing robustness first means M4
   produces findings about *those projects* rather than a stream of crashes
   about ours.
2. **M1.2 (property-based) next.** Cheap relative to its yield, and it hardens
   the generator that everything else depends on.
3. **M2 (performance research).** Independent of the rest; the profiler is
   useful to have before M4 runs on larger codebases.
4. **M4 (OSS hunt).** Needs M1.4 done to be pleasant, and feeds M1.3.
5. **M1.3 (differential at scale).** Reuses M4's five configured projects, so
   it is much cheaper afterwards than before.
6. **M3 (documentation).** Last, so the architecture pages describe what the
   code actually does after M2's changes rather than what it did before.

**Rough shape:** M1.4 and M1.2 are each a day or so; M2 and M4 are the
substantial ones; M3 is large but low-risk and highly parallelisable.

## 6. Risks worth naming now

- **OSS suites that are not deterministic (M4).** Some libraries have
  order-dependent or timing-sensitive tests. M4.1's green-baseline check catches
  the obvious cases; flakiness that appears only under mutation will look like
  `SUSPICIOUS` noise, which is why M1.4.3 comes first.
- **Benchmark overfitting (M2).** Tuning against one generated workload risks
  optimising for a shape nobody has. M2.1.3's three workload shapes and M2.4.2's
  no-regression rule are the guard.
- **Documentation drift (M3).** M3.3.10's executable examples are what keeps the
  prose honest; without it the tutorial rots the first time a flag changes.
