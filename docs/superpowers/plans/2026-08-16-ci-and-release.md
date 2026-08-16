# CI and Release (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take moonbuggy from "every gate runs because a person remembered" to automated tiered CI and a tag-driven PyPI release, with a lint and type baseline underneath both.

**Architecture:** Three ordered milestones. M5 (Tasks 1–8) adds ruff and mypy `--strict` and fixes every pre-existing violation, so CI has something green to gate on. M6 (Tasks 9–14) adds four GitHub Actions workflows tiering the existing `Makefile` gates by cost. M7 (Tasks 15–21) adds a tag-triggered release workflow that preflights, gates, builds reproducibly, smoke-tests the built wheel, and publishes via PyPI trusted publishing.

**Tech Stack:** Python 3.12+, hatchling, pytest, ruff, mypy, Sphinx + MyST + furo, GitHub Actions, PyPI trusted publishing (OIDC).

**Spec:** [../specs/2026-08-16-ci-and-release-design.md](../specs/2026-08-16-ci-and-release-design.md). Every acceptance criterion referenced as M5.x/M6.x/M7.x is defined there.

## Global Constraints

Every task's requirements implicitly include all of these.

- **Python floor is 3.12.** `requires-python = ">=3.12"`. The CI matrix is `ubuntu-latest × {3.12, 3.13, 3.14}`. No Windows, no macOS — the engine forks worker processes, and platform support is engine work, not CI work.
- **`PYTHON ?= .venv/bin/python`** is how every `Makefile` target invokes Python. New targets follow it. Console-script tools that are not `python -m`-runnable are invoked as `$(dir $(PYTHON))toolname`, the way the existing `pydoclint` line does.
- **`tests/fixtures` is input data, never linted or formatted.** It deliberately contains a file with a syntax error (M1.4.1) and a non-UTF8 file (M1.4.7). Any tool pointed at it fails on input it is supposed to fail on. `pyproject.toml` already excludes it from pytest collection; ruff and mypy must exclude it too.
- **Line length is 88** (ruff default). Existing prose comments wrap nearer 79; `ruff format` does not rewrap comments or docstrings, so they stay as they are.
- **Zero `# noqa` comments in the repository (M5.1.2).** A violation is fixed, or its rule is turned off in `pyproject.toml` — repo-wide or via `per-file-ignores` — with a comment giving the reason (M5.1.3). Two `# noqa` comments exist today, both in `docs/conf.py`; Task 1 removes them.
- **Every third-party GitHub Action is pinned to a full commit SHA (M6.5.1)**, with the human-readable version in a trailing comment. Never a floating tag. Resolve a SHA with:
  ```bash
  gh api repos/actions/checkout/git/ref/tags/v5 --jq .object.sha
  ```
- **Every workflow job declares an explicit minimal `permissions:` block (M6.5.2)** and sets `timeout-minutes` (M6.1.2). `actions/checkout` sets `persist-credentials: false` in every job that does not push (M6.5.3).
- **Commit style:** the repo uses a milestone-prefixed subject and a body explaining the decision, not the diff. Match it — e.g. `M5.1: ruff, and the violations it found`.
- **Never mark a criterion met without running its command and reading the output.**

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `.github/workflows/ci.yml` | PR tier (M6.1): the blocking matrix build |
| `.github/workflows/nightly.yml` | Nightly tier (M6.2): the correctness net |
| `.github/workflows/weekly.yml` | Weekly tier (M6.3): differential + oss-hunt |
| `.github/workflows/docs.yml` | Docs to GitHub Pages on merge to `main` (M6.4) |
| `.github/workflows/release.yml` | Tag-driven release (M7.1–M7.7) |
| `.github/actions/setup/action.yml` | Composite action: checkout-adjacent Python setup + venv + deps, used by every workflow so the setup exists once |
| `scripts/report_ci_failure.sh` | Opens or comments on the failure issue (M6.3.2) |
| `scripts/check_version_consistency.py` | Tag ↔ package version ↔ CHANGELOG preflight (M7.1.1, M7.1.3) |
| `CHANGELOG.md` | Keep a Changelog, hand-written (M7.6.1) |
| `docs/releasing.md` | Release runbook (M7.8) |

**Modified:**

| Path | Change |
|---|---|
| `pyproject.toml` | `[tool.ruff]`, `[tool.mypy]`, `lint` extra, dynamic version, revised `[tool.pydoclint]` comment |
| `Makefile` | `lint`, `format-check`, `typecheck` targets; joined to `check-all` |
| `src/moonbuggy/*.py` | Lint fixes, formatting, type annotations (Tasks 1–7) |
| `scripts/check_fresh_install.sh` | `--wheel` flag (M7.4.1) |
| `docs/conf.py` | Remove both `# noqa` comments |
| `docs/next-milestones.md` | Annotate M3.1.5 as superseded (M6.4.3) |
| `docs/index.md`, `README.md` | Link to the published docs site (M6.4.2) |

---

# Milestone M5 — Static quality baseline

## Task 1: Ruff lint

**Files:**
- Modify: `pyproject.toml` (add `[tool.ruff]`, add `lint` extra)
- Modify: `Makefile` (add `lint` target)
- Modify: `docs/conf.py:19,24` (remove two `# noqa` comments)
- Modify: whatever ruff flags across `src/`, `tests/`, `scripts/`, `docs/conf.py`

**Interfaces:**
- Produces: `make lint` — exits 0 on a clean checkout. Tasks 2 and 8 depend on the target existing.

- [ ] **Step 1: Add the `lint` extra to `pyproject.toml`**

In `[project.optional-dependencies]`, after the existing `docs` block:

```toml
# Milestone M5: the static quality gate. Pinned here so a clean venv can run
# `make lint`, `make format-check`, and `make typecheck` from a fresh checkout.
# Not required to use moonbuggy.
lint = [
  "ruff>=0.6",
  "mypy>=1.11",
]
```

- [ ] **Step 2: Install it**

```bash
.venv/bin/pip install -e '.[lint]'
```

- [ ] **Step 3: Add the ruff configuration**

Append to `pyproject.toml`, after the `[tool.pydoclint]` block:

```toml
[tool.ruff]
# Milestone M5.2. 88 is ruff's default. The prose comments in this codebase
# wrap nearer 79, and the formatter does not rewrap comments or docstrings, so
# they stay as written -- the extra width is available to code.
line-length = 88
target-version = "py312"
# tests/fixtures is *input data* for moonbuggy's own suite, not code this
# project maintains. It deliberately contains a file with a syntax error
# (M1.4.1) and a file in a non-UTF8 encoding (M1.4.7). A linter pointed at it
# fails on input it is designed to fail on.
extend-exclude = ["tests/fixtures", "docs/_build", ".venv"]

[tool.ruff.lint]
# M5.1: chosen for this code rather than inherited from ALL and then riddled
# with suppressions. E/F/W are the pyflakes and pycodestyle core, I sorts
# imports, UP catches syntax kept alive past its Python version, B is bugbear,
# SIM is the readability set.
select = ["E", "F", "W", "I", "UP", "B", "SIM"]

[tool.ruff.lint.per-file-ignores]
# conf.py must adjust sys.path before importing the package, which is E402.
# That is the file's contract with Sphinx, not a style choice, so it is
# exempted here rather than with a `# noqa` -- M5.1.2 wants zero inline
# suppressions. (The file also carries a `# noqa: A001` for Sphinx's required
# module-level `copyright` name. A-rules are not in `select`, so that
# suppression is inert and Task 1 Step 8 simply deletes it.)
"docs/conf.py" = ["E402"]
```

- [ ] **Step 4: Add the `make lint` target**

In `Makefile`, add `lint` to the `.PHONY` list, then add after the `docstring-coverage` target:

```makefile
## Milestone M5.1: the lint gate.
## Config and the reason for every disabled rule live in pyproject.toml.
lint:
	$(dir $(PYTHON))ruff check .
```

- [ ] **Step 5: Run it and record the starting damage**

```bash
make lint
```

Expected: FAIL, with a violation count. Note the count — Step 8 checks it reaches zero. This is the pre-existing debt M5 exists to absorb.

- [ ] **Step 6: Apply the mechanical fixes**

```bash
.venv/bin/ruff check . --fix
```

This resolves import sorting (`I`) and most `UP` and `SIM` findings without judgement. Review the diff before continuing: `git diff --stat`.

- [ ] **Step 7: Fix the remainder by hand**

For each surviving violation, choose one of exactly two options — never a third:

1. **Fix the code.** Preferred.
2. **Disable the rule in `pyproject.toml`** with a one-line comment giving the reason (M5.1.3), via `select` removal for a repo-wide problem or `per-file-ignores` for a localized one.

Do not add `# noqa`. Watch specifically for `B008` (function call in a default argument) and `SIM105`/`SIM117` in `forkserver.py` and `runner.py` — where the process-boundary code may have a real reason to be written the way it is. If so, that reason is a `per-file-ignores` entry with a comment, not a suppression.

- [ ] **Step 8: Remove the two `# noqa` comments in `docs/conf.py`**

They are covered by the `per-file-ignores` entry from Step 3.

```python
from moonbuggy import __version__

project = "moonbuggy"
author = "Jennifer Hamon"
copyright = "2026, Jennifer Hamon"
```

- [ ] **Step 9: Verify lint is clean and no suppressions remain**

```bash
make lint
```
Expected: PASS, exit 0.

```bash
grep -rn "# noqa" --include="*.py" src tests scripts docs
```
Expected: no output (M5.1.2). `tests/fixtures` is excluded from ruff but is still searched here — if a fixture contains a `# noqa`, it is input data; leave it and note it in the commit message.

- [ ] **Step 10: Verify nothing broke**

```bash
make test
```
Expected: PASS. Ruff's `--fix` can rewrite code, and `UP`/`SIM` rewrites are behaviour-preserving only if the tool is right.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml Makefile src tests scripts docs
git commit -m "M5.1: ruff, and the violations it found

A rule set chosen for this code rather than inherited from ALL and then
riddled with suppressions. Zero '# noqa' in the repository: a violation is
fixed, or its rule is off in pyproject.toml with the reason next to it.
tests/fixtures is excluded -- it holds a deliberate syntax error and a
non-UTF8 file, which are input data, not debt."
```

---

## Task 2: Ruff format

**Files:**
- Modify: `Makefile` (add `format-check` target)
- Modify: every formatted `.py` file outside `tests/fixtures`

**Interfaces:**
- Consumes: `[tool.ruff]` config from Task 1.
- Produces: `make format-check` — exits 0 on a clean checkout.

- [ ] **Step 1: Add the target**

Add `format-check` to `.PHONY`, then after `lint`:

```makefile
## Milestone M5.2: the formatting gate. Checks only; `ruff format` reformats.
format-check:
	$(dir $(PYTHON))ruff format --check .
```

- [ ] **Step 2: Confirm it fails before the reformat**

```bash
make format-check
```
Expected: FAIL, listing files that would be reformatted.

- [ ] **Step 3: Capture the pre-reformat test state (M5.2.3)**

```bash
make check-all 2>&1 | tail -30 > /tmp/check-all-before.txt
cat /tmp/check-all-before.txt
```
Expected: PASS. If `check-all` is already failing, stop — M5.2.3 is unverifiable until it passes, and that is a separate problem to fix first.

- [ ] **Step 4: Reformat**

```bash
.venv/bin/ruff format .
```

- [ ] **Step 5: Verify**

```bash
make format-check
```
Expected: PASS.

- [ ] **Step 6: Verify the reformat changed no behaviour (M5.2.3)**

```bash
make check-all 2>&1 | tail -30 > /tmp/check-all-after.txt
diff /tmp/check-all-before.txt /tmp/check-all-after.txt
```
Expected: identical pass/fail outcomes. Timings and durations will differ; test counts and statuses must not.

- [ ] **Step 7: Commit — reformat only, nothing else (M5.2.2)**

```bash
git add -A
git commit -m "M5.2: ruff format, mechanically and by itself

No behavioural change in this commit, so 'git log -p' over it can be skimmed
rather than read. check-all passes identically before and after."
```

---

## Task 3: Mypy configuration and the first leaf modules

**Files:**
- Modify: `pyproject.toml` (add `[tool.mypy]`)
- Modify: `Makefile` (add `typecheck` target)
- Modify: `src/moonbuggy/mutant.py`, `srcio.py`, `cache.py`, `discover.py`

**Interfaces:**
- Produces: `make typecheck` — exits 0. The `[[tool.mypy.overrides]]` list of not-yet-annotated modules is the mechanism Tasks 4–6 consume: each task deletes its modules from that list.

**Why this order:** annotation proceeds leaf-first through the internal import graph, so a module is annotated only after everything it imports already is. The graph is: `mutant`, `srcio`, `cache`, `discover`, `profiling`, `report` (leaves) → `operators/*`, `inmemory`, `codeswap`, `generate`, `naive` → `plugin`, `baseline`, `forkserver`, `coverage_pass` → `runner` → `cli`.

- [ ] **Step 1: Add the mypy configuration**

Append to `pyproject.toml`:

```toml
[tool.mypy]
# Milestone M5.3. Strict over the package only -- tests/ and scripts/ are
# harnesses and benchmark drivers, and annotating them buys ceremony.
python_version = "3.12"
files = ["src/moonbuggy"]
strict = true
# An un-annotated module is not an excuse for a red gate while M5.3 is in
# progress. Each of Tasks 4-6 deletes its modules from the list below, so
# `make typecheck` is green at every commit and the list is the visible
# remaining work.
[[tool.mypy.overrides]]
module = [
  "moonbuggy.operators.*",
  "moonbuggy.inmemory",
  "moonbuggy.codeswap",
  "moonbuggy.generate",
  "moonbuggy.naive",
  "moonbuggy.profiling",
  "moonbuggy.report",
  "moonbuggy.plugin",
  "moonbuggy.baseline",
  "moonbuggy.forkserver",
  "moonbuggy.coverage_pass",
  "moonbuggy.runner",
  "moonbuggy.cli",
]
ignore_errors = true
```

- [ ] **Step 2: Add the target**

Add `typecheck` to `.PHONY`, then:

```makefile
## Milestone M5.3: the type gate. Strict, over src/moonbuggy only.
## The override list in pyproject.toml is the remaining un-annotated work.
typecheck:
	$(PYTHON) -m mypy
```

- [ ] **Step 3: Run it**

```bash
make typecheck
```
Expected: errors in `mutant.py`, `srcio.py`, `cache.py`, `discover.py` only — the four modules not on the override list. If a module on the list still errors, the module path in the override is misspelled.

- [ ] **Step 4: Annotate the four leaf modules**

Work one module at a time, smallest first: `mutant.py` (34 lines), `discover.py` (122), `cache.py` (117), `srcio.py` (217). After each, run `make typecheck` and confirm that module's errors are gone.

Rules for this and every annotation task:
- Annotate the real shape. Where a value is genuinely a heterogeneous tuple crossing a process boundary, that is a `tuple[str, int, bool]` or a `TypedDict`, not `Any`.
- Every `Any` in a public signature needs a comment saying what it stands for (M5.3.3).
- No bare `# type: ignore` — always `# type: ignore[error-code]  # reason` (M5.3.2).
- Do not change runtime behaviour. If an annotation reveals a bug, stop, write a failing test for it first, then fix it in its own commit.

- [ ] **Step 5: Verify**

```bash
make typecheck && make test
```
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml Makefile src/moonbuggy
git commit -m "M5.3: mypy strict, and the four leaf modules

The override list in pyproject.toml is the remaining work made visible: the
gate is green at every commit, and shrinking the list is the milestone."
```

---

## Task 4: Annotate the operator and generation layer

**Files:**
- Modify: `src/moonbuggy/operators/__init__.py`, `arithmetic.py`, `boolean.py`, `boundary.py`, `comparison.py`, `constant.py`
- Modify: `src/moonbuggy/inmemory.py`, `codeswap.py`, `generate.py`, `naive.py`, `profiling.py`, `report.py`
- Modify: `pyproject.toml` (shrink the override list)

**Interfaces:**
- Consumes: annotated `Mutant` from `mutant.py` and the `srcio` functions (`read_source`, `detect_encoding`, `encode_source`, `replace_line`, `strip_coding_cookie`) from Task 3.
- Produces: annotated `register` and `replace_operator` in `operators/__init__.py`, `all_operators()`, `generate_mutants()`, `mutated_source()`, `apply_in_place()` / `SwapFailed`.

- [ ] **Step 1: Remove these modules from the override list**

Delete from the `module = [...]` list in `pyproject.toml`: `"moonbuggy.operators.*"`, `"moonbuggy.inmemory"`, `"moonbuggy.codeswap"`, `"moonbuggy.generate"`, `"moonbuggy.naive"`, `"moonbuggy.profiling"`, `"moonbuggy.report"`.

- [ ] **Step 2: See the work**

```bash
make typecheck
```
Expected: FAIL, listing errors in exactly those modules.

- [ ] **Step 3: Annotate `operators/__init__.py` first**

It defines the registration decorator every operator module uses, so its signature determines theirs. The decorator is the one place here where getting the type right matters structurally — a `Callable` alias declared once and reused beats repeating an inline signature in six files:

```python
from collections.abc import Callable

# The shape every operator implements: given an AST node, yield the mutated
# variants of it. Declared once here because six modules implement it.
OperatorFn = Callable[[ast.AST], Iterator[ast.AST]]
```

Match the alias to what the existing code actually does — read `arithmetic.py` before writing it, and adjust if the real signature differs.

- [ ] **Step 4: Annotate the five operator modules**

They are near-identical in shape; annotating one tells you the other four.

- [ ] **Step 5: Annotate `inmemory.py`, `codeswap.py`, `generate.py`, `naive.py`, `profiling.py`, `report.py`**

One at a time, running `make typecheck` after each.

- [ ] **Step 6: Verify**

```bash
make typecheck && make test && make check-properties
```
Expected: all PASS. `check-properties` runs the Hypothesis invariants over generation and splicing — the code this task just touched — and takes about two minutes.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/moonbuggy
git commit -m "M5.3: annotate the operator and generation layer

OperatorFn declared once in operators/__init__.py rather than an inline
signature repeated in six files."
```

---

## Task 5: Annotate the process layer

**Files:**
- Modify: `src/moonbuggy/plugin.py`, `baseline.py`, `coverage_pass.py`, `forkserver.py`
- Modify: `pyproject.toml` (shrink the override list)

**Interfaces:**
- Consumes: everything annotated in Tasks 3–4.
- Produces: annotated `run_baseline_pass()`, `read_coverage_data()`, `check()`, `BaselineError`, `CoveragePassError`, `prewarm()`, `install()`, `MUTANT_ENV_VAR`, `OutcomeRecorder`.

**This is the hard one.** `forkserver.py` is 659 lines and is precisely the code the old pydoclint comment was defending: values crossing a fork boundary as plain tuples and dicts. Expect to spend most of this task deciding what those shapes actually are.

- [ ] **Step 1: Remove `plugin`, `baseline`, `coverage_pass`, `forkserver` from the override list**

- [ ] **Step 2: See the work**

```bash
make typecheck
```

- [ ] **Step 3: Name the cross-boundary shapes before annotating anything**

Read `forkserver.py` end to end first. For each value sent across the fork or returned from a worker, decide between:

- **`TypedDict`** — for a dict with known keys, which is most of them. Costs nothing at runtime and documents the keys.
- **`NamedTuple`** — for a fixed-length tuple whose fields have names in the reader's head but not in the code.
- **A plain `tuple[...]`** — for a genuinely positional pair.

Put the declarations at the top of `forkserver.py`, each with a comment saying which direction it crosses (parent→child or child→parent). This is the substantive deliverable of the task; the annotations that follow are mechanical once these exist.

- [ ] **Step 4: Annotate `plugin.py`, `baseline.py`, `coverage_pass.py`, then `forkserver.py`**

- [ ] **Step 5: Verify — the full correctness net, not just the fast suite**

```bash
make typecheck && make test && make check-oracle && make check-robustness
```
Expected: all PASS. `check-oracle` and `check-robustness` spawn a process per mutant and exercise the fork path this task touched; `make test` alone would not catch a break here.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/moonbuggy
git commit -m "M5.3: annotate the process layer, and name what crosses the fork

The values that cross the fork boundary are TypedDicts and NamedTuples now,
declared at the top of forkserver.py with the direction they travel. That
shape was previously carried in the reader's head."
```

---

## Task 6: Annotate the entry points and empty the override list

**Files:**
- Modify: `src/moonbuggy/runner.py`, `cli.py`, `__init__.py`
- Modify: `pyproject.toml` (delete the override block entirely)

**Interfaces:**
- Consumes: everything from Tasks 3–5.
- Produces: `make typecheck` clean under `--strict` over all of `src/moonbuggy`, with no overrides (M5.3.1).

- [ ] **Step 1: Delete the entire `[[tool.mypy.overrides]]` block**

Not just the remaining two entries — the block itself, so `strict = true` applies with nothing carved out.

- [ ] **Step 2: See the work**

```bash
make typecheck
```

- [ ] **Step 3: Annotate `runner.py`, then `cli.py`, then `__init__.py`**

`runner.py` (669 lines) consumes the `forkserver` shapes from Task 5 — reuse those declarations by importing them rather than redeclaring equivalents.

- [ ] **Step 4: Verify the gate is genuinely strict**

```bash
make typecheck
```
Expected: PASS.

```bash
grep -n "ignore_errors\|overrides" pyproject.toml
```
Expected: no output. An override left behind means the gate is narrower than it claims (M5.3.1).

- [ ] **Step 5: Verify no bare ignores and no undocumented `Any` (M5.3.2, M5.3.3)**

```bash
grep -rn "type: ignore" src/moonbuggy
```
Expected: every hit has both an error code in brackets and a trailing reason.

```bash
grep -rn ": Any\|-> Any" src/moonbuggy
```
Expected: every hit in a public signature has a comment saying what it stands for.

- [ ] **Step 6: Verify everything still works**

```bash
make check-all
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/moonbuggy
git commit -m "M5.3: annotate runner and cli, and drop the override block

strict = true now applies with nothing carved out. The override list served
its purpose -- it kept the gate green while the annotation landed -- and
leaving it behind would make the gate narrower than it claims."
```

---

## Task 7: Revise the typing convention on the record

**Files:**
- Modify: `pyproject.toml` (`[tool.pydoclint]` comment and settings)
- Modify: `docs/architecture.md` if it repeats the old convention (check first)

**Why:** `pyproject.toml` currently states, as a documented decision, "Type hints are not this codebase's convention... annotating them would document a shape the code deliberately does not enforce." That is now false. Leaving it there leaves the repo arguing with itself.

- [ ] **Step 1: Find every place the old convention is stated**

```bash
grep -rn "type hint\|type-hint" pyproject.toml docs README.md
```

- [ ] **Step 2: Rewrite the `[tool.pydoclint]` comment**

Replace the paragraph beginning "Type hints are not this codebase's convention" with:

```toml
# M5.3 reversed the earlier decision recorded here, which was that type hints
# were not this codebase's convention because core objects cross process
# boundaries as plain tuples and dicts. They still do -- but they cross as
# TypedDicts and NamedTuples now, declared at the top of forkserver.py, so the
# shape is enforced rather than implied. The signatures carry the types; the
# docstrings do not repeat them, which is what the two settings below mean.
arg-type-hints-in-signature = false
arg-type-hints-in-docstring = false
```

`arg-type-hints-in-signature = false` stays deliberately: it tells pydoclint not to *require* hints in docstrings' argument lists, which is still what we want now that the signature is the authority. Do not flip it without running `make docs` — flipping it demands a type restated in every docstring.

- [ ] **Step 3: Verify the docstring gates still pass**

```bash
make docs
```
Expected: PASS, including `interrogate` at 100% and `pydoclint`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml docs
git commit -m "M5.3: record that the typing convention changed

The old comment said type hints were not this codebase's convention. After
M5.3 that is simply false, and a config file arguing with the code it
configures is worse than either position."
```

---

## Task 8: Wire the new gates into `check-all`

**Files:**
- Modify: `Makefile` (`check-all` target)

- [ ] **Step 1: Add the three targets to `check-all` (M5.4.2)**

```makefile
check-all: lint format-check typecheck test check-oracle check-spike check-properties check-robustness check-mutmut check-fresh-install
```

The three static gates go first: they take seconds, and there is no reason to spend twenty minutes on mutant runs before learning the code does not lint.

- [ ] **Step 2: Verify the whole milestone from a clean checkout (M5.4.3)**

```bash
git status --porcelain
```
Expected: clean.

```bash
make check-all
```
Expected: PASS, end to end.

- [ ] **Step 3: Verify the `lint` extra is sufficient on its own**

```bash
python3.12 -m venv /tmp/m5check && /tmp/m5check/bin/pip install -q -e '.[lint]' && PYTHON=/tmp/m5check/bin/python make lint format-check typecheck && rm -rf /tmp/m5check
```
Expected: PASS. This is what M5.4.3 claims — a fresh venv can run the new targets — and it is the only way to find a tool that only works because it happened to be in the dev venv.

- [ ] **Step 4: Commit**

```bash
git add Makefile
git commit -m "M5.4: lint, format-check and typecheck join check-all

Static gates first: no reason to spend twenty minutes on mutant runs before
learning the code does not lint."
```

---

# Milestone M6 — Continuous integration

## Task 9: The shared setup action and the PR workflow

**Files:**
- Create: `.github/actions/setup/action.yml`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: a composite action at `./.github/actions/setup` taking one input, `python-version`, which checks out nothing (the caller checks out), sets up Python, and installs the project with all extras. Tasks 10, 11, and 15 consume it.

- [ ] **Step 1: Resolve the action SHAs**

```bash
gh api repos/actions/checkout/git/ref/tags/v5 --jq .object.sha
gh api repos/actions/setup-python/git/ref/tags/v6 --jq .object.sha
```

Use the returned SHAs everywhere below, with the tag in a trailing comment. Do not copy a SHA from elsewhere in this plan — resolve it (M6.5.1).

- [ ] **Step 2: Write the composite setup action**

`.github/actions/setup/action.yml`:

```yaml
name: Set up moonbuggy
description: Python, pip cache, and the project installed with every extra.

inputs:
  python-version:
    description: Python version to install
    required: true

runs:
  using: composite
  steps:
    - uses: actions/setup-python@<SHA>  # v6
      with:
        python-version: ${{ inputs.python-version }}
        cache: pip
        cache-dependency-path: pyproject.toml
    - shell: bash
      run: |
        python -m pip install --upgrade pip
        python -m pip install -e '.[dev,docs,lint,bench]'
```

Note `PYTHON` is not set here: the `Makefile` default is `.venv/bin/python`, which does not exist on a runner. Every workflow step below passes `PYTHON=python` explicitly.

- [ ] **Step 3: Write the PR workflow**

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

# M6.1.3: a force-push supersedes its own in-flight run.
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  check:
    name: check (py${{ matrix.python-version }})
    runs-on: ubuntu-latest
    timeout-minutes: 20
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.12", "3.13", "3.14"]
    steps:
      - uses: actions/checkout@<SHA>  # v5
        with:
          persist-credentials: false
      - uses: ./.github/actions/setup
        with:
          python-version: ${{ matrix.python-version }}
      - run: make lint PYTHON=python
      - run: make format-check PYTHON=python
      - run: make typecheck PYTHON=python
      - run: make test PYTHON=python
      - run: make docs PYTHON=python

  install:
    name: fresh install and docs examples
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@<SHA>  # v5
        with:
          persist-credentials: false
      - uses: ./.github/actions/setup
        with:
          python-version: "3.12"
      # M6.1: both spawn real installs and neither is version-sensitive, so
      # they run once on the floor version rather than on every matrix leg.
      - run: make docs-test PYTHON=python
      - run: make check-fresh-install PYTHON=python
        env:
          PYENV_PY: python
```

`fail-fast: false` is deliberate: when 3.14 breaks and 3.12 does not, that difference is the finding, and cancelling the other legs hides it.

- [ ] **Step 4: Check `check_fresh_install.sh` accepts the runner's Python**

The script defaults to `${PYENV_PY:-$HOME/.pyenv/versions/3.12.13/bin/python3}` — a path that does not exist on a runner. The `env: PYENV_PY: python` above covers it. Confirm by reading `scripts/check_fresh_install.sh:20`.

- [ ] **Step 5: Verify locally before pushing**

```bash
make lint format-check typecheck test docs PYTHON=.venv/bin/python
```
Expected: PASS. Everything the `check` job runs, run locally first — a red first CI run should mean the workflow is wrong, not that the code is.

- [ ] **Step 6: Commit and push to a branch, then open a PR**

```bash
git add .github
git commit -m "M6.1: the PR tier

Three Python versions, fail-fast off so a version-specific break is visible
rather than cancelled. The two install-spawning gates run once on the floor
version -- neither is version-sensitive and both cost minutes."
git push -u origin m6-ci
gh pr create --fill
```

- [ ] **Step 7: Verify the workflow is actually green (M6.1.1)**

```bash
gh run watch
```
Expected: all four jobs pass. Record the wall clock — M6.1.1 wants p50 under 10 minutes, checked over at least ten runs, so this is the first data point rather than the verdict.

---

## Task 10: Scheduled failure reporting

**Files:**
- Create: `scripts/report_ci_failure.sh`

**Interfaces:**
- Produces: `scripts/report_ci_failure.sh <label> <title>` — opens an issue labelled `<label>` with the run URL, or comments on the existing open one. Tasks 11 and 12 call it.

**Why its own task:** M6.3.2 is the criterion that makes the nightly and weekly tiers gates rather than decoration, both workflows need it, and it is the one piece here with logic worth testing before two callers depend on it.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
#
# Milestone M6.3.2: a scheduled job whose only failure signal is an email
# nobody reads is not a gate. Opens one issue per failing tier and comments on
# it thereafter, so a tier that has been broken for a week is one issue with a
# week of comments rather than seven issues.
#
# Usage: report_ci_failure.sh <label> <title>
# Requires: GH_TOKEN in the environment, and `issues: write` on the job.
set -euo pipefail

LABEL="$1"
TITLE="$2"
RUN_URL="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
BODY="Scheduled run failed: ${RUN_URL}"

EXISTING="$(gh issue list --label "$LABEL" --state open --limit 1 --json number --jq '.[0].number // empty')"

if [ -n "$EXISTING" ]; then
  echo "==> commenting on existing issue #${EXISTING}"
  gh issue comment "$EXISTING" --body "$BODY"
else
  echo "==> opening a new issue"
  gh issue create --label "$LABEL" --title "$TITLE" --body "$BODY"
fi
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/report_ci_failure.sh
```

- [ ] **Step 3: Create the two labels the script expects**

```bash
gh label create nightly-failure --description "A nightly CI tier run failed" --color B60205
gh label create weekly-failure --description "A weekly CI tier run failed" --color B60205
```

- [ ] **Step 4: Test the branch that matters — reuse, not creation**

Verify the existing-issue path by hand, because a bug here produces one issue per day forever:

```bash
gh issue create --label nightly-failure --title "test: reuse check" --body "temporary"
GITHUB_SERVER_URL=https://github.com \
GITHUB_REPOSITORY=jhamon/moonbuggy \
GITHUB_RUN_ID=1 \
./scripts/report_ci_failure.sh nightly-failure "test: should not be created"
```
Expected: a comment on the existing issue, and no second issue.

```bash
gh issue list --label nightly-failure --state open
```
Expected: exactly one issue. Close it:

```bash
gh issue close <number>
```

- [ ] **Step 5: Commit**

```bash
git add scripts/report_ci_failure.sh
git commit -m "M6.3.2: one issue per failing tier, not one per run

A tier broken for a week should be one issue with a week of comments."
```

---

## Task 11: Nightly and weekly tiers

**Files:**
- Create: `.github/workflows/nightly.yml`
- Create: `.github/workflows/weekly.yml`

**Interfaces:**
- Consumes: `./.github/actions/setup` (Task 9), `scripts/report_ci_failure.sh` (Task 10).

- [ ] **Step 1: Write the nightly workflow**

```yaml
name: Nightly

on:
  schedule:
    - cron: "0 7 * * *"   # 07:00 UTC daily
  workflow_dispatch:      # M6.3.1

permissions:
  contents: read
  issues: write

jobs:
  nightly:
    runs-on: ubuntu-latest
    timeout-minutes: 90
    steps:
      - uses: actions/checkout@<SHA>  # v5
        with:
          persist-credentials: false
      - uses: ./.github/actions/setup
        with:
          python-version: "3.12"
      - run: make check-oracle PYTHON=python
      - run: make check-properties PYTHON=python
      - run: make check-robustness PYTHON=python
      - run: make check-mutmut PYTHON=python
      # docs-linkcheck is here rather than in the PR tier: it hits the network,
      # and an upstream site being down must not block a pull request.
      - run: make docs-linkcheck PYTHON=python
      - if: failure()
        run: ./scripts/report_ci_failure.sh nightly-failure "Nightly CI tier failing"
        env:
          GH_TOKEN: ${{ github.token }}
```

- [ ] **Step 2: Write the weekly workflow**

Identical in shape, with these differences: `name: Weekly`, `cron: "0 8 * * 1"` (Mondays 08:00 UTC), `timeout-minutes: 360`, the two gates `make check-differential` and `make oss-hunt`, and the label `weekly-failure` with title `Weekly CI tier failing`.

`oss-hunt` clones five repositories and builds a venv for each; `check-differential` runs both moonbuggy and mutmut across ten projects. Six hours is a ceiling that should never be reached, not an estimate.

- [ ] **Step 3: Commit and push**

```bash
git add .github/workflows
git commit -m "M6.2/M6.3: the nightly and weekly tiers

Tiered by cost. The network-bound gates are weekly because their inputs are
pinned upstream repositories that change slowly."
git push
```

- [ ] **Step 4: Verify by dispatch rather than by waiting (M6.3.1)**

```bash
gh workflow run nightly.yml && gh run watch
```
Expected: PASS. Do not wait for the schedule to find out whether the file parses.

- [ ] **Step 5: Verify the failure path end to end**

Temporarily add a failing step (`- run: exit 1`) after the last gate, push, dispatch, and confirm an issue is opened with the run URL. Then remove the step and push again.

```bash
gh issue list --label nightly-failure
```
Expected: one issue, containing the run URL. Close it and commit the removal. M6.3.2 is a claim about behaviour, and the only way to check it is to make the thing fail.

- [ ] **Step 6: Dispatch the weekly workflow too**

```bash
gh workflow run weekly.yml && gh run watch
```
Expected: PASS. This one takes a while — it is the longest-running workflow in the repository.

---

## Task 12: Docs to GitHub Pages

**Files:**
- Create: `.github/workflows/docs.yml`
- Modify: `docs/next-milestones.md` (M3.1.5 supersede note)
- Modify: `README.md`, `docs/index.md` (links)

- [ ] **Step 1: Enable Pages for the repository**

In GitHub → Settings → Pages, set Source to "GitHub Actions". The deploy job fails with a clear error until this is done.

- [ ] **Step 2: Resolve the Pages action SHAs**

```bash
gh api repos/actions/upload-pages-artifact/git/ref/tags/v3 --jq .object.sha
gh api repos/actions/deploy-pages/git/ref/tags/v4 --jq .object.sha
gh api repos/actions/configure-pages/git/ref/tags/v5 --jq .object.sha
```

- [ ] **Step 3: Write the workflow**

```yaml
name: Docs

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@<SHA>  # v5
        with:
          persist-credentials: false
      - uses: ./.github/actions/setup
        with:
          python-version: "3.12"
      - uses: actions/configure-pages@<SHA>  # v5
      # -W is on inside the Makefile target, so a broken cross-reference fails
      # the deploy rather than publishing a page with a dangling link.
      - run: make docs PYTHON=python
      - uses: actions/upload-pages-artifact@<SHA>  # v3
        with:
          path: docs/_build/html

  deploy:
    needs: build
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@<SHA>  # v4
```

`cancel-in-progress: false` here, unlike CI: cancelling a deploy mid-flight can leave Pages in a partial state.

- [ ] **Step 4: Annotate M3.1.5 as superseded (M6.4.3)**

In `docs/next-milestones.md`, find the M3.1.5 text stating nothing is published, and append:

```markdown
> **Superseded by M6.4** (see
> [the Phase 3 design](superpowers/specs/2026-08-16-ci-and-release-design.md)).
> Documentation is published to GitHub Pages on every merge to `main`. M3.1.5
> was a scope fence for Phase 2 rather than a principle, but leaving it
> unannotated would leave these documents disagreeing.
```

- [ ] **Step 5: Verify the docs build locally first**

```bash
make docs
```
Expected: PASS. A `-W` build failing on the runner after a five-minute setup is a slow way to learn about a dangling cross-reference.

- [ ] **Step 6: Push and verify the deploy**

```bash
git add .github/workflows/docs.yml docs/next-milestones.md
git commit -m "M6.4: publish the docs, and say so where M3.1.5 said otherwise"
git push
gh run watch
```
Expected: PASS, and the deploy job prints the Pages URL.

- [ ] **Step 7: Verify the published site is real (M6.4.1)**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://jhamon.github.io/moonbuggy/
```
Expected: `200`.

- [ ] **Step 8: Add the links (M6.4.2)**

In `README.md`, under the `## Install` heading, and in `docs/index.md` near the top, link to `https://jhamon.github.io/moonbuggy/`. Use the URL the deploy job actually printed, not the one predicted above.

- [ ] **Step 9: Commit**

```bash
git add README.md docs/index.md
git commit -m "M6.4.2: link the published docs from the README and the index"
git push
```

---

## Task 13: Supply-chain hygiene audit

**Files:**
- Modify: any workflow file that fails the checks below

**Why a separate task:** the constraints were applied while writing each workflow, but M6.5 is a claim about *all* of them, and the only way to check a repo-wide claim is to check it repo-wide, once, at the end.

- [ ] **Step 1: Verify every action is SHA-pinned (M6.5.1)**

```bash
grep -rn "uses:" .github | grep -v "uses: \./" | grep -vE "@[0-9a-f]{40}"
```
Expected: no output. Any hit is a floating tag.

- [ ] **Step 2: Verify every version comment is present and honest**

```bash
grep -rn "uses:" .github | grep -v "uses: \./"
```
Expected: every line has a trailing `# v<N>` comment. Spot-check two SHAs against `gh api` to confirm the comment matches the tag.

- [ ] **Step 3: Verify every job has `permissions` and `timeout-minutes` (M6.5.2, M6.1.2)**

Read each of the five workflow files and confirm both, per job. A workflow-level `permissions` block covers its jobs; a job that narrows or widens it needs its own.

- [ ] **Step 4: Verify `persist-credentials: false` (M6.5.3)**

```bash
grep -A2 -rn "actions/checkout" .github | grep -c "persist-credentials: false"
```
Expected: equal to the number of `actions/checkout` uses. No job in this repo pushes, so there should be no exceptions.

- [ ] **Step 5: Commit any fixes**

```bash
git add .github
git commit -m "M6.5: pin, restrict, and bound every workflow job"
```

---

## Task 14: Branch protection

**Files:** none — this is repository configuration plus a verification.

- [ ] **Step 1: Require the PR-tier checks on `main` (M6.6.1)**

In GitHub → Settings → Branches → add a rule for `main`: require a pull request before merging, require status checks to pass, and select `check (py3.12)`, `check (py3.13)`, `check (py3.14)`, and `install`. Check names must match the `name:` fields from Task 9 exactly — a mistyped name is a check that never runs and therefore never blocks.

- [ ] **Step 2: Verify it has teeth (M6.6.2)**

Open a pull request that deliberately fails lint:

```bash
git checkout -b test-branch-protection
printf 'import os\n' >> src/moonbuggy/cache.py   # F401: unused import
git commit -am "test: deliberately fail lint"
git push -u origin test-branch-protection
gh pr create --fill
gh pr checks --watch
```
Expected: the `check` jobs fail, and `gh pr view --json mergeable,mergeStateStatus` reports the PR as blocked.

- [ ] **Step 3: Clean up**

```bash
gh pr close test-branch-protection --delete-branch
git checkout main && git branch -D test-branch-protection
```

- [ ] **Step 4: Record the configuration**

Branch protection lives in repository settings, not in the repo, so nothing in git records it. Add a short "Branch protection" section to `docs/releasing.md` in Task 21 listing the four required checks, so a future maintainer restoring the repo knows what to re-create.

---

# Milestone M7 — Release pipeline

## Task 15: Single-source the version, and the consistency check

**Files:**
- Modify: `pyproject.toml` (dynamic version)
- Modify: `src/moonbuggy/__init__.py` (bump to `0.1.0`)
- Create: `scripts/check_version_consistency.py`
- Create: `CHANGELOG.md`

**Interfaces:**
- Produces: `python scripts/check_version_consistency.py <tag>` — exits 0 when the tag, the package version, and a `CHANGELOG.md` section all agree; exits 1 with a specific message naming which one disagrees. Task 17 calls it.

**Deviation from the spec, stated deliberately:** the design says the version is held in `pyproject.toml`. In fact it is held in *two* places today — `pyproject.toml:7` and `src/moonbuggy/__init__.py:7` — and `docs/conf.py` and `scripts/oss_hunt.py` both read the second. Rather than check three numbers against each other, this task makes `__init__.py` the single literal and has hatchling read it. The spec's intent (the repo holds the number, the tag must match, no `hatch-vcs`) is preserved; the drift class is removed. Reverse it by restoring the static `version =` line if this turns out to be unwanted.

- [ ] **Step 1: Make the version dynamic in `pyproject.toml`**

Replace `version = "0.0.1"` with:

```toml
dynamic = ["version"]
```

And add, next to the existing `[tool.hatch.build.targets.wheel]` block:

```toml
[tool.hatch.version]
# M7: one literal version in the repository. docs/conf.py and
# scripts/oss_hunt.py already read __version__, so making that the source and
# having the build read it removes a number that could drift from the build.
path = "src/moonbuggy/__init__.py"
```

- [ ] **Step 2: Set the version to the first real release**

In `src/moonbuggy/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: Verify the build picks it up**

```bash
.venv/bin/pip install -q build && .venv/bin/python -m build --wheel --outdir /tmp/vercheck . && ls /tmp/vercheck
```
Expected: `moonbuggy-0.1.0-py3-none-any.whl`. A `0.0.1` filename means hatchling is not reading the path.

- [ ] **Step 4: Write `CHANGELOG.md` (M7.6.1)**

```markdown
# Changelog

All notable changes to moonbuggy are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-16

First published release.

### Added

- Fast mutation testing driven by per-line coverage: only the tests covering a
  mutated line are rerun, mutations are applied in memory rather than on disk,
  mutants run in parallel forked workers, and results are cached across runs.
- JSON Lines results with a derived plaintext view whose every line starts with
  a fixed status keyword, so `grep SURVIVED` works without knowing the schema.
- Zero-configuration operation: source layout and test suite are discovered
  from the project root.
- Five mutation operator families: arithmetic, boolean, boundary, comparison,
  and constant.
```

- [ ] **Step 5: Write the consistency checker**

`scripts/check_version_consistency.py`:

```python
"""Milestone M7.1: refuse to release a tag that disagrees with the repository.

Checks, in order, that the tag matches the packaged version and that the
changelog has a non-empty section for it. Each failure names which of the three
disagreed, because "version mismatch" without saying which side is wrong is a
message that sends the reader to look at both.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def packaged_version():
    """Return the version literal from src/moonbuggy/__init__.py.

    Returns:
        The version string, without quotes.
    """
    text = (ROOT / "src" / "moonbuggy" / "__init__.py").read_text()
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if match is None:
        sys.exit("FAIL: no __version__ literal in src/moonbuggy/__init__.py")
    return match.group(1)


def changelog_section(version):
    """Return the changelog body for a version, or None if absent.

    Args:
        version: The version to look for, without a leading 'v'.

    Returns:
        The section body with surrounding whitespace stripped, or None.
    """
    text = (ROOT / "CHANGELOG.md").read_text()
    pattern = rf"^## \[{re.escape(version)}\].*?$(.*?)(?=^## \[|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return None if match is None else match.group(1).strip()


def main():
    """Check the tag against the package version and the changelog.

    Returns:
        0 when everything agrees; exits non-zero with a message otherwise.
    """
    if len(sys.argv) != 2:
        sys.exit("usage: check_version_consistency.py <tag>")
    tag = sys.argv[1]

    if not tag.startswith("v"):
        sys.exit(f"FAIL: tag {tag!r} does not start with 'v'")
    version = tag[1:]

    packaged = packaged_version()
    if packaged != version:
        sys.exit(
            f"FAIL (M7.1.1): tag {tag} means version {version}, but "
            f"src/moonbuggy/__init__.py says {packaged}"
        )

    section = changelog_section(version)
    if section is None:
        sys.exit(f"FAIL (M7.1.3): CHANGELOG.md has no section for {version}")
    if not section:
        sys.exit(f"FAIL (M7.1.3): the CHANGELOG.md section for {version} is empty")

    print(f"OK: tag {tag}, package {packaged}, changelog section present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Test all four outcomes by hand**

```bash
.venv/bin/python scripts/check_version_consistency.py v0.1.0
```
Expected: `OK: ...`, exit 0.

```bash
.venv/bin/python scripts/check_version_consistency.py v0.2.0
```
Expected: FAIL naming M7.1.1 and both numbers.

```bash
.venv/bin/python scripts/check_version_consistency.py 0.1.0
```
Expected: FAIL about the missing `v` prefix.

Temporarily rename the `## [0.1.0]` heading in `CHANGELOG.md`, rerun the first command, and expect a FAIL naming M7.1.3. Restore it.

- [ ] **Step 7: Verify the static gates still pass on the new files**

```bash
make lint format-check typecheck docs
```
Expected: PASS. `scripts/` is linted and formatted (though not type-checked), and `interrogate`/`pydoclint` run over `src/` only — but `make docs` also confirms `conf.py` still imports `__version__` cleanly after the dynamic-version change.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/moonbuggy/__init__.py scripts/check_version_consistency.py CHANGELOG.md
git commit -m "M7.1: one version literal, and a check that the tag agrees

The number lived in pyproject.toml and __init__.py, with conf.py and oss_hunt
reading the second. hatchling reads the literal now, so there is one number
and the tag is checked against it."
```

---

## Task 16: Teach `check_fresh_install.sh` to test a built wheel

**Files:**
- Modify: `scripts/check_fresh_install.sh`

**Interfaces:**
- Produces: `check_fresh_install.sh [--wheel PATH]`. With no argument it installs the repo (`pip install .`), preserving criteria H1/H2 exactly as they run today. With `--wheel`, it installs that file instead. Task 18 calls the second form.

**Why this shape (M7.4.1):** one script with two callers, not a second copy that drifts. Everything after the install — the generated unseen project, the bare run, the JSONL validation, the grep check — is identical, and that part is the actual test.

- [ ] **Step 1: Add argument parsing after the `set -euo pipefail` line**

```bash
# M7.4.1: with no argument this installs the repository, which is criteria
# H1/H2 exactly as they have always run. With --wheel it installs a built
# artifact instead, which is what the release workflow needs: the wheel that
# is about to be published, not the source tree it came from.
WHEEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --wheel)
      WHEEL="${2:?--wheel needs a path}"
      shift 2
      ;;
    *)
      echo "usage: check_fresh_install.sh [--wheel PATH]" >&2
      exit 2
      ;;
  esac
done
```

- [ ] **Step 2: Branch the install step**

Replace:

```bash
echo "==> installing moonbuggy from $REPO"
"$WORK/venv/bin/pip" install --quiet "$REPO"
```

with:

```bash
if [ -n "$WHEEL" ]; then
  test -f "$WHEEL" || { echo "FAIL: no such wheel: $WHEEL" >&2; exit 2; }
  echo "==> installing moonbuggy from the built wheel $WHEEL"
  "$WORK/venv/bin/pip" install --quiet "$WHEEL"
else
  echo "==> installing moonbuggy from $REPO"
  "$WORK/venv/bin/pip" install --quiet "$REPO"
fi
```

- [ ] **Step 3: Verify the existing behaviour is unchanged**

```bash
make check-fresh-install
```
Expected: PASS, ending in the same `PASS: H1 (clean install) and H2 ...` line. This is the criterion that must not regress — H1/H2 are Phase 1 acceptance criteria and this script is their only check.

- [ ] **Step 4: Verify the new path**

```bash
.venv/bin/python -m build --wheel --outdir /tmp/wheeltest .
./scripts/check_fresh_install.sh --wheel /tmp/wheeltest/moonbuggy-0.1.0-py3-none-any.whl
```
Expected: PASS, with the log line naming the wheel.

- [ ] **Step 5: Verify the failure path**

```bash
./scripts/check_fresh_install.sh --wheel /tmp/nonexistent.whl; echo "exit=$?"
```
Expected: `FAIL: no such wheel`, `exit=2`. A release workflow that silently tests the wrong thing is worse than one that fails.

- [ ] **Step 6: Commit**

```bash
git add scripts/check_fresh_install.sh
git commit -m "M7.4.1: let the H1/H2 script test a built wheel

One script, two callers. The release path needs to exercise the artifact
about to be published, not the source tree it was built from, and everything
after the install line is the same test either way."
```

---

## Task 17: Release preflight and gate

**Files:**
- Create: `.github/workflows/release.yml` (preflight and gate jobs only)

**Interfaces:**
- Produces: a `release.yml` triggered on `v*` tags whose first two jobs are `preflight` and `gate`. Tasks 18–20 add jobs to this file that depend on them.

- [ ] **Step 1: Write the preflight job (M7.1)**

```yaml
name: Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: read

jobs:
  preflight:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@<SHA>  # v5
        with:
          fetch-depth: 0          # M7.1.2 needs main's history, not just the tag
          persist-credentials: false

      # Each check is its own step so the log names which one tripped, rather
      # than a single script whose failure means "something about the version".
      - name: M7.1.1/M7.1.3 — tag, package version, and changelog agree
        run: python scripts/check_version_consistency.py "${GITHUB_REF_NAME}"

      - name: M7.1.2 — the tagged commit is an ancestor of main
        run: |
          git fetch --quiet origin main
          if ! git merge-base --is-ancestor "${GITHUB_SHA}" origin/main; then
            echo "FAIL (M7.1.2): ${GITHUB_SHA} is not an ancestor of main" >&2
            exit 1
          fi
          echo "OK: tagged commit is on main"

      - name: M7.1.4 — the version is not already on PyPI
        run: |
          VERSION="${GITHUB_REF_NAME#v}"
          CODE="$(curl -sS -o /dev/null -w '%{http_code}' \
            "https://pypi.org/pypi/moonbuggy/${VERSION}/json")"
          if [ "$CODE" = "200" ]; then
            echo "FAIL (M7.1.4): moonbuggy ${VERSION} already exists on PyPI." >&2
            echo "PyPI versions cannot be replaced. Ship the fix as the next patch." >&2
            exit 1
          fi
          echo "OK: version ${VERSION} is unused (HTTP ${CODE})"
```

- [ ] **Step 2: Write the gate job (M7.2)**

```yaml
  gate:
    needs: preflight
    runs-on: ubuntu-latest
    timeout-minutes: 360
    steps:
      - uses: actions/checkout@<SHA>  # v5
        with:
          persist-credentials: false
      - uses: ./.github/actions/setup
        with:
          python-version: "3.12"
      # check-all covers lint, format-check, typecheck, test, check-oracle,
      # check-spike, check-properties, check-robustness, check-mutmut and
      # check-fresh-install. These five are the gates it does not include.
      - run: make check-all PYTHON=python
        env:
          PYENV_PY: python
      - run: make docs PYTHON=python
      - run: make docs-test PYTHON=python
      - run: make docs-linkcheck PYTHON=python
      - run: make check-differential PYTHON=python
      - run: make oss-hunt PYTHON=python
```

"The last nightly was green" is a claim about a different commit, which is why this runs against the tag even though it duplicates work.

- [ ] **Step 3: Verify the workflow parses before tagging anything**

```bash
gh workflow view release.yml
```
Expected: the workflow is listed. A YAML error here surfaces only when a tag is pushed, and tags are awkward to retract.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "M7.1/M7.2: release preflight and gate

Four preflight checks, each its own step so the log says which one tripped.
The gate runs everything against the tagged commit."
```

---

## Task 18: Reproducible build and wheel smoke test

**Files:**
- Modify: `.github/workflows/release.yml` (add `build`, `build-again`, `reproducible`, `smoke` jobs)

**Interfaces:**
- Consumes: `preflight`, `gate` from Task 17; `check_fresh_install.sh --wheel` from Task 16.
- Produces: an uploaded artifact named `dist` containing the sdist and wheel, which Tasks 19–20 download.

- [ ] **Step 1: Resolve the artifact action SHAs**

```bash
gh api repos/actions/upload-artifact/git/ref/tags/v4 --jq .object.sha
gh api repos/actions/download-artifact/git/ref/tags/v4 --jq .object.sha
```

- [ ] **Step 2: Add the build job**

```yaml
  build:
    needs: [preflight, gate]
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@<SHA>  # v5
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: ./.github/actions/setup
        with:
          python-version: "3.12"
      # M7.3: without a pinned epoch the archives embed build time and two
      # builds of the same commit differ, which is what M7.3.1 checks for.
      - name: Build sdist and wheel
        run: |
          SOURCE_DATE_EPOCH="$(git log -1 --pretty=%ct)"
          export SOURCE_DATE_EPOCH
          echo "SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}"
          python -m pip install --upgrade build
          python -m build --sdist --wheel --outdir dist .
      - run: sha256sum dist/* | tee dist.sha256
      - uses: actions/upload-artifact@<SHA>  # v4
        with:
          name: dist
          path: |
            dist/
            dist.sha256
```

- [ ] **Step 3: Add a second, independent build job**

Copy the `build` job verbatim as `build-again`, changing only the job name, the artifact name to `dist-again`, and the checksum filename to `dist-again.sha256`. It must not `need` `build` — the point is two fresh runners building independently.

- [ ] **Step 4: Add the comparison job (M7.3.1)**

```yaml
  reproducible:
    needs: [build, build-again]
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/download-artifact@<SHA>  # v4
        with:
          name: dist
          path: a
      - uses: actions/download-artifact@<SHA>  # v4
        with:
          name: dist-again
          path: b
      - name: M7.3.1 — the two builds are byte-identical
        run: |
          sed 's|  dist/|  |' a/dist.sha256 | sort > a.sums
          sed 's|  dist/|  |' b/dist-again.sha256 | sort > b.sums
          if ! diff -u a.sums b.sums; then
            echo "FAIL (M7.3.1): two builds of the same tag differ." >&2
            echo "Usually SOURCE_DATE_EPOCH is not reaching the build." >&2
            exit 1
          fi
          echo "OK: builds are byte-identical"
          cat a.sums
```

- [ ] **Step 5: Add the smoke test job (M7.4)**

```yaml
  smoke:
    needs: build
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@<SHA>  # v5
        with:
          persist-credentials: false
      - uses: actions/setup-python@<SHA>  # v6
        with:
          python-version: "3.12"
      - uses: actions/download-artifact@<SHA>  # v4
        with:
          name: dist
          path: dist
      # Deliberately NOT ./.github/actions/setup: that installs the project
      # from source, which would put the repo's own copy on the path and make
      # this test say nothing about the wheel.
      - name: M7.4.2 — the wheel reports the tag's version
        run: |
          python -m venv /tmp/vercheck
          /tmp/vercheck/bin/pip install --quiet dist/*.whl
          REPORTED="$(/tmp/vercheck/bin/moonbuggy --version | awk '{print $2}')"
          EXPECTED="${GITHUB_REF_NAME#v}"
          if [ "$REPORTED" != "$EXPECTED" ]; then
            echo "FAIL (M7.4.2): wheel reports ${REPORTED}, tag says ${EXPECTED}" >&2
            exit 1
          fi
          echo "OK: wheel reports ${REPORTED}"
      - name: M7.4.3 — the wheel runs end to end on an unseen project
        run: ./scripts/check_fresh_install.sh --wheel "$(ls dist/*.whl)"
        env:
          PYENV_PY: python
```

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "M7.3/M7.4: build twice, compare, then smoke-test the wheel

The smoke job deliberately does not use the shared setup action -- installing
from source would put the repo's copy on the path and the test would say
nothing about the artifact. This is what stands in for a TestPyPI dry run."
```

---

## Task 19: Publish

**Files:**
- Modify: `.github/workflows/release.yml` (add the `publish` job)

**Interfaces:**
- Consumes: the `dist` artifact and the `reproducible` and `smoke` jobs.

- [ ] **Step 1: Configure trusted publishing on PyPI (M7.5.1)**

On pypi.org → your account → Publishing → add a pending publisher:

- PyPI project name: `moonbuggy`
- Owner: `jhamon`
- Repository: `moonbuggy`
- Workflow name: `release.yml`
- Environment name: `pypi`

This is a *pending* publisher because the project does not exist on PyPI yet; it becomes a normal one after the first publish. No API token is created — that is the point of M7.5.1.

- [ ] **Step 2: Create the `pypi` environment with a required reviewer (M7.5.2)**

GitHub → Settings → Environments → New environment → `pypi` → Required reviewers → add yourself.

This is the one deliberate human step in the release, and it is placed after every automated gate has already passed, so approving it means "the gates were green and I still want this version spent".

- [ ] **Step 3: Resolve the publish action SHA**

```bash
gh api repos/pypa/gh-action-pypi-publish/git/ref/tags/release/v1 --jq .object.sha
```

- [ ] **Step 4: Add the publish job**

```yaml
  publish:
    needs: [reproducible, smoke]
    runs-on: ubuntu-latest
    timeout-minutes: 15
    environment:
      name: pypi
      url: https://pypi.org/p/moonbuggy
    permissions:
      # M7.5.1: OIDC, so no long-lived API token exists in repository secrets.
      id-token: write
      # M7.5.3: build provenance attestations are signed with the same identity.
      attestations: write
      contents: read
    steps:
      - uses: actions/download-artifact@<SHA>  # v4
        with:
          name: dist
          path: dist
      # dist.sha256 rides along in the artifact and is not a distribution file;
      # twine rejects the upload if it is left in the directory.
      - run: rm -f dist/dist.sha256 dist.sha256
      - uses: pypa/gh-action-pypi-publish@<SHA>  # release/v1
        with:
          attestations: true
```

- [ ] **Step 5: Verify the artifact layout matches what the job expects**

Before tagging, confirm from the Task 18 run's artifact that `dist/` contains exactly one `.whl`, one `.tar.gz`, and the checksum file the step above removes. A stray file makes the upload fail after every gate has passed — the most expensive place to discover it.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "M7.5: publish over OIDC, behind one human approval

No long-lived token exists. The approval sits after every automated gate, so
approving means 'the gates were green and I still want this version spent'."
```

---

## Task 20: GitHub Release and post-publish verification

**Files:**
- Modify: `.github/workflows/release.yml` (add `release` and `verify` jobs)

- [ ] **Step 1: Add the GitHub Release job (M7.6.2, M7.6.3)**

```yaml
  release:
    needs: publish
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write   # the only job in this repository that writes to it
    steps:
      - uses: actions/checkout@<SHA>  # v5
        with:
          persist-credentials: false
      - uses: actions/download-artifact@<SHA>  # v4
        with:
          name: dist
          path: dist
      - run: rm -f dist/dist.sha256
      # M7.6.2: the notes are extracted verbatim. The workflow moves what a
      # person wrote; it does not generate anything.
      - name: Extract the changelog section for this tag
        run: |
          VERSION="${GITHUB_REF_NAME#v}"
          python - "$VERSION" > notes.md <<'PY'
          import re, sys
          version = sys.argv[1]
          text = open("CHANGELOG.md").read()
          pattern = rf"^## \[{re.escape(version)}\].*?$(.*?)(?=^## \[|\Z)"
          match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
          if match is None:
              sys.exit(f"no CHANGELOG.md section for {version}")
          sys.stdout.write(match.group(1).strip() + "\n")
          PY
          cat notes.md
      - run: gh release create "${GITHUB_REF_NAME}" dist/* --title "${GITHUB_REF_NAME}" --notes-file notes.md
        env:
          GH_TOKEN: ${{ github.token }}
```

The preflight already proved this section exists (M7.1.3), so the extraction cannot come up empty here — but it exits non-zero rather than posting an empty release if it somehow does.

- [ ] **Step 2: Add the post-publish verification job (M7.7.1)**

```yaml
  verify:
    needs: publish
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@<SHA>  # v5
        with:
          persist-credentials: false
      - uses: actions/setup-python@<SHA>  # v6
        with:
          python-version: "3.12"
      # PyPI's CDN takes a moment to serve a new version. Retry rather than
      # failing a release that actually succeeded.
      - name: Install from real PyPI
        run: |
          VERSION="${GITHUB_REF_NAME#v}"
          python -m venv /tmp/verify
          for attempt in 1 2 3 4 5; do
            if /tmp/verify/bin/pip install --quiet "moonbuggy==${VERSION}"; then
              echo "installed on attempt ${attempt}"
              break
            fi
            if [ "$attempt" = "5" ]; then
              echo "FAIL (M7.7.1): could not install moonbuggy==${VERSION}" >&2
              exit 1
            fi
            sleep 30
          done
          /tmp/verify/bin/moonbuggy --version
      - name: Run the smoke test against the published package
        run: ./scripts/check_fresh_install.sh
        env:
          PYENV_PY: python
```

Note the second step calls the script in its default mode against the repository, which is not the same as testing the published wheel. Change it to install from PyPI into the script's venv only if a `--pypi` mode is added; otherwise this step's value is the install-and-version check above it. Leave the comment in the runbook (Task 21) saying so, rather than overstating what `verify` proves.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "M7.6/M7.7: release notes a person wrote, and a check after the fact

Post-publish verification cannot undo a bad release. It converts 'we think it
worked' into a check, which is all it claims to be."
```

---

## Task 21: The runbook, and cutting v0.1.0

**Files:**
- Create: `docs/releasing.md`
- Modify: `docs/index.md` (add to the toctree)

- [ ] **Step 1: Write `docs/releasing.md` (M7.8)**

Cover, in this order:

1. **Before you tag** — bump `__version__` in `src/moonbuggy/__init__.py`, move `CHANGELOG.md`'s `Unreleased` entries into a new dated section, commit to `main`.
2. **Cutting the release** — `git tag v0.1.0 && git push origin v0.1.0`, then approve the `pypi` environment when GitHub asks.
3. **What each job checks** — one line per job: `preflight`, `gate`, `build`/`build-again`, `reproducible`, `smoke`, `publish`, `release`, `verify`, each naming its criterion.
4. **When a job fails** — preflight and gate failures cost nothing; delete the tag (`git push --delete origin v0.1.0`), fix, re-tag. After `publish` succeeds the version is spent.
5. **A bad release is yanked, never replaced (M7.8.2)** — `pip` will not serve a yanked version to a new install but existing pins keep working; the fix ships as the next patch. PyPI does not permit reusing a version number, which is why the wheel smoke test runs before publishing rather than after.
6. **Branch protection** (from Task 14) — the four required checks on `main`, so a maintainer restoring the repository knows what to re-create.
7. **What `verify` does and does not prove** — it confirms the published artifact installs and reports the right version; the end-to-end guarantee comes from `smoke`, which ran before publishing.

- [ ] **Step 2: Add it to the docs toctree**

In `docs/index.md`, add `releasing` to the toctree alongside the existing entries.

- [ ] **Step 3: Verify the docs build**

```bash
make docs
```
Expected: PASS. `-W` means a document not in any toctree is a warning and therefore an error.

- [ ] **Step 4: Commit and push**

```bash
git add docs/releasing.md docs/index.md
git commit -m "M7.8: the release runbook

Including the part that matters most: a bad version is yanked, never
replaced, and the fix ships as the next patch."
git push
```

- [ ] **Step 5: Confirm `main` is green before tagging**

```bash
gh run list --branch main --limit 5
```
Expected: the latest CI and Docs runs pass. Tagging a red `main` means finding out in the gate job, forty minutes later.

- [ ] **Step 6: Cut the release**

```bash
git tag v0.1.0
git push origin v0.1.0
gh run watch
```

Approve the `pypi` environment when prompted. Expected: every job passes and `publish` uploads.

- [ ] **Step 7: Verify the release is real**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/moonbuggy/0.1.0/json
```
Expected: `200`.

```bash
python3.12 -m venv /tmp/final && /tmp/final/bin/pip install -q moonbuggy && /tmp/final/bin/moonbuggy --version && rm -rf /tmp/final
```
Expected: `moonbuggy 0.1.0`. This is the whole point of Phase 3 — a stranger's install, run from a machine with no checkout.

```bash
gh release view v0.1.0
```
Expected: the notes match the `CHANGELOG.md` section, with the sdist and wheel attached.

- [ ] **Step 8: Open the next `Unreleased` section**

```bash
git checkout main && git pull
```

Task 15 already wrote an empty `## [Unreleased]` heading above the `0.1.0` section, so confirm it is still there rather than adding a second one. If it is missing, add it and commit:

```bash
git add CHANGELOG.md
git commit -m "M7.6.1: open the next Unreleased section"
git push
```

---

## Verification summary

Every criterion in the spec, and where it is checked:

| Criterion | Task | Check |
|---|---|---|
| M5.1.1 | 1 | `make lint` exits 0 |
| M5.1.2 | 1 | `grep -rn "# noqa"` finds nothing |
| M5.1.3 | 1 | every disabled rule has a comment |
| M5.2.1 | 2 | `make format-check` exits 0 |
| M5.2.2 | 2 | reformat is its own commit |
| M5.2.3 | 2 | `check-all` diff before/after |
| M5.3.1 | 6 | `make typecheck` clean, no overrides remain |
| M5.3.2 | 6 | every `type: ignore` has a code and a reason |
| M5.3.3 | 6 | every public `Any` has a comment |
| M5.4.1–3 | 1,2,3,8 | targets exist, joined to `check-all`, fresh venv runs them |
| M6.1.1 | 9 | Actions timing view |
| M6.1.2–4 | 9,13 | timeouts, concurrency, cache key |
| M6.2, M6.3 | 11 | `gh workflow run` on each |
| M6.3.1 | 11 | `workflow_dispatch` present |
| M6.3.2 | 11 | deliberate failure opens one issue |
| M6.4.1–3 | 12 | HTTP 200, links added, M3.1.5 annotated |
| M6.5.1–3 | 13 | repo-wide greps |
| M6.6.1–2 | 14 | failing PR is blocked from merge |
| M7.1.1–4 | 15,17 | four preflight steps, tested locally first |
| M7.2.1 | 17 | gate job |
| M7.3.1 | 18 | two builds, checksums compared |
| M7.4.1–3 | 16,18 | `--wheel` flag, version check, end-to-end run |
| M7.5.1–3 | 19 | OIDC, `pypi` environment, `attestations: true` |
| M7.6.1–3 | 15,20 | CHANGELOG, extracted notes, attached artifacts |
| M7.7.1 | 20 | install from real PyPI |
| M7.8.1–2 | 21 | `docs/releasing.md` |
