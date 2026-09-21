# Closing the loop

**Audience:** you have read moonbuggy's findings — or a program has — and you
want the whole cycle in one place: find the gaps, hand them to something that
fixes them, and prove the fixes worked. The consumer on the other end of the
export can be a human with an editor or an automated agent; the loop is the
same either way, because every step of it is a file format or a command with a
frozen, versioned shape.

The loop in one line:

```{code-block} text
run -> export -> read survivors.jsonl -> fix tests -> run <id> -> read the verdict
```

Nothing in the loop touches a run's artifacts: the export is a new file, and
re-measuring one mutant leaves `results.jsonl` exactly as the full run wrote
it. Each section below owns one leg.

## The four commands

| command | what it does in the loop |
|---|---|
| `moonbuggy` | produces the findings: one line per mutant in `.moonbuggy/results.txt`, one JSON record per mutant in `.moonbuggy/results.jsonl` |
| `moonbuggy export` | copies just the findings — every `SURVIVED` and `NO_COVERAGE` record — to `survivors.jsonl`, one JSON object per line |
| (your work) | strengthen or write a test, guided by the record's `diff`, `nearest_test` and `survival_reason` |
| `moonbuggy run <id>` | re-measures that one mutant fresh and prints the verdict that answers "did the fix kill it?" |

All of it works with no configuration file and no knowledge of moonbuggy's
internals. See [Quickstart](quickstart.md) for getting the first run going.

## Step 1 — export the findings

After a full run, `moonbuggy export` writes `survivors.jsonl`:

```{code-block} console
$ moonbuggy export
moonbuggy: exported 4 findings from 4 records to /tmp/loopdemo/survivors.jsonl
```

One JSON object per finding, and nothing else: killed, timed-out, suspicious
and skipped mutants never export, so a zero-line file means "no findings" —
not "something went wrong". Check `schema` on the first line before parsing
the rest, exactly as you would for any other moonbuggy output.

## Step 2 — read each record

Every record carries the full `results.jsonl` envelope plus the export's own
fields. The ones that drive the fix:

| field | what to do with it |
|---|---|
| `id` | the re-measurement input. Pass it straight to `moonbuggy run`. |
| `status` | `SURVIVED` (tests ran, none objected) or `NO_COVERAGE` (no test executes the line at all). |
| `diff` | what the mutant changed — the question your test has to answer. |
| `nearest_test` | for a `SURVIVED`, where to start reading; the tests that already execute the line. |
| `survival_reason` | why it survived, as one closed-vocabulary token: `no_coverage`, `covered_unasserted`, `logging_noise` (only under `--include-logging-mutants`), `accepted_equivalent` (a reviewed equivalent — leave it alone), or `null` meaning *not yet classified*. Never treat `null` as a reason. |
| `original`, `mutated` | the exact before/after line, so a program can reason about the change without re-deriving it. |

The token set is frozen by the survival-reason vocabulary contract
([survival-reason-v1.md](https://github.com/jhamon/moonbuggy/tree/main/docs/contracts));
the export shape itself is frozen by
[survivor-export-v1.md](https://github.com/jhamon/moonbuggy/tree/main/docs/contracts).
A consumer written against those versions reads `survivors.jsonl` unchanged.

Two records from a small module, one of each finding kind:

```{code-block} text
{"id": "calc.py:2:comparison_swap:0", "status": "NO_COVERAGE", "survival_reason": "no_coverage", "tests_run": 0, "mutated": "if value >= ceiling:", "diff": "- if value > ceiling:\n+ if value >= ceiling:", "nearest_test": null}
{"id": "calc.py:8:constant_int:0", "status": "SURVIVED", "survival_reason": "covered_unasserted", "tests_run": 1, "mutated": "return value + 2", "diff": "- return value + 1\n+ return value + 2", "nearest_test": "test_calc.py::test_bump_runs"}
```

(abridged for reading; real records carry every envelope field). The first
says: *no test executes `calc.py:2` at all* — write a test that reaches the
line. The second says: *the line runs, nothing checks its result* — make an
existing test assert on it.

## Step 3 — fix the test, not the code

The record is a question about the tests, and `diff` is the question: *if the
code did this instead, would any test fail?* A mutation testing finding is
almost never a bug in the code — it is an untested behaviour. Strengthen a
test or write one; do not "fix" the mutant.

For the `NO_COVERAGE` record above, the fix is a test that reaches line 2 at
all. For the `covered_unasserted` one, the test called the function but never
asserted — the same trap as a suite that "covers" a line by executing it:

```{code-block} python
# before: the line was executed, never checked
def test_bump_runs():
    bump(1)

# after: the behaviour is pinned
def test_bump_adds_one():
    assert bump(1) == 2
```

## Step 4 — re-measure, and read the verdict honestly

`moonbuggy run <id>` measures the mutant fresh — never from the cache — with
the same coverage pass, selection and runner a full run uses:

```{code-block} console
$ moonbuggy run calc.py:8:constant_int:0
KILLED    calc.py:8 constant_int line=8 nearest_test=- tests_run=1 killreason=assertion_failed id=calc.py:8:constant_int:0
moonbuggy: KILLED=1  re-measured=1 (.moonbuggy/results.jsonl is unchanged)
```

Exit code `0` when the mutant is killed, `1` when it is not, so the step works
in a script. And because every exported `id` is valid input, the whole finding
set replays at once:

```{code-block} console
$ jq -r '.id' survivors.jsonl | moonbuggy run -
```

**The one verdict rule to get right:** `killreason` on the verdict line is
what tells you the kill was *real*.

- `killreason=assertion_failed` — a test's assertion failed under the
  mutation. **This is the only token that proves your new test actually
  checked the mutant's behaviour.** When you are verifying a fix, this is the
  transition to look for: the record was `SURVIVED` (or `NO_COVERAGE`), and
  the fresh verdict is `KILLED` with `assertion_failed`.
- `killreason=test_errored` — a test *errored* rather than asserted. This
  status is `KILLED_BY_ERROR`, and **it is not a kill to act on**: the mutant
  broke the code badly enough that something raised before anything could
  object. It counts as a kill in the mutation score, but it says nothing about
  whether your tests check the mutated line. If your new test "kills" a
  survivor this way, you have not verified anything — check what actually
  happened before counting it as progress.

A consumer gates on the transition, not on the status keyword alone:

```{code-block} text
SURVIVED -> KILLED (assertion_failed)   fixed: the new test objects to the mutation
SURVIVED -> KILLED_BY_ERROR (...)       not verified: something crashed first
SURVIVED -> SURVIVED                    still a finding
```

## The loop, checked

Every claim on this page is executed when the docs are tested, against a
throwaway project. The export carries what a consumer needs:

```{doctest}
>>> project = make_project({
...     "calc.py": "def bump(value):\n    return value + 1\n",
...     "test_calc.py": "from calc import bump\n\ndef test_bump_runs():\n    bump(1)\n",
... })
>>> _ = moonbuggy(cwd=project)
>>> proc = moonbuggy("export", cwd=project)
>>> proc.returncode
0
>>> findings = [json.loads(line) for line in (project / "survivors.jsonl").read_text().splitlines() if line]
>>> [(f["id"], f["status"], f["survival_reason"]) for f in findings]
[('calc.py:2:arithmetic_swap:0', 'SURVIVED', 'covered_unasserted'), ('calc.py:2:constant_int:0', 'SURVIVED', 'covered_unasserted')]
```

The fix is a real assertion, and re-measurement answers it:

```{doctest}
>>> _ = (project / "test_calc.py").write_text(
...     "from calc import bump\n\ndef test_bump_adds_one():\n    assert bump(1) == 2\n")
>>> proc = moonbuggy("run", "calc.py:2:constant_int:0", cwd=project)
>>> proc.stdout.split()[0], proc.returncode
('KILLED', 0)
```

The verdict line carries the only killreason that proves the test checked the
mutant:

```{doctest}
>>> "killreason=assertion_failed" in proc.stdout
True
```

And every exported id is valid input — the whole set re-measures in one
command:

```{doctest}
>>> proc = moonbuggy("run", *[f["id"] for f in findings], cwd=project)
>>> sorted(line.split()[0] for line in proc.stdout.splitlines()
...        if line.split()[0] in ("KILLED", "SURVIVED", "NO_COVERAGE"))
['KILLED', 'KILLED']
```

(Only two findings remain in this tiny project; a real export may also contain
`NO_COVERAGE` rows, which `run -` re-measures and gates on exactly the same
way.)

## Where to go next

- [Reading the output](reading-the-output.md) — the full JSONL schema, the
  plaintext line, and every field the export envelope carries.
- [Equivalent mutants](equivalent-mutants.md) — survivors that no test can
  kill, and the ledger that records the human decision to accept one.
- [Troubleshooting](troubleshooting.md) — when a run itself is lying to you.
