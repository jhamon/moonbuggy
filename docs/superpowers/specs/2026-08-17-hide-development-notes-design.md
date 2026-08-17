# Moving the development record out of the published docs

**Date:** 2026-08-17
**Status:** approved, not yet implemented

## The problem

`docs/index.md` carries a toctree section captioned "Project record" with nine
entries. Six of them are notes on how moonbuggy was built — milestone lists,
criterion-by-criterion status, spike write-ups, a performance-hypothesis
ledger. They are addressed to whoever was building the tool, not to whoever is
using it, and they occupy two thirds of a top-level section in the sidebar of a
site whose job is to explain mutation testing to a newcomer.

The remaining three — `benchmark-results`, `differential`, `oss-findings` — are
measured results *about* moonbuggy. A user has a reason to read them. They stay.

## Precedent

This decision has already been made once, for `docs/superpowers`, and the
reasoning is recorded in `docs/conf.py`:

> internal planning documents should not ship to users

That directory is kept in the tree and excluded from the build via
`exclude_patterns`. This spec applies the same treatment to the same kind of
material, and reuses the same mechanism rather than inventing a second one.

## Design

### 1. The move

`git mv` six files from `docs/` into a new `docs/development/`:

| file |
|---|
| `acceptance-criteria.md` |
| `next-milestones.md` |
| `phase-2-status.md` |
| `spike-a-findings.md` |
| `spike-b-findings.md` |
| `perf-hypotheses.md` |

Add `"development"` to `exclude_patterns` in `docs/conf.py`, extending the
existing comment that explains the `superpowers` exclusion rather than writing
a second comment saying the same thing. Sphinx then never builds these pages,
they never reach `docs/_build/html`, and nothing is published to GitHub Pages.
Because they are excluded, `-W` does not demand a toctree entry for them.

Add `docs/development/index.md`: a short page stating what the folder holds,
that it is a record rather than documentation, and that `docs/superpowers`
holds the same kind of material from the design phase. Someone arriving here
from a source-code comment should not have to infer the folder's purpose. This
page is excluded along with the rest.

### 2. `docs/index.md`

Remove the six toctree entries. The three that remain — `benchmark-results`,
`differential`, `oss-findings` — are no longer a "project record"; they are
measurements. Rename the caption to **Results**.

### 3. Link repair

Every reference below points at a path that will no longer exist, and each is
updated to `docs/development/<name>.md` (or the correct relative form):

**Repo root and build files**

- `README.md` — `acceptance-criteria`, `spike-a-findings`, `spike-b-findings`.
  Its `benchmark-results` link is unchanged.
- `Makefile` — three comments referencing `acceptance-criteria`,
  `perf-hypotheses`, `spike-b-findings`.

**Source and tests**

- `src/moonbuggy/__init__.py` — `acceptance-criteria`
- `src/moonbuggy/inmemory.py` — `spike-a-findings`
- `src/moonbuggy/runner.py` — `spike-a-findings`, `perf-hypotheses`
- `src/moonbuggy/coverage_pass.py` — `spike-b-findings`
- `src/moonbuggy/operators/__init__.py` — `perf-hypotheses`
- `src/moonbuggy/srcio.py` — `perf-hypotheses`
- `tests/test_coverage_pass.py` — `spike-b-findings`

**Inside the moved files**

Links to pages that stayed published become `../benchmark-results.md`,
`../differential.md`, `../oss-findings.md`. Links between moved files are
unchanged — they remain siblings.

`next-milestones.md` states file locations as literal acceptance criteria
(M2.2.1 names `docs/perf-hypotheses.md`; M1.3.4 and M4.4 name files that have
not moved). These are claims about where a file is, not predictions about what
would work, so the path in M2.2.1 is corrected and the criterion text is
otherwise left alone. The document's own rule — that wrong predictions stay on
the page — is about predictions, and is not weakened by fixing a path.

### 4. Cost to the published site

`docs/making-runs-fast.md` is a user-facing page and links to
`perf-hypotheses.md` twice, once as "has the measurements to prove it". Once
that page is off the site, those become dead links in the built HTML.

Both become GitHub blob URLs, so the claim keeps its receipt. Note that
`linkcheck_ignore` skips `https?://` by existing policy — a third-party outage
should not fail the build — so these two links will not be verified by
`make docs-linkcheck`. That is a real, if small, loss of coverage, and it is
the only place where a reader of the published site is worse off than before.

### 5. Deliberately unchanged

- **The `docs/superpowers` tree** references `../../acceptance-criteria.md` and
  friends. That directory is a frozen record of a past design session and is
  already excluded from the build. Rewriting paths inside it edits history for
  no reader. `docs/development/index.md` notes that those links are stale by
  design.
- **`scripts/differential.py` and `scripts/triage.py`** write into `docs/`.
  Both of their targets (`differential.md`, `oss-findings.md`) stay published,
  so neither script changes.
- **`scripts/oss_hunt.py`** comments on `oss-findings.md`, which has not moved.
- **`docs/writing-an-operator.md`** links `oss-findings.md`, which has not moved.

## Verification

1. `make docs` — passes with `-W`. Confirms no page lost a toctree entry and no
   internal cross-reference broke.
2. `make docs-test` — passes, with the same doctest count as before. None of
   the six pages contains an executable example (checked: their only mentions
   of doctests are prose), so moving them should not change what runs.
3. `make docs-linkcheck` — passes with `-W`. No broken internal links.
4. `grep -rn` across the repo for each of the six basenames, excluding
   `docs/_build`, `.git`, and `docs/superpowers`: every surviving hit names
   `docs/development/`.
5. `ls docs/_build/html` contains no HTML file for any of the six.

## Out of scope

Rewriting, condensing, or deleting the content of the six pages. This change
moves them and repairs the references; what they say is unchanged.
