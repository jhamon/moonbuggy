# moonbuggy — CI and release (Phase 3)

**Status:** design approved, not implemented.
**Baseline:** Phase 0, Phase 1, and Phase 2 complete. 135+ tests, all criteria in
[acceptance-criteria.md](../../acceptance-criteria.md) met, four Phase 2
milestones landed (see [phase-2-status.md](../../phase-2-status.md)).

Three milestones, written the same way as the Phase 1 and Phase 2 criteria:
every acceptance criterion is a claim an evaluator can mark clearly true or
clearly false by running something, not by forming an opinion.

**Unlike Phase 2, these are ordered.** M5 must land before M6, and M6 before
M7. §4 explains why.

---

## Where the project starts

Facts as of this design, not assumptions:

- No `.github/` directory. Nothing runs automatically. Every gate in the
  `Makefile` runs because a person remembered to run it.
- No git tags. `version = "0.0.1"` in `pyproject.toml`, never published to any
  index.
- No linter, formatter, or type checker configured. Quality is enforced by the
  test suite, the docstring gates (`interrogate`, `pydoclint`), and review.
- `docs/` builds with `make docs` into `docs/_build/html` and is published
  nowhere — M3.1.5 states this explicitly.
- `requires-python = ">=3.12"`. The engine runs mutants in forked worker
  processes, so Windows is out of scope until someone does that work
  deliberately.

The end state: automated gates on every change, and `pip install moonbuggy`
working for a stranger.

---

## M5 — Static quality baseline

**Goal:** make lint and type errors a thing CI *can* gate on. Neither tool has
ever run over this codebase, so enabling them in a workflow first would produce
a wall of pre-existing violations and a permanently red `main`. This milestone
absorbs that cost once, deliberately, with no automation attached — which is
also why it is a separate milestone rather than a subsection of M6. It is the
only open-ended chunk of work in Phase 3, and burying it inside a milestone
that otherwise consists of writing workflow files would misrepresent its size.

### M5.1 Ruff lint

Configuration lives in `pyproject.toml` alongside the existing
`[tool.interrogate]` block. The starting rule set is `E,F,W,I,UP,B,SIM` — chosen
for this code rather than inherited from `ALL` and then riddled with
suppressions.

- **M5.1.1** `make lint` exits 0 on a clean checkout, over `src/`, `tests/`,
  and `scripts/`.
- **M5.1.2** No `# noqa` comments exist anywhere in the repository. A violation
  is either fixed or its rule is disabled repo-wide.
- **M5.1.3** Every disabled rule carries a one-line comment giving the reason it
  is disabled.

### M5.2 Ruff format

`line-length = 88`, ruff's default. The existing prose comments and docstrings
mostly wrap nearer 79 and the formatter leaves them alone, so in practice the
extra width is available to code without rewrapping the commentary.

- **M5.2.1** `make format-check` (`ruff format --check`) exits 0 on a clean
  checkout.
- **M5.2.2** The reformat lands as its own commit, containing no behavioural
  change, so `git log -p` over the reformat is skimmable.
- **M5.2.3** `make check-all` passes identically before and after the reformat
  commit.

### M5.3 Type checking

mypy in `--strict` mode over `src/moonbuggy` only. Not `tests/`, not `scripts/`
— those are harnesses and benchmark drivers, and annotating them buys ceremony
rather than safety. mypy rather than pyright because it configures from
`pyproject.toml` like every other gate here and needs no Node toolchain on a
runner.

- **M5.3.1** `make typecheck` exits 0 under `--strict` over `src/moonbuggy`.
- **M5.3.2** No bare `# type: ignore`. Every one carries an error code and a
  trailing comment explaining why it is there.
- **M5.3.3** Every `Any` appearing in a public signature is deliberate and
  carries a comment saying what it stands for.

**Known risk.** Reaching `--strict` on an AST-and-subprocess-heavy codebase can
surface genuine design ambiguity in return types, and this is the one criterion
here that could take days rather than hours. If it does, the documented
fallback is to land default-mode mypy in M5 and open `--strict` as a follow-on
milestone — recorded as a deviation, not silently dropped. Partial typing that
is never tightened produces false confidence, so the fallback must be written
down as unfinished work rather than treated as met.

### M5.4 Wiring

- **M5.4.1** `make lint`, `make format-check`, and `make typecheck` exist, each
  documented with a comment in the same style as the surrounding targets.
- **M5.4.2** All three join the `check-all` target.
- **M5.4.3** A `lint` extra under `[project.optional-dependencies]` pins ruff
  and mypy, so a clean venv can run the new targets from a fresh checkout.

---

## M6 — Continuous integration

**Goal:** every gate that exists runs automatically, on a cadence matched to its
cost, and a failure reaches a person.

Gates are tiered because their costs differ by orders of magnitude: `make test`
is seconds, `check-properties` is about two minutes, `check-oracle` and
`check-robustness` spawn a process per mutant, and `check-differential` and
`oss-hunt` clone repositories and run mutmut across ten projects.

The release tier is *defined* in M7 and invoked by M7's workflow. The tiering
table below is the single place that records which gate runs when.

| gate | tier |
|---|---|
| `test`, `lint`, `format-check`, `typecheck`, `docs` | PR (every matrix leg) |
| `check-fresh-install`, `docs-test` | PR (3.12 leg only) |
| `check-oracle`, `check-properties`, `check-robustness`, `check-mutmut`, `docs-linkcheck` | nightly |
| `check-differential`, `oss-hunt` | weekly |
| all of the above, plus `check-spike` | release (M7.2) |

### M6.1 PR tier (blocking)

Triggers on `pull_request` and on `push` to `main`. Matrix:
`ubuntu-latest × {3.12, 3.13, 3.14}`.

`check-fresh-install` and `docs-test` run on the 3.12 leg only — both spawn real
installs and neither is version-sensitive. `docs-linkcheck` is nightly, not PR:
it hits the network, and an upstream site being down must not block a PR.

- **M6.1.1** The workflow's p50 wall clock is under 10 minutes, checked against
  the Actions timing view over at least ten runs.
- **M6.1.2** Every job sets `timeout-minutes`, so a hung mutant run fails in
  bounded time rather than consuming the runner limit.
- **M6.1.3** A `concurrency` group with `cancel-in-progress` is set, so a
  force-push supersedes its own in-flight run.
- **M6.1.4** Dependencies are cached, with `pyproject.toml` in the cache key.

### M6.2 Nightly tier

Scheduled daily: `check-oracle`, `check-properties`, `check-robustness`,
`check-mutmut`, `docs-linkcheck`.

### M6.3 Weekly tier

Scheduled weekly: `check-differential` and `oss-hunt`. Both are network-bound
and the longest-running gates in the repository, and their inputs are pinned
upstream repositories that change slowly.

- **M6.3.1** Both scheduled workflows also accept `workflow_dispatch`, so a
  maintainer can run them on demand without waiting for the schedule.
- **M6.3.2** A failure in M6.2 or M6.3 opens a GitHub issue containing the run
  URL. A repeat failure comments on the existing open issue rather than opening
  a second one. A scheduled job whose only failure signal is an email nobody
  reads is not a gate.

### M6.4 Docs publishing

Sphinx output deploys to GitHub Pages on merge to `main`, using the official
Pages actions.

- **M6.4.1** The published site is reachable and its front page matches the
  `main` build.
- **M6.4.2** `README.md` and `docs/index.md` link to the published site.
- **M6.4.3** M3.1.5 in [next-milestones.md](../../next-milestones.md) — "nothing
  built is published anywhere" — is annotated as superseded by M6.4, with a
  pointer to this spec. That criterion was a scope fence for Phase 2, not a
  principle, but the two documents must not be left disagreeing.

### M6.5 Supply-chain hygiene

- **M6.5.1** Every third-party action is pinned to a full commit SHA, never a
  floating tag, with the human-readable version in a trailing comment.
- **M6.5.2** Every job declares an explicit minimal `permissions:` block.
- **M6.5.3** `actions/checkout` sets `persist-credentials: false` in every job
  that does not need to push.

### M6.6 Branch protection

- **M6.6.1** `main` requires the M6.1 checks to pass before merge.
- **M6.6.2** Verified by opening a pull request that fails `make lint` and
  confirming the merge button is disabled.

---

## M7 — Release pipeline

**Goal:** `pip install moonbuggy` works for a stranger, and the path from tag to
index is one workflow with no human step that can be performed incorrectly.

The first release is `v0.1.0`. The current `0.0.1` was never published, so no
version history needs preserving.

Versioning is tag-driven with the number held in `pyproject.toml`: a maintainer
bumps `version`, commits, and tags to match. The workflow refuses to publish if
the two disagree. This keeps the build backend unchanged — no `hatch-vcs`
dependency — at the cost of one consistency check, which M7.1.1 makes
mechanical.

### M7.1 Trigger and preflight

Fires on a pushed tag matching `v*`. Before anything is built, each of the
following is a separate step with its own failure message, so the log states
which one tripped:

- **M7.1.1** The tag matches `version` in `pyproject.toml` exactly
  (`v0.1.0` ↔ `0.1.0`).
- **M7.1.2** The tagged commit is an ancestor of `main`.
- **M7.1.3** `CHANGELOG.md` contains a section for this version with at least
  one entry.
- **M7.1.4** The version does not already exist on PyPI.

### M7.2 Release gate

- **M7.2.1** The full suite runs against the tagged commit: `make check-all` —
  which after M5.4.2 covers `test`, `lint`, `format-check`, `typecheck`,
  `check-oracle`, `check-spike`, `check-properties`, `check-robustness`,
  `check-mutmut`, and `check-fresh-install` — plus the five gates `check-all`
  does not include: `docs`, `docs-test`, `docs-linkcheck`,
  `check-differential`, and `oss-hunt`. Releases are rare enough to afford
  this, and "the last nightly was green" is a claim about a different commit.

### M7.3 Build and reproducibility

`python -m build` produces an sdist and a wheel, with `SOURCE_DATE_EPOCH` pinned
to the tagged commit's date.

- **M7.3.1** Two independent builds of the same tag, in separate jobs on fresh
  runners, produce byte-identical artifacts, compared by sha256. Without the
  pinned epoch this fails on embedded timestamps, which is why it is stated as a
  criterion rather than assumed.

### M7.4 Wheel smoke test

The built wheel — not the source tree — installs into a clean venv on a runner
with no checkout on `sys.path`, and the run proceeds end to end on an unseen
project.

- **M7.4.1** `scripts/check_fresh_install.sh` grows a flag to install a
  supplied wheel instead of running `pip install .`, so one script serves both
  criterion H1 locally and the release path in CI. One script with two callers,
  not a second copy that drifts.
- **M7.4.2** `moonbuggy --version` reports the tag's version.
- **M7.4.3** The full smoke run produces the expected `.moonbuggy/` output on
  the unseen project, exactly as H1/H2 require locally.

This is the deliberate substitute for a TestPyPI dry run. It catches a broken
wheel, a missing package directory, a bad entry point, or an unlisted
dependency before the version number is spent — and PyPI version numbers can
never be reused, only yanked. A second index and its credentials were judged not
worth the machinery given this check covers the same failure modes.

### M7.5 Publish

- **M7.5.1** Publishing uses PyPI trusted publishing over OIDC. No long-lived
  API token exists in repository secrets.
- **M7.5.2** The publish job runs in a GitHub `pypi` environment with a required
  reviewer, so the one irreversible step has a single deliberate human approval
  after every preceding gate has passed.
- **M7.5.3** Build provenance attestations are generated and uploaded with the
  artifacts.

### M7.6 GitHub Release

- **M7.6.1** `CHANGELOG.md` follows Keep a Changelog, is maintained by hand, and
  has an `Unreleased` section between releases.
- **M7.6.2** The workflow extracts the section matching the tag verbatim and
  posts it as the GitHub Release body. Release notes stay something a person
  wrote; the workflow only moves them.
- **M7.6.3** The sdist and wheel are attached to the release.

### M7.7 Post-publish verification

- **M7.7.1** After publishing, a job installs `moonbuggy==<version>` from real
  PyPI into a clean venv and reruns the M7.4 smoke test. It cannot undo a bad
  release, but it converts "we think it worked" into a check.

### M7.8 Runbook

- **M7.8.1** `docs/releasing.md` documents how to cut a release, what each gate
  means, and what to do when one fails.
- **M7.8.2** It states explicitly that a bad release is yanked, never replaced,
  and that the fix ships as the next patch version.

---

## 4. Ordering

The three milestones are sequential, and the dependencies are real rather than
stylistic:

- **M5 before M6.** M6.1 gates pull requests on `make lint` and `make
  typecheck`. Turning that on before M5 has fixed the pre-existing violations
  makes the first pull request red for reasons unrelated to its contents, and
  M6 could not be marked met.
- **M6 before M7.** M7.2's release gate reuses the tiering and the workflow
  structure M6 establishes. Writing the release workflow first would mean
  writing that structure twice.

Within a milestone, subsections may be done in any order.

## 5. Out of scope

Named here so their absence is a decision rather than an oversight:

- **Windows support.** The engine forks worker processes. Supporting Windows is
  engine work, not CI work, and belongs in its own milestone.
- **TestPyPI.** Replaced by M7.4, for the reasons given there.
- **Coverage gating on moonbuggy's own suite.** This project's whole argument is
  that line coverage is the weaker signal and mutation score the stronger one.
  Gating on the weaker one here would be incoherent.
- **Self-mutation in CI.** Still blocked for the reason recorded in the vacant
  M1.1 slot: the code under mutation would be the mutation engine itself.
- **Automated changelog from commit messages.** M7.6.1 keeps release notes
  hand-written; imposing a commit-message convention on the repository was not
  wanted.
