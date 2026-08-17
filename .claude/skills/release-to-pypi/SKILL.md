---
name: release-to-pypi
description: Use when cutting a moonbuggy release — publishing to PyPI, bumping the version, tagging, or when the user says "ship a release", "cut a release", "release 0.2.0", or "publish to PyPI".
---

# Release moonbuggy to PyPI

## Overview

Publishing is done by `.github/workflows/release.yml`, which triggers on a pushed
`v*` tag. Everything done locally is preparation for that tag: bump the version,
cut the changelog section, commit to `main`, tag, push. `docs/releasing.md` is the
human runbook; this skill is the same process, executed.

**A published version is spent forever.** PyPI never allows re-uploading a version
number, so the tag push is the point of no return for that number. Get explicit
approval before pushing the tag.

## Version facts for this repo

| Thing | Where |
|---|---|
| The only version literal | `src/moonbuggy/__init__.py` (`__version__ = "x.y.z"`) — `pyproject.toml` reads it via `[tool.hatch.version]`; do **not** add a version to `pyproject.toml` |
| Changelog | `CHANGELOG.md`, Keep a Changelog format: `## [Unreleased]` on top, then `## [x.y.z] - YYYY-MM-DD` sections |
| Tag format | `vx.y.z` (leading `v`; the workflow strips it) |
| Local preflight | `python scripts/check_version_consistency.py vX.Y.Z` — the exact check CI runs first |

## Workflow

### 1. Check the ground is safe

Run these and stop if any fails:

```bash
git rev-parse --abbrev-ref HEAD && git status --porcelain && git fetch origin && git log --oneline origin/main..HEAD && git log --oneline HEAD..origin/main
```

Required: on `main`, clean tree, no divergence from `origin/main`. The workflow's
`preflight` rejects a tag whose commit is not an ancestor of `main`.

### 2. Recommend a version

Gather the evidence before asking anything:

```bash
git describe --tags --abbrev=0 && git log --oneline "$(git describe --tags --abbrev=0)"..HEAD
```

Also read the `## [Unreleased]` section of `CHANGELOG.md` — it is the better
signal, since it is what a user will actually read.

Then classify against the **current** version, remembering this project is `0.x`:

- **major** — only once the project intends `1.0`. In `0.x`, a breaking change is
  a *minor* bump, not major. Do not propose `1.0.0` unless the user has said so.
- **minor** — new user-visible capability, new CLI flag or output, changed
  behaviour, or a breaking change while in `0.x`.
- **patch** — bug fixes, performance work with identical results, docs, internals.

Present the recommendation with the reason, using `AskUserQuestion` with the
recommended level as the first option, labelled with the actual number
(e.g. "patch — 0.1.1 (Recommended)"). One question, three options. Give the
one-line rationale in each option's description so approving is a single click.

### 3. Apply the bump

1. Edit `__version__` in `src/moonbuggy/__init__.py`.
2. In `CHANGELOG.md`, insert `## [x.y.z] - YYYY-MM-DD` (today's date from
   `date +%F`, not from memory) below `## [Unreleased]`, and move every entry
   currently under `Unreleased` into it. Leave `## [Unreleased]` in place, empty
   — the next round of work writes under it, and `docs/releasing.md` expects it.
3. Verify before committing:

```bash
python scripts/check_version_consistency.py vX.Y.Z
```

That single command covers the tag/version/changelog agreement that CI checks
first. An empty or missing changelog section fails the release.

### 4. Commit and push `main`

```bash
git add src/moonbuggy/__init__.py CHANGELOG.md && git commit -m "Release vX.Y.Z" && git push origin main
```

The bump must be on `origin/main` *before* the tag is pushed.

### 5. Tag — confirm first

Ask the user to confirm, naming the version and that this starts the PyPI
publish. On a clear yes:

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

### 6. Hand off to the workflow

Tell the user what happens next and offer to watch it:

```bash
gh run watch "$(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

The `gate` job runs the full `check-all` suite plus docs and differential checks
and can take hours. **The `publish` job then pauses for a required reviewer in the
`pypi` GitHub environment — only the user can approve it.** Say so explicitly;
do not wait silently for a job that cannot proceed without them.

After `publish`, the `release` job creates the GitHub Release from the changelog
section and `verify` installs from real PyPI and smoke-tests it.

## If something fails

Anything failing **before** `publish` succeeds means nothing was uploaded — the
version number is still free:

```bash
git push --delete origin vX.Y.Z && git tag -d vX.Y.Z
```

Then fix on `main`, push, and re-tag the same version.

Once `publish` has succeeded, that number is spent. Do not try to re-upload it,
and do not delete-and-retag. Yank the bad version on PyPI and ship the fix as the
next patch. See `docs/releasing.md` for the full policy.

## Common mistakes

| Mistake | Why it breaks |
|---|---|
| Adding `version = ` to `pyproject.toml` | Version is dynamic, read from `__init__.py`; a second literal drifts |
| Tagging without the leading `v` | `release.yml` only triggers on `v*`, and `check_version_consistency.py` rejects it |
| Removing the `## [Unreleased]` heading | The runbook and next release expect it to stay, empty |
| Leaving the new section empty | `preflight` fails: an empty changelog section is a release with no notes |
| Tagging before pushing `main` | `preflight` requires the tagged commit to be an ancestor of `main` |
| Bumping to `1.0.0` for a breaking change | Project is `0.x`; breaking changes are minor bumps until the user decides on `1.0` |
| Waiting on the `publish` job | It is gated on a human approval in the `pypi` environment; tell the user to approve |
