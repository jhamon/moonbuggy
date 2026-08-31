# Releasing

This is the runbook for cutting a moonbuggy release. It assumes you are the
maintainer with push access to `main` and admin access to the repository and
PyPI project settings.

## First-time setup

These are one-time, owner-only steps. None of them can be done from a
workflow file or a pull request, and the release will not work until all
five are in place.

1. **PyPI trusted publisher.** On pypi.org, sign in and go to your account's
   Publishing page, then add a *pending* publisher (the project does not
   exist on PyPI yet, so there is no existing project to attach it to):
   - PyPI project name: `moonbuggy`
   - Owner: `jhamon`
   - Repository: `moonbuggy`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

   No API token is created. `publish` authenticates over OIDC using this
   registration instead.

2. **The `pypi` GitHub environment.** Repository Settings → Environments →
   New environment → name it `pypi` → add a required reviewer (yourself, at
   minimum). This is what turns `publish` into an approval gate: the job
   pauses and waits for a human to click Approve before it can spend a
   version number.

3. **GitHub Pages source.** Repository Settings → Pages → Build and
   deployment → Source → `GitHub Actions`. Without this, the `docs.yml`
   workflow's `deploy` job has nothing to deploy to.

4. **Branch protection on `main`.** Repository Settings → Branches → add a
   protection rule for `main` requiring these four status checks to pass
   before merging:
   - `check (py3.12)`
   - `check (py3.13)`
   - `check (py3.14)`
   - `fresh install and docs examples`

   These are the four jobs `ci.yml` produces (three matrix legs of `check`,
   plus `install`). If the repository is ever rebuilt from scratch, this is
   the list to re-create.

5. **Scheduled-failure labels.** Create two labels in the repository's issue
   tracker: `nightly-failure` and `weekly-failure`. `scripts/report_ci_failure.sh`,
   called from `nightly.yml` and `weekly.yml`, opens an issue with one of
   these labels when a scheduled run fails; the label lookup fails if the
   label doesn't exist yet.

## Before you tag

1. Bump `__version__` in `src/moonbuggy/__init__.py` to the new version.
2. Move the entries under `CHANGELOG.md`'s `## [Unreleased]` heading into a
   new dated section, `## [x.y.z] - YYYY-MM-DD`, directly below it. Leave the
   `## [Unreleased]` heading in place, empty, above the new section.
3. Commit both changes to `main` (directly or via a reviewed pull request —
   either way, `main` must contain the bump before you tag).

## Cutting the release

```bash
git tag v0.1.0
git push origin v0.1.0
```

Then watch the Actions run for that tag. When the `publish` job reaches the
`pypi` environment, GitHub will wait for you to approve it — approve it once
you're satisfied the run so far looks right.

## What each job checks

| Job | Criterion |
|---|---|
| `preflight` | the tag, the packaged `__version__`, and the `CHANGELOG.md` section all agree; the tagged commit is on `main`; the version isn't already on PyPI |
| `gate` | the full local `check-all` suite plus docs build, docs doctest, docs linkcheck, the mutmut differential, and the open-source hunt all pass |
| `build` / `build-again` | the sdist and wheel build cleanly, twice, from the tagged commit |
| `reproducible` | the two builds are byte-identical (`SOURCE_DATE_EPOCH` pinned) |
| `smoke` | the built wheel reports the tag's version and runs the smoke scenarios end to end on a project it has never seen |
| `publish` | uploads to PyPI over OIDC, behind the `pypi` environment's required reviewer |
| `release` | creates the GitHub Release, attaching the sdist and wheel, with notes taken verbatim from the `CHANGELOG.md` section |
| `verify` | the version just published installs from the real PyPI index and passes the same smoke test, run against the installed package rather than the repository |

## When a job fails

- **`preflight` or `gate` fails:** nothing has been published or built for
  real yet. Delete the tag, fix the problem on `main`, and re-tag:
  ```bash
  git push --delete origin v0.1.0
  git tag -d v0.1.0
  # fix, commit, push to main
  git tag v0.1.0
  git push origin v0.1.0
  ```
- **`build`, `build-again`, `reproducible`, or `smoke` fails:** same as
  above — nothing has been published. Delete the tag, fix, re-tag.
- **`publish` fails or is rejected:** nothing was uploaded (PyPI never saw a
  file, or the OIDC exchange failed before the upload). Delete the tag, fix,
  re-tag.
- **`publish` succeeds:** the version is spent. PyPI will not let you upload
  `0.1.0` again under any circumstances, even if `release` or `verify` fails
  afterward. See the next section.

## A bad release is yanked, never replaced

If a published version turns out to be broken, do not try to re-upload a fix
under the same version number — PyPI refuses this outright. Instead:

1. [Yank](https://pypi.org/help/#yanked) the bad version on PyPI. A yanked
   version stays visible and installable for anyone who already pinned it
   (`moonbuggy==0.1.0` still resolves), but `pip install moonbuggy` will not
   select it for a new, unpinned install.
2. Fix the problem and ship it as the next patch version (`0.1.1`), following
   this same runbook from the top.

This is why the wheel smoke test (`smoke`) runs *before* `publish`, not
after: by the time a version is spent, the option to withdraw it stops
existing — the only path forward is a new version.

## Branch protection

See "First-time setup" above for the four required checks. This section
exists so that if the repository's settings are ever lost or need to be
re-created, the list of what to require is written down somewhere other than
the GitHub UI.

## What `verify` does and does not prove

`verify` installs the just-published version from the real PyPI index into a
clean virtual environment, confirms `moonbuggy --version` reports the right
version, and then runs the smoke test (a real project, a bare
`moonbuggy` invocation, checked output) against that installed package.

What it proves: the artifact a stranger gets from `pip install moonbuggy`
today is installable and runs end to end.

What it does not prove: anything about correctness beyond what the smoke test already
check, and it cannot undo a bad release if it fails — by the time `verify`
runs, `publish` has already succeeded and the version is already spent. The
end-to-end guarantee that matters for the decision to publish comes from
`smoke`, which runs against the built wheel *before* `publish`; `verify` is a
check that the trip through PyPI didn't corrupt anything, not a second
opportunity to catch what `smoke` missed.

## Open the next Unreleased section

After the release is out, `main` should have an empty `## [Unreleased]`
heading above the section you just cut, ready for the next round of changes.
Confirm it's there (it should already be, since it was left in place in
"Before you tag" above); if it's missing, add it back and commit.
