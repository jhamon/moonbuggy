# Troubleshooting

**Audience:** moonbuggy has said something you did not expect, and you want to
know what to do about it.

Every message the CLI can emit is here, with its cause and its fix. If you have
one that is not on this page, it is a documentation bug — moonbuggy is supposed
to have exactly one way of telling you it cannot proceed.

**moonbuggy never prints a traceback as its user-facing output.** If you see
one, that is a crash rather than a diagnosis, and it is worth reporting as a
bug regardless of what your project was doing.

## Exit codes first

| code | meaning |
|---|---|
| 0 | ran to completion, no survivors |
| 1 | ran to completion, at least one survivor |
| 2 | did not run — one of the messages below |

Exit 1 is a *result*. In CI, `moonbuggy; test $? -le 1` means "run it and do not
fail the build on findings"; plain `moonbuggy` means survivors fail the build.

---

## "does not look like a pytest project"

```{code-block} text
moonbuggy: /path does not look like a pytest project (no pytest.ini,
tox.ini, setup.cfg, pyproject.toml or conftest.py found). Run moonbuggy from
your project root, or pass --project.
```

**Cause:** the working directory has none of the markers a pytest project
usually has.

**Fix:** run it from the directory where you would run `pytest`, or point at
that directory with `--project path/to/project`.

## "No Python source found under …"

**Cause:** the project has tests but no non-test Python that moonbuggy is
willing to mutate. Test files, `conftest.py` and `setup.py` are never mutated,
and neither is anything under `tests/`, `docs/`, `build/`, `.venv/` and similar.

**Fix:** `--source path/to/package` says explicitly what to mutate.

## "Found several candidate packages (…)"

**Cause:** more than one importable package at the project root, so there is no
single obvious thing to mutate. moonbuggy refuses to pick rather than mutating
the wrong one and wasting the run.

**Fix:** `--source path/to/the/one/you/meant`.

## "the test suite is already failing before any mutation"

```{code-block} text
moonbuggy: the test suite is already failing before any mutation (2 of 47 tests):
  tests/test_orders.py::test_refund
  tests/test_orders.py::test_partial_refund
Mutation results against a red baseline are meaningless -- every mutant those
tests cover would be reported KILLED regardless of the mutation. Fix the suite,
then run moonbuggy again. No mutation results were produced.
```

**Cause:** exactly what it says. moonbuggy ran your suite twice before mutating
anything, and those tests failed both times.

**Why it refuses rather than warning:** a red baseline does not produce *weak*
results, it produces *flattering* ones. Every mutant covered by a permanently
failing test is reported `KILLED`, and the score goes up.

**Fix:** fix the tests. If they are expected failures, mark them `xfail` so
pytest does not report them as failures.

## "no tests ran"

**Cause:** pytest collected nothing from your project root.

**Fix:** check that plain `pytest` from the same directory collects your tests.
The usual causes are a `testpaths` setting pointing elsewhere, or running from
a directory above the one your tests live in.

## "skipping <file>: syntax error at line N"

**Cause:** that file does not parse. moonbuggy names it, skips it, and carries
on with the rest — one broken file during editing is not a reason to abandon
the other forty.

**Fix:** fix the file, or `--exclude` it if it is not meant to be valid Python.

If *every* file is unreadable you get `none of the N source files could be read
as Python, so there is nothing to mutate` and exit 2, which is deliberately
different from "no mutants generated" — the latter would read as good news.

## "cannot decode <file> as utf-8"

**Cause:** the file contains bytes that are not valid in the encoding it
declares (or in UTF-8, if it declares nothing).

**Fix:** add a PEP 263 coding declaration — `# -*- coding: latin-1 -*-` — as the
first or second line. moonbuggy honours declared encodings; what it will not do
is guess, because a file decoded wrongly gets mutated wrongly and the status it
produces is about a file that does not exist.

## "N site(s) too deeply nested to mutate"

**Cause:** an expression nested thousands of levels deep — usually generated
code — beyond what can be rewritten.

**Fix:** none needed, usually. The message exists so that "fewer mutants than
expected" is distinguishable from "this file has fewer mutable sites", and it
names the first affected line.

## "N of M selected tests cannot be found from <dir>"

**Cause:** pytest's rootdir is not the directory moonbuggy is running in,
usually because an enclosing directory has its own pytest configuration — a
monorepo, or a checkout inside another project. The node ids in the coverage map
are then relative to that outer directory and do not resolve.

**Fix:** run moonbuggy from the outer directory, or pass `--project` to point at
it.

**Why it refuses:** because the alternative was worse. Before this check
existed, this situation produced hundreds of `SUSPICIOUS` mutants with no
explanation, which reads as a property of the code under test rather than a
misconfiguration.

## "coverage pass failed (pytest exit N)"

**Cause:** pytest could not complete the instrumented run at all — a collection
error, an internal error, or a usage error.

**Fix:** run the same suite yourself under coverage and read pytest's own
message:

```{code-block} console
$ pytest -q --cov=your_package --cov-context=test --cov-report=
```

---

## A mutant is `SUSPICIOUS`

`SUSPICIOUS` is not a finding about your code. It means moonbuggy could not
reach a defensible answer, and there are two causes.

**A covering test is flaky.** Its outcome differed between two unmutated runs of
your suite, so neither `KILLED` nor `SURVIVED` would mean anything. Fix the
flaky test — it is undermining more than this tool — or run with
`--flaky-probe 0` to turn the detection off and accept confident answers that
may be wrong.

**The mutant's process died without an exit code.** Usually a test that calls
`os._exit()`, or a mutation that makes the code under test do so. There is no
exit code to read, so there is no status to give.

## A mutant is `TIMEOUT`

The mutation made the tests take longer than `--timeout` (default 30 seconds).
Almost always an infinite loop — `n += 1` becoming `n -= 1` inside a `while` is
the classic. Practically it means your suite would notice, since it would hang.

If your suite is fast, lowering `--timeout` makes these cheap. If your suite is
genuinely slow, raise it, or `# moonbuggy: skip` the line.

## Everything is `NO_COVERAGE`

No test executes those lines at all. The coverage map is empty or nearly so.
(Before 0.1.3 this arrived as `SURVIVED` with `tests_run=0`.)

A handful of these is a real finding about your suite — write the tests, or
delete the code. *Everything* is almost always a finding about the run.

Check that the coverage pass measured the right thing: `--source` must point at
the package your tests actually import. If your tests import an *installed* copy
of your package rather than the working tree, coverage records the installed
path and moonbuggy mutates the working tree — two different files. Installing
your project in editable mode (`pip install -e .`) resolves it.

## The score got worse after I added tests

Check whether the mutant count changed too. Adding tests does not add mutants,
but editing source does — and a new mutable line arrives as a survivor until
something covers it. Compare mutant counts, not just percentages.

## Runs are slower than expected

See [Making runs fast](making-runs-fast.md). The two most common causes are a
broad end-to-end test being selected for nearly every mutant, and `-n` being
used where `--jobs` was meant — `-n` opts out of the warm session and makes
every mutant pay full process startup again.
