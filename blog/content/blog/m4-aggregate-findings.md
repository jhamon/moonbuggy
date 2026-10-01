+++
title = "25 survivors, 15 real: what a defect hunt says about an MVP operator set"
date = 2026-09-26
author = "moonbuggy"
tags = ["mutation-testing", "oss-findings", "operators"]
description = "A run against five real Python libraries found three bugs in our own tool. What 25 surviving mutants say about test suites and about our operator set."
+++

# 25 survivors, 15 real: what a defect hunt says about an MVP operator set

The most useful thing our mutation tool found in five real Python libraries was three bugs in itself.

That's not false modesty and it's not a punchline. It's the honest read of the numbers, and the numbers are worth walking through, because they say something about what mutation testing is actually for.

## The setup

We ran moonbuggy against five real open-source libraries: tomli, humanize, sqlparse, more-itertools, and boltons. Each pinned at a tagged release, cloned read-only, in an isolated venv, with the project's own test suite green before any mutant ran. 1,313 mutants total. Scores ranged from 0.64 (boltons) to 0.91 (tomli).

The run produced 25 findings that survived triage. We classified every one, by hand, with a script that refuses to generate the findings document if any finding is left unclassified: 15 probable real gaps in the libraries' test suites, 8 equivalent mutants, and 2 things the projects had deliberately marked untested.

Then we verified all 25 by hand, mechanically: apply the mutation to the pinned checkout and run the project's *entire* suite, not just the tests moonbuggy selected. All 25 confirmed. The full suite still passed, so each survivor was real and not an artifact of test selection.

One caveat before anything else: **nothing here was reported upstream.** No issue, no pull request, no email to any maintainer. These libraries exist in this story as test targets, and every project was cloned read-only at a pinned tag. The findings are also a sample, not a census — we mutated named modules, not whole libraries, because mutating everything produces more survivors than anyone would triage.

## What a home fixture structurally cannot do

moonbuggy's own test suite runs against a small fixture: 22 mutants, built to exercise the tool's internals. It's good at what it does. It's also, by construction, blind to everything else.

The first pass of the hunt made that concrete. Four of the five targets came back wrong in ways the fixture could never have shown:

1. **pytest rootdir inference.** A project checked out inside another project with its own pytest config gets node ids relative to the outer directory. moonbuggy recorded those node ids, handed them back from the project root, and reported 233 of 233 tomli mutants `SUSPICIOUS` with no explanation. Every mutant in the target, unexplainable.
2. **Import-time mutations rebound only their own module.** `from .recipes import *` left every test using the re-exported name running unmutated code. The tool reported a confident `SURVIVED` for a mutation the project's own suite catches.
3. **`all_tests()` came from coverage contexts.** A module never *called* during a test contributed no coverage contexts, so a module-level mutant that widened the test selection to "the whole suite" ran nothing and reported `SURVIVED` with `tests_run=0`.

Plus a fourth problem in the harness rather than the tool: boltons was first measured with bare `pytest` while its real test command is `pytest --doctest-modules`, so four survivors were reported that the project's own CI catches. moonbuggy grew `--pytest-arg` as a result, because a project whose test command isn't bare `pytest` was previously unmeasurable.

None of this was visible on our own 22-mutant fixture, and none of it could have been. The fixture is one project, in one layout, with one test command. The failures were all in the seams between moonbuggy and *someone else's* project structure. That's the structural point: a tool tested only against its own fixture has no contact with the variations that break it. Every real project carries some variation the fixture doesn't.

We fixed all four, and each fix went in test-first: a regression test that fails without the fix, committed before the fix. Two of the three tool defects are covered by regression tests in `tests/test_rootdir.py` and `tests/test_module_level_aliases.py`. The hand-verification pass is the check that the fixes hold: an earlier verification run refuted 2 of 20 findings, and both turned out to be false `SURVIVED`s in moonbuggy — real mutations that the projects' suites caught but our tool mislabeled. Both were fixed (commit `c526b5c`) with regression tests. That's why the final classification table shows zero moonbuggy bugs: not because we assumed there were none, but because we found ours, fixed them, and re-ran.

Running against unfamiliar code turned out to be the cheapest bug-finding this project has done.

## What the 25 survivors looked like

The 15 probable real gaps cluster in two places, and the clustering is the interesting part.

**Parameters with defaults that no test overrides.** boltons' `split()` accepts a `maxsplit` argument. Four of the five boltons findings are mutations to the maxsplit path, and every one survives because nothing in the suite passes a non-zero maxsplit and checks the result. One mutant flips the separator function to "match everything" once maxsplit is reached, so the remainder splits into one group per element — the exact opposite of what maxsplit means. It survives with zero covering tests. The parameter's documented main use is simply unverified.

**Error paths that nothing provokes.** Four of the fifteen were in code that only runs when something has already gone wrong. tomli computes the column of a parse error by searching backwards for a newline from index 0; mutate the 0 to a 1, and a malformed TOML document starting with a blank line crashes the error reporter instead of reporting the error. Another mutant makes the parser walk backwards instead of forwards when naming an illegal character in an error message, and nothing in the suite exercises that branch. This is exactly the code least likely to be exercised and most annoying to have wrong: the tests pass until the day a user hands the library input it's never seen, and the diagnostic itself is broken.

There were also 8 equivalent mutants, and they're worth a sentence rather than a wave-off. Humanize's `if math.isinf(value) and value < 0` widened to `<= 0` adds only the case `value == 0`, which the `isinf` conjunct already excludes. A tool that reported these as findings would be generating noise, and the honest classification of a mutation-testing run has to include "no test can distinguish this, and none should try." Roughly a third of our survivors were equivalent mutants — that ratio is itself information about the operator set, which is the next section.

## What the operator mix says

moonbuggy's MVP ships five mutation operators, and the 25 findings amount to a report card on each.

`constant_int` is both the workhorse and the noisiest operator at once: 8 real gaps and 6 pieces of noise, more than every other operator combined on both counts. Every off-by-one in a slice index or a boundary came from it, and so did most of the equivalent mutants. It earns its place. It also argues for being narrowed, because incrementing a constant used as a length or an opaque flag value is far more often equivalent than incrementing one used as a boundary, and the operator currently can't tell those apart. That's a plausible post-MVP refinement: same operator, filtered by how the constant is used.

`arithmetic_swap` had the best ratio: 3 real gaps, no noise. All three were in index arithmetic, where a sign or a division mode is load-bearing. The more-itertools `nth_combination` case is the vivid one — flip `index += c` to `index -= c` and every negative index raises IndexError instead of wrapping the way a sequence index does. The docstring shows the behavior; no test runs the example. There's also the integer-division mutant where `//` becoming `/` works fine for small pools and then, past 2^53, silently selects the wrong combination — no error, a different answer. The tests use small pools only.

`comparison_swap` split evenly, 2 and 2, and its noise had a recognizable shape: both equivalent mutants were comparisons sitting behind a guard that already constrains the value, like `isinf(x) and x < 0`. A guard-constrained comparison is a plausible future suppression heuristic, because the pattern is mechanical enough to detect.

`boundary` produced nothing at all on these libraries. `range(x)` with a single argument turns out to be rare in code written by people who reach for `enumerate` and comprehensions. We'd rather say that plainly than defend an operator that earned its slot on intuition: post-MVP operator effort is better spent on slice bounds and default arguments, because that's where these libraries' untested behavior actually was.

None of this means the MVP operator set is wrong. It means one run against five real libraries is enough to rank the operators by yield, and the ranking disagrees with intuition in at least one place. That's the argument for running against unfamiliar code in the first place — you learn what your tool actually does, not what you designed it to do.

## Limits

This was five libraries and named modules, not the ecosystem. The scores (0.64 to 0.91) measure the libraries' test suites, not moonbuggy's speed or quality, and we're not presenting them as a benchmark of anything. The operator observations come from 25 triaged findings, which is enough to rank five operators against each other and not enough to settle operator design in general. And the three defects in moonbuggy are fixed and regression-tested on our side; whether the libraries' 15 probable real gaps are worth fixing is those projects' call, which is one reason among several that nothing was reported upstream.

The tool got better at its job by being pointed at strangers' code. We'll keep doing that, and we'll keep writing down what breaks — ours first.

---

---

## Sources - Run data, scores, mutant counts, and per-finding details (diffs, classifications, verification results, suggested tests): the project's aggregate findings record (`docs/oss-findings.md` in the repo, read 2026-09-26). The public rendering of the record is deployed at https://jhamon.github.io/moonbuggy/oss-findings.html.
- Five targets table: tomli 2.4.1 (233 mutants, 0.9103), humanize 4.16.0 (160, 0.7895), sqlparse 0.6.0 (157, 0.75), more-itertools v11.1.0 (329, 0.6388), boltons 26.1.0 (434, 0.6349); 1313 total; wall clocks per target — `docs/oss-findings.md`, "Runs" section.
- Classification counts (15 / 8 / 2 / 0, 25 triaged, zero unclassified, triage script fails otherwise), hand-verification method and results (25 confirmed, 0 refuted; earlier run refuted 2 of 20, both false SURVIVEDs, fixed commit `c526b5c`, regression tests in `tests/test_module_level_aliases.py`), and the nothing-reported-upstream statement — `docs/oss-findings.md`, "Classification" and "Hand verification" sections.
- The three moonbuggy defects (rootdir inference / import-time rebind / coverage-context all_tests), the harness `--doctest-modules` problem, `--pytest-arg`, and regression-test-first receipts (`tests/test_rootdir.py`, `tests/test_module_level_aliases.py`): the moonbuggy project's internal development status record, M4 section, read 2026-09-26.
- Operator aggregate observations (constant_int 8 real / 6 noise; arithmetic_swap 3/0; comparison_swap 2/2; boundary 0; clustering into default-override params and error paths; 4 of 15 in error paths; design-section 3.2 reference): `docs/oss-findings.md`, "Aggregate observations" section.
- moonbuggy version at run time: `0.0.1+f11720b-dirty` (`docs/oss-findings.md` header).
- Constraint: no upstream contacting, no claims anything was reported to maintainers — stated in the source docs ("Nothing was reported upstream") and in the task brief; preserved verbatim in spirit above.
