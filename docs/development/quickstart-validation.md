# Quickstart first-run validation checklist

Internal development doc — not linked from the public site.

**What this is for.** Criterion M3.3.11 is only partially met: `make
check-fresh-install` covers the mechanical part (clean virtualenv, wheel,
bare `moonbuggy` on an unseen project), but no human has followed
[the quickstart page](../quickstart.md) on a machine that never had moonbuggy.
This checklist is the script for that human run. **Preparing it was DX's job;
executing it is Jennifer's call** — nobody on the dev side should run it and
call the criterion closed.

**Machine requirements.** A machine (or fresh container/VM) where moonbuggy has
*never* been installed — no `pip install moonbuggy` in any interpreter, no
`moonbuggy` on `PATH`, no `~/.moonbuggy` or project `.moonbuggy/` cache. Python
3.12+. A scratch Python project with a pytest suite, not moonbuggy's own repo.
Before starting, record `python --version`, OS, and how the environment was
made fresh (new user account, container, wiped venv).

Every step below follows the quickstart page in order. At each step: run the
command, compare against "Expected", and if reality differs, write down what
you saw before moving on — a mismatch is a finding, not a mistake.

---

## Step 1 — Start it

### 1a. Help screen (agent path)

```console
$ uv run --with moonbuggy moonbuggy -h
```

**Expected:** a help screen listing `run`, `show`, `why`, and the flags used
later (`--include`, `--pytest-arg`). No configuration file is mentioned as
required. First download may take a minute (uv fetching the package).

**Failure looks like:** `uv: command not found` (uv not installed — the
quickstart assumes it), a traceback instead of a help screen, or a version
error mentioning Python < 3.12.

### 1b. First run from the project root

```console
$ uv run --with moonbuggy moonbuggy --include src/yourpkg --pytest-arg=-q
```

(Use your package's real import path in place of `src/yourpkg`.)

**Expected:** the run starts; the first thing it does is execute your suite
once, unmutated, to build the coverage map. Progress then streams line by line,
first word a verdict (`KILLED`, `SURVIVED`, `NO_COVERAGE`, …).

**Failure looks like:** red-baseline refusal — a message that the suite is
failing and the run is refused (exit 2). That message is correct behaviour if
your suite is genuinely red; a bug if your suite is green. Also a finding: any
traceback, any "no configuration file found" style complaint, or a suggestion
to scaffold/init something.

### 1c. Self-install path

```console
$ pip install moonbuggy
$ moonbuggy
```

**Expected:** install succeeds without warnings that matter; bare `moonbuggy`
from the project root runs as in 1b.

**Failure looks like:** pip resolving to a Python older than 3.12, install
errors on any dependency, or `moonbuggy: command not found` after install.

---

## Step 2 — Read the output

Pick one `SURVIVED` and one `NO_COVERAGE` line from the run (if the project
produced neither, note that — pick a different scratch project with weaker
tests; this checklist needs at least one survivor to complete).

```console
$ grep -E '^(SURVIVED|NO_COVERAGE)' .moonbuggy/results.txt
```

**Expected:** only finding lines match; each has `file:line`, an operator
name, `nearest_test=`, `tests_run=`, and an `id=`.

**Failure looks like:** the grep matches nothing but the summary said
`SURVIVED>0`; `.moonbuggy/` doesn't exist; fields named differently from the
page's example.

---

## Step 3 — Look at one survivor

```console
$ moonbuggy show <id>
```

**Expected:** a block with `id`, `status SURVIVED`, `location`, `operator`,
`nearest_test`, `tests_run`, and a `diff` section showing exactly one small
change. Check that the diff's `+` line really is what a mutation of the
`-` line would produce.

**Failure looks like:** unknown-id error for an id copied verbatim from
`results.txt`; a diff with no change; multiple unrelated hunks.

---

## Step 4 — Fix it

Add the boundary test the survivor suggests (the page's example:
`test_exactly_ten_does_not_qualify`). Then re-measure just that mutant:

```console
$ moonbuggy run <id>
```

**Expected:** status flips to `KILLED`, exit code 0, and the new test appears
in the `selected` list and in `failed`. Confirm the other suite files were
*not* re-run (the page stresses this is one mutant, not the suite).

Then the batch re-check:

```console
$ grep -E '^(SURVIVED|NO_COVERAGE)' .moonbuggy/results.txt | moonbuggy run -
```

**Expected:** each piped id is re-measured and reported, one block per line.

**Failure looks like:** `run` serves an instant cached verdict for the id you
just targeted (the page promises it never does — that promise is the point of
the command); the new test is missing from `selected`; exit code 1 despite
status `KILLED`.

If a survivor refuses to die:

```console
$ moonbuggy why <id>
```

**Expected:** the new test appears in the listed selection; cache state is
named. Nothing is re-run.

---

## Step 5 — Re-run

```console
$ moonbuggy
```

**Expected:** a one-line summary like
`moonbuggy: KILLED=… NO_COVERAGE=… SURVIVED=… cached=… -> .moonbuggy/results.jsonl`;
the fixed mutant now counts as `KILLED`; `cached=` is large (most untouched
mutants were not re-run); noticeably faster than the first run.

**Failure looks like:** every mutant re-run (cached=0), summary line missing
or in a different format than the page shows, or `.moonbuggy/results.jsonl`
not written.

---

## Reporting

For each step record: pass / mismatch / blocked, plus the exact output on any
mismatch. Findings go to the board (open a task or comment against this
checklist's parent). Do **not** fix anything yourself mid-run — an observer
fixing as they go destroys the evidence that a fresh human can follow the page.

**When the criterion may be flipped to met:** Jennifer's run completed with
zero mismatches, or every mismatch has a filed finding and the quickstart page
has been corrected and re-validated on the affected steps only.
