# Phase 2 status

Criterion-by-criterion record for [next-milestones.md](next-milestones.md).
Every entry says what was done and how to check it. Where a criterion was met
differently from the plan, or not met, that is stated here rather than left for
a reader to discover.

**How to check the lot:** `make check-all`, plus `make docs`, `make docs-test`,
`make docs-linkcheck`, `make profile`, `make oss-hunt`, `make check-differential`.

---

## M1.2 — Property-based testing

`make check-properties` · [tests/test_properties.py](../tests/test_properties.py)

| # | criterion | status |
|---|---|---|
| M1.2.1 | every mutant compiles | met |
| M1.2.2 | mutation never changes string or comment content | met, **scoped** — multiset of string constants and of comment tokens, before and after; string equality is relaxed to "nothing invented, nothing altered" for a mutation that deletes a whole statement. See below |
| M1.2.3 | ids stable and unique | met |
| M1.2.4 | splicing round-trips | met — **found a bug**: trailing whitespace was dropped |
| M1.2.5 | line attribution correct | met — and exactly one line differs |
| M1.2.6 | scope classification sound | met — **found two bugs**, oracle reads CPython's own line tables |
| M1.2.7 | ≥500 examples, non-trivial programs | met — 500 per property; a separate test asserts every named feature is reachable |
| M1.2.8 | failures become regression examples | met — three `@example` cases, each with the bug it found named, plus one pinning the M1.2.2 narrowing |

**Bugs found.** `def f(p=1 + 2)` and `@_tagged(1 + 1)` at module level were
classified as deferred, so selection ran no tests for them and reported false
survivors. Both confirmed against the pre-fix walk before the fix landed.
Scope classification is now derived from function-body line ranges and is
deliberately conservative about lambdas: widening costs time, narrowing costs
correctness.

**Where this is narrower than the plan.** M1.2.6 asserts one direction only —
claiming module level when the line is not is merely wasteful, and the property
lets that pass. Stated in the test's own docstring.

M1.2.2 is the second. The properties ran against the `default` tier only until
they were widened to `all`; run against `statement_deletion`, the exact
string-multiset equality fails by construction, because replacing
`x = "hello"` with `pass` removes a string literal along with the statement it
belonged to. The criterion the property exists to defend is Phase 1's C2, *no
mutation applied inside a string literal*, and deleting a statement is not
editing within a string — so the property was scoped rather than the operator
changed. It now asserts, for every operator, that no string or comment content
is ever invented or altered, and separately that no string is removed except by
a mutation that replaces a whole statement with `pass`. The exemption is keyed
on the shape of the mutation, not on an operator name. Stated in the test's own
docstring.

## M1.3 — Differential against mutmut at scale

`make check-differential` · [differential.md](../differential.md)

| # | criterion | status |
|---|---|---|
| M1.3.1 | ≥10 projects, per-mutant correspondence table | met on count, **not as specified** — see below |
| M1.3.2 | every disagreement classified, zero unclassified | met — the harness exits non-zero otherwise |
| M1.3.3 | moonbuggy bugs get a failing regression test first | vacuous — no disagreement was classified *moonbuggy bug* |
| M1.3.4 | table checked in with counts per category | met |
| M1.3.5 | re-runnable, reports drift | met — the table is regenerated each run |

**Not as specified.** M1.3.1 names the five M4 libraries as five of the ten
projects. They are not included. mutmut cannot be pointed at an arbitrary
checkout: it rewrites the project into a `mutants/` tree and requires the
project's pytest configuration to be replaced with one that reads from it, and
running it needs a virtualenv per target carrying both mutmut and that project's
own dependencies. The count is made up with generated projects instead, which is
a weaker substitution — generated code has no decorators, classes, closures or
third-party imports, so it cannot surface the disagreements those produce. Said
plainly at the top of the generated report as well as here.

## M1.4 — Robustness and fault injection

`make check-robustness` · [tests/test_robustness.py](../tests/test_robustness.py)

Every row of the M1.4 table is one automated test (M1.4.11), none produces a
traceback as its user-facing output (M1.4.12), and the crash-recovery check
kills a run with `SIGKILL` mid-flight (M1.4.13).

| # | scenario | behaviour |
|---|---|---|
| M1.4.1 | syntax error in a source file | named, skipped, other files still processed |
| M1.4.2 | import-time side effects | handled; module-level mutant still killed |
| M1.4.3 | flaky test | `SUSPICIOUS` for its mutants, confident statuses elsewhere |
| M1.4.4 | red baseline | refused, exit 2, no results claimed |
| M1.4.5 | threads | completes; a non-terminating loop is `TIMEOUT`, not a survivor |
| M1.4.6 | `sys.exit()` / `os._exit()` | classified, never lost |
| M1.4.7 | unusual encoding | declared encodings handled; undeclared refused by name |
| M1.4.8 | 10k lines, deep nesting | iterative walk; sites past the limit skipped *and announced* |
| M1.4.9 | empty project / no tests / no source | exit 2 with an actionable message |
| M1.4.10 | side-effecting conftest fixtures | selection verified against the naive oracle |

**Design decisions worth knowing.** The flaky probe is one extra unmutated suite
run, on by default, `--flaky-probe 0` to disable. It costs 3.6–5.2% of wall
clock and buys the M1.4.3 guarantee. The M1.4.3 fixture alternates
deterministically rather than randomly, so the *test* is reproducible; that is
the worst case for a single probe, not the easiest.

## M2 — Performance research

`make profile`, `make ab` · [perf-hypotheses.md](perf-hypotheses.md)

| # | criterion | status |
|---|---|---|
| M2.1.1 | phase breakdown | met — nine named phases plus interpreter startup |
| M2.1.2 | phases ≥95% of wall clock, "other" explicit | met — 98.6–100% across three shapes |
| M2.1.3 | three workload shapes reported separately | met |
| M2.1.4 | profiling overhead ≤20%, measured | met — 1.00–1.01x |
| M2.2.1 | register with phase, measured cost, prediction, risk | met — six entries, all predictions written before implementing |
| M2.2.2 | ranked, worked in rank order | met — one documented override (H1, deferred on correctness risk) |
| M2.2.3 | actuals recorded, including abandoned attempts | met |
| M2.2.4 | ≥6 registered, ≥4 attempted | met — 6 registered, 5 attempted |
| M2.3.1 | `make ab`, ≥7 runs, median/min/95% CI | met |
| M2.3.2 | winner only on non-overlapping intervals | met |
| M2.3.3 | identical refs → "indistinguishable", automated | met — unit tests plus one end-to-end case using a real commit twice |
| M2.3.4 | performance commits cite an A/B result | met |
| M2.4.1 | improvement **or** written finding of irreducibility | met via the second option, honestly |
| M2.4.2 | no shape regressed >10% | met — no shape regressed at all |
| M2.4.3 | `check-oracle` and `check-all` still pass | met |

**Outcome.** One shape improved by a statistically significant 1.5%; the
remaining cost is irreducible without a named architectural change. Roughly 20%
of every run is per-mutant process setup, and removing it means running several
mutants in one process — the one idea in the register whose failure mode is a
confident wrong status rather than a slow run. That trade is stated rather than
implied.

**The most useful output is the scoreboard on the predictions themselves:** one
right, one wrong about the mechanism, one wrong about the premise, one right
about the speed and wrong about whether speed was the question.

## M3 — Sphinx documentation

`make docs`, `make docs-test`, `make docs-linkcheck`

| # | criterion | status |
|---|---|---|
| M3.1.1 | `make docs` builds HTML | met |
| M3.1.2 | `-W`, zero warnings | met |
| M3.1.3 | linkcheck passes on internal links | met — external links deliberately not checked |
| M3.1.4 | `docs` extra installs everything | met |
| M3.1.5 | nothing published | met |
| M3.2.1 | 100% public docstring coverage, automated, wired into `make docs` | met — interrogate |
| M3.2.2 | parameters, returns, deliberate exceptions documented | met — pydoclint checks the docstring against the signature |
| M3.2.3 | one style, enforced by the build | met — Google, with NumPy support switched off so a stray NumPy docstring renders wrongly rather than quietly establishing a second convention |
| M3.2.4 | private helpers exempt | met |
| M3.2.5 | rationale survives into the rendered site | met |
| M3.3.1–9 | nine narrative pages, each with a stated audience | met |
| M3.3.10 | every example executable and verified | met — 36 doctests, including ones that build a project and run the real CLI |
| M3.3.11 | quickstart validated on a machine with no prior install | **partially met** — see below |
| M3.3.12 | API reference generated by autodoc | met |

**M3.3.11, honestly.** `make check-fresh-install` builds a wheel, installs it
into a clean virtualenv, and runs bare `moonbuggy` on a project it has never
seen — which is the mechanical part of the criterion. What has *not* been done
is a human following the quickstart page on a machine that never had moonbuggy
on it. That is the part the criterion is really about, and it cannot be
self-certified.

## M4 — Open-source defect hunt

`make oss-hunt` · [oss-findings.md](../oss-findings.md)

| # | criterion | status |
|---|---|---|
| M4.1 | pinned, isolated venv, green baseline first | met |
| M4.2 | all five complete, or blocker documented and replaced | met — all five complete; two needed harness fixes first, both recorded |
| M4.3 | mutant count, breakdown, wall clock, score, version | met |
| M4.4 | findings document to the stated standard | met |
| M4.5 | every finding classified, zero unclassified | met — the generator fails otherwise |
| M4.6 | at least 10 findings triaged | met — 25 |
| M4.7 | every real gap verified by hand | met — mechanically, via the project's full suite |
| M4.8 | moonbuggy bugs get a failing regression test first | met — three of them |
| M4.9 | states plainly that nothing was reported upstream | met |
| M4.10 | aggregate observations on operators | met |

Five targets, 1313 mutants, scores from 0.64 to 0.91. 25 findings triaged:
15 probable real gaps, 8 equivalent mutants, 2 intentional. All 25 confirmed by
hand verification — an earlier pass refuted 2 of 20, and both were false
SURVIVEDs in moonbuggy.

The standing constraint held: **nothing was posted anywhere and no maintainer
was contacted.**

The most valuable result was not a finding about any of the five libraries. It
was three defects in moonbuggy, none of which our own fixture could have
surfaced:

1. **pytest rootdir inference.** A project checked out inside another project
   with its own pytest config gets node ids relative to the outer directory.
   moonbuggy recorded those, handed them back from the project root, and
   reported 233 of 233 tomli mutants `SUSPICIOUS` with no explanation.
2. **Import-time mutations rebound only their own module.** `from .recipes
   import *` left every test using the re-exported name running unmutated code —
   a confident `SURVIVED` for a mutation the project's own suite catches.
3. **`all_tests()` came from coverage contexts.** A module never *called* during
   a test contributed no contexts, so a module-level mutant that widens to "the
   whole suite" ran nothing and reported `SURVIVED` with `tests_run=0`.

Each has a regression test that fails without the fix:
[test_rootdir.py](../tests/test_rootdir.py),
[test_module_level_aliases.py](../tests/test_module_level_aliases.py).

A fourth problem was in the harness rather than the tool, and is worth the same
candour: boltons was first measured with bare `pytest` while its real test
command is `pytest --doctest-modules`, so four survivors were reported that the
project's own CI catches. moonbuggy grew `--pytest-arg` as a result, because a
project whose test command is not bare `pytest` was previously unmeasurable.
