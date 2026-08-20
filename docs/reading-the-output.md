# Reading the output

**Audience:** agent authors and CLI users. Anything that has to consume
moonbuggy's output programmatically, or read it at 2am.

Every run writes three result files into `.moonbuggy/`:

`results.jsonl`
: The canonical record. One JSON object per line, one line per mutant.

`results.txt`
: A plaintext view, **derived from the JSONL** rather than written alongside it,
  so the two cannot drift apart.

`summary.json`
: The run itself rather than its mutants: one versioned JSON object with the
  counts, the totals, the wall time and the configuration that produced them.
  `--json` prints the same object to stdout. See *The run summary* below.

Two more things live in the directory and are not result files. `cache.json` is
written by every run that does not pass `--no-cache`; it is disposable, and
deleting it costs one slow run. `accepted.toml` appears once you run
`moonbuggy accept`, and it is the opposite: a checked-in record of human
decisions. If you gitignore `.moonbuggy/`, un-ignore that one file — see
[Equivalent mutants](equivalent-mutants.md#recording-the-decision-the-ledger).

The plaintext is also what moonbuggy prints to stdout whenever the output is
piped, redirected, or pinned to the agent format, so what you grep in a file is
exactly what you saw scroll past. At an interactive terminal stdout carries the
human report instead — the same findings, rendered to be read, described under
*The human report* below. Both files are written either way.

## Statuses

Seven keywords, and only seven. Each line of plaintext begins with one, so
`grep SURVIVED` needs no knowledge of anything else on this page.

| status | meaning | what to do |
|---|---|---|
| `KILLED` | a selected test's assertion failed under the mutation | nothing — your suite caught it |
| `KILLED_BY_ERROR` | a selected test errored out under the mutation | nothing to fix, but **read the count**; see below |
| `SURVIVED` | every selected test passed under the mutation | **read it**; this is the finding |
| `NO_COVERAGE` | no test executes the line, so none was selected | **read it**; this is the other finding |
| `TIMEOUT` | the mutation made the tests take longer than `--timeout` | usually an infinite loop; treat as killed-ish |
| `SUSPICIOUS` | moonbuggy cannot give a confident answer | investigate the *run*, not the code |
| `SKIPPED` | the mutant was suppressed: the line carries `# moonbuggy: skip`, or the mutation sits inside a logging call | nothing — but see [Logging calls](equivalent-mutants.md#logging-calls) for the second case |

:::{admonition} Changed in 0.1.3
:class: warning

`NO_COVERAGE` is new, and it took cases that used to be `SURVIVED`. A line no
test reaches was previously reported as `SURVIVED` with `tests_run=0`, so
**`grep SURVIVED` no longer returns every finding.** Anything that gates on
survivors — a CI step, a triage script, a dashboard — needs both keywords:

```{code-block} console
$ grep -E '^(SURVIVED|NO_COVERAGE)' .moonbuggy/results.txt
```

The exit code did not change: `NO_COVERAGE` exits `1` exactly as `SURVIVED`
does, so a gate that only reads the exit code is unaffected.
:::

:::{admonition} Changed in 0.1.4
:class: warning

`KILLED_BY_ERROR` is new, and it took cases that used to be `KILLED`. A kill
where the test raised rather than asserted is reported under the new keyword,
so **`grep KILLED` no longer returns every kill** — it now returns both, since
one keyword is a prefix of the other. Anchor the pattern to get only ordinary
kills:

```{code-block} console
$ grep -E '^KILLED ' .moonbuggy/results.txt
```

Neither the exit code nor the mutation score changed: a crash-kill is a kill,
it counts in the numerator, and it is not a finding.
:::

Three of these are worth expanding, because they are where a mutation tool
usually lies to you.

**`SURVIVED`** means tests ran and none of them objected. The mutated line is
exercised, and nothing asserts on what it produces — so the fix is a stronger
assertion in a test you already have. (Or the mutant is equivalent: the mutated
program genuinely behaves identically, and no test could tell. See
[Equivalent mutants](equivalent-mutants.md).)

**`NO_COVERAGE`** means no test was even selected: nothing in your suite
executes that line, so nothing could have caught the change, and `tests_run` is
always `0` and `nearest_test` always `-`. It is a finding rather than a skip —
an untested line is a gap, not an exclusion — but a different finding from
`SURVIVED`, with different work attached: write a test, or delete the code.
Being told this by a survivor list was the reason the two were split apart.

If *everything* comes back `NO_COVERAGE`, suspect the run rather than the
suite: it usually means the coverage pass measured a different copy of your
package than the one being mutated. See [Troubleshooting](troubleshooting.md).

**`KILLED_BY_ERROR`** means a test died rather than objected. The mutation
broke the code badly enough that something raised — `NameError`,
`AttributeError`, `TypeError` — and pytest recorded an error rather than a
failed assertion. That is still a kill: the mutation was noticed, it counts in
the score's numerator, and there is nothing to fix.

What it does not prove is that your tests *check* the mutated line. They
*execute* it, which is a weaker claim, and the two are worth telling apart
because a suite that only executes code can still score highly. Under the
default operators this is rare: most mutations there leave a program that runs
and merely computes something else. Under the `deep` tier's
`statement_deletion` it is the common case, since deleting a binding leaves
everything downstream undefined — which is exactly why the two statuses were
split before that operator shipped. `kwarg_drop`, in the same tier, is the
other one to expect it from: dropping a keyword argument the callee actually
requires makes every test raise `TypeError`, and that kill says nothing about
whether the tests check anything. The human report's footer says how many of
the kills were crashes, so the number above it can be read honestly.

`pytest.fail()` and a `pytest.raises` block whose exception never arrived both
count as ordinary `KILLED`: those are the test speaking as deliberately as
`assert` is. A failure during a fixture counts as an error, because a fixture
that raised has not checked anything either.

**`SUSPICIOUS`** is deliberate humility. moonbuggy reports it when a confident
status would not be supportable: a test covering the mutant behaved
inconsistently between two unmutated runs, or the mutant's process died without
an exit code (a test called `os._exit`, say). A tool that guessed `KILLED` or
`SURVIVED` here would produce a status indistinguishable from a real one and
wrong. See [Troubleshooting](troubleshooting.md).

## The plaintext line

One line per mutant, never wrapped, never containing a newline:

```{code-block} text
SURVIVED  app/pricing.py:14 comparison_swap line=14 nearest_test=tests/test_pricing.py::test_discount tests_run=3 id=app/pricing.py:14:comparison_swap:0
```

Whitespace-split gives you positional fields followed by `key=value` tokens:

| position | field | notes |
|---|---|---|
| 1 | status | one of the seven keywords |
| 2 | `file:line` | the location, in the form editors and terminals linkify |
| 3 | category | currently the operator name |
| 4+ | `key=value` | `line`, `nearest_test`, `tests_run`, `id` |

A field with no value prints as `-` rather than as an empty string, so the token
count per line is constant and a naive parser never has to special-case a
missing field.

**The diff is deliberately not on this line.** One line per mutant is what keeps
`grep`, `awk` and `wc -l` usable; a multi-line record would break all three.
Retrieve a diff with `moonbuggy show <id>`, and re-measure one mutant with
`moonbuggy run <id>`.

## Re-running one mutant

`moonbuggy run <id>` answers the fix-verify question — "I think this new test
kills that mutant" — without a full run. It uses the same coverage pass,
selection and runner, so its verdict means what a full run's verdict means, and
it adds the two things a one-line report cannot carry: every test selection
chose, and every one of them that failed.

```{code-block} console
$ moonbuggy run app/pricing.py:14:comparison_swap:0
```

Three properties are worth knowing:

- **It never serves a cached verdict for its target.** Re-measuring is the
  whole point. It does *store* the fresh verdict, under the same key a full run
  uses, so verifying a fix here makes the next full run shorter rather than
  longer. `--no-cache` turns the write off.
- **It does not touch `results.jsonl` or `results.txt`.** Those are the record
  of a *run*, complete with a summary; rewriting one line of them from a
  single-mutant measurement would leave a file that no longer describes itself.
  Run `moonbuggy` to refresh them.
- **The exit code matches a full run's.** `0` when every named mutant was
  killed, `1` when any of them is a finding — `SURVIVED` or `NO_COVERAGE`, both
  of which mean the mutation went unnoticed — and `2` when it could not run at
  all.

It takes several ids, and `-` reads them from stdin, one per line. Whole result
lines are accepted as well as bare ids, so the survivor set pipes straight back
in:

```{code-block} console
$ grep -E '^(SURVIVED|NO_COVERAGE)' .moonbuggy/results.txt | moonbuggy run -
```

Piped like that, `run` emits the same one-line-per-mutant format as
`results.txt`, so its output greps and pipes exactly as a run's does. At a
terminal it prints the block above instead; `--report` overrides either way.

A mutant a human has accepted as equivalent is annotated with its reason and
otherwise reported unchanged — the acceptance never alters the verdict or the
exit code, which is `--fail-on-unexplained`'s job on a full run.

## Why a mutant was handled the way it was

Two very different problems produce the identical symptom — a survivor that
will not die no matter what you write:

1. **selection never picked up your new test**, so it was never given a chance
   to object; or
2. **the verdict came from the cache**, so nothing ran at all.

A result line cannot tell them apart, and reading the source or running a
controlled experiment to find out costs minutes every time. `moonbuggy why`
costs one coverage pass and answers both:

```{code-block} console
$ moonbuggy why app/pricing.py:14:comparison_swap:0
```

```{code-block} text
id           app/pricing.py:14:comparison_swap:0
location     app/pricing.py:14
operator     comparison_swap
diff
  - if quantity > BULK:
  + if quantity >= BULK:
selection    the coverage pass saw 2 tests execute app/pricing.py:14
tests_run    2
selected     tests/test_pricing.py::test_bulk
             tests/test_pricing.py::test_single
cache        hit -- the next run replays SURVIVED (tests_run=2) without measuring it
cache_key    9ad65041277be0aea5806303770f8bd0acbfa179dffc988287b220cdc933993d
cache_covers app/pricing.py
             tests/test_pricing.py
run_inputs   pytest args: (none)   timeout: 30.0   python: /usr/bin/python3.12
last_run     SURVIVED  tests_run=2  (.moonbuggy/results.jsonl)
```

Field by field:

`selection`
: Where the selected set came from. Usually the coverage pass, run *just now*
  against the source as it stands — there is no line→test map on disk, and
  deliberately so: a stored map would answer for the code as it was. A
  module-level line says so instead, because an import-time line is attributed
  to no test and widens to the whole suite; a suppressed line says that, and
  names which suppression it was.

`tests_run`
: The size of the selected set, and the same number the result line's
  `tests_run=` token carries. This is where that token comes from.

`selected`
: Every test node id, one per line. If the test you just wrote is not here,
  problem 1 is your problem: nothing you assert in it can affect the verdict,
  because it is never run for this mutant.

`cache`
: Whether an entry exists **under this mutant's current key**. A `hit` means
  the next full run replays that verdict without measuring; a `miss` means it
  measures. `moonbuggy why` only ever reads the cache — it stores nothing,
  because it measures nothing.

`cache_key` / `cache_covers`
: The key and the files whose contents go into it. Editing any of them changes
  the key, and so does a change to the selected set itself, which is why a
  stale verdict cannot outlive the test that would kill it. `--no-cache` skips
  the lookup entirely.

`run_inputs`
: The rest of the key, from
  [`run_fingerprint`](api/index.md): the `--pytest-arg` values, the `--timeout`
  and the interpreter. Pass `why` the same flags your real run uses, or it
  describes a different entry from the one your run would find.

`last_run`
: What `results.jsonl` recorded, if a run has happened here. Historical, and
  deliberately separate from `cache` — the two disagree exactly when something
  has changed since, which is worth seeing.

When nothing is selected, `why` says so outright rather than printing an empty
list, and says what it means:

```{code-block} text
selection    the coverage pass saw no test execute lib.py:6
tests_run    0
selected     -
note         no test reaches this line, so a run reports NO_COVERAGE rather than
             SURVIVED -- nothing could have caught the mutation. Write a test
             that executes the line, or delete the code.
```

`why` never runs the mutant — that is what `moonbuggy run <id>` is for — so it
has no verdict to gate on and **always exits `0`** unless it could not explain
at all (exit `2`). It takes several ids and `-` on stdin exactly as `run` does,
and one coverage pass serves all of them.

`--json` emits one JSON object per mutant, one per line — JSONL like
`results.jsonl`, but its own set of keys: the `why` fields above, not the record
schema. There is no `schema`, `status`, `category` or `diff` on these lines, so
a `jq` filter written against `results.jsonl` (`select(.status=="SURVIVED")`)
matches nothing here.

```{code-block} console
$ moonbuggy why --json app/pricing.py:14:comparison_swap:0 | jq '{tests_run, cache_hit, next_run}'
```

The `next_run` key is `why`'s prediction of what a full run would do with this
mutant now, following the planner's own order of decisions: `skipped`,
`suspicious`, `cache`, `no_coverage` or `measure`.

Flaky tests in the selection are **not** probed for by default: a probe is
another whole unmutated suite run, and `why`'s selling point is that it answers
without measuring. `--flaky-probe N` turns it on, and then a flaky selection is
reported and `next_run` can be `suspicious`.

## The human report

The plaintext line above is what you get when output is piped, redirected, or
`MOONBUGGY_REPORT=agent` is set. At a terminal, with none of those in play,
moonbuggy prints something meant to be read instead: survivors grouped by
`file:line`, each with the code delta and a caret under exactly what changed.

Format selection checks four things in order: the `--report` flag, then
`MOONBUGGY_REPORT`, then whether `CI` is set in the environment (agent format
— a CI log is rarely a place for a human report), then whether stdout is a
terminal. In practice the `CI` tier rarely changes the outcome, since a CI
run's stdout is usually not a terminal anyway and would land on agent format
regardless; it exists for the harnesses where that assumption does not hold.

This is the literal stdout of one command against
`tests/fixtures/sample_project`, `--report human` forcing the format
regardless of terminal detection and `2>/dev/null` dropping stderr so only the
report remains:

```{code-block} console
$ moonbuggy --project tests/fixtures/sample_project --report human 2>/dev/null
```

```{code-block} text
moonbuggy  29 mutants across 6 files

sample/inventory.py:9
  SURVIVED  comparison_swap
    - return stock > 0 and not discontinued
    + return stock >= 0 and not discontinued
                   ^^
  SURVIVED  constant_int
    + return stock > 1 and not discontinued
                     ^
  2 tests run this line; first is
  tests/test_inventory.py::test_discontinued_item_is_not_available

sample/inventory.py:13
  SURVIVED  comparison_swap
    - if stock < target:
    + if stock <= target:
               ^^
  1 test runs this line; first is
  tests/test_inventory.py::test_restock_fills_to_target

sample/loops.py:10
  SURVIVED  comparison_swap
    - while n > 0:
    + while n >= 0:
              ^^
  2 tests run this line; first is
  tests/test_loops.py::test_countdown_of_zero_is_zero

sample/predicates.py:37
  SURVIVED  condition_negation
    - return [value for value in values if value]
    + return [value for value in values if not value]
                                           ^^^^^^^^^^
  1 test runs this line; first is
  tests/test_predicates.py::test_wanted_of_nothing_is_nothing

1 line no test reaches

sample/inventory.py:15
  NO_COVERAGE  constant_int
    - return 0
    + return 1
             ^
  no test runs this line at all

Problems with the run

sample/loops.py:12
  TIMEOUT  arithmetic_swap  (timed out after 30s)
    - n -= 1
    + n += 1
        ^^

5 survived, 1 no_coverage, 1 timeout, 21 killed, 1 skipped in 30.2s -- 21/28 killed, 75%
Full records: .moonbuggy/results.jsonl
exit 1 -- survivors, and lines no test reaches
```

That is exactly what lands in a file from `moonbuggy --report human >
report.txt`, and nothing more. Progress goes to stderr separately, so it is
not in the block above: while a run is in flight, a terminal shows a single
counter line that is redrawn in place as mutants finish (there is no static
text to show for it — depicting a frame of it here would misrepresent
something that never sits still), and each `SURVIVED` mutant scrolls past
underneath it as it is found, so a reader watching live sees a finding the
moment it lands rather than only at the end.

The location prints once per `file:line` group, with every mutant that lands
on it nested underneath — adjacent survivors that one new test could kill
together stay together, and `nearest_test` is shown once rather than repeated
per mutant. `KILLED` and `SKIPPED` mutants do not get a block; they only move
the footer counts. `TIMEOUT` and `SUSPICIOUS` mutants move below the survivors
under a `Problems with the run` heading — a timeout is treated as killed-ish
elsewhere in this doc, so mixing it in with survivors would misrepresent it as
a finding. A run with many `SUSPICIOUS` mutants collapses them to a single
line rather than one block each, because that many is almost always one root
cause and not eighty-four separate ones.

Flags that shape this view: `--color auto|always|never` (colour is never the
only carrier of meaning — the caret ruler works with `NO_COLOR` set, piped
through `less` without `-R`, or read by someone who can't distinguish red from
green), `--width N` to wrap at a fixed column count instead of the detected
terminal width, and `--no-progress` to suppress the live line drawn on stderr
while mutants are still running.

**None of this layout is a contract.** The grouping, the wording of the footer
sentence, the exact indentation, the `Problems with the run` heading — any of
it may change between releases as the report is refined. The only thing this
page guarantees byte for byte is the plaintext line format above, because a
golden test pins it. If you are scripting against moonbuggy's output, script
against `results.txt` or `results.jsonl`, never against what prints to a
terminal.

## The JSONL schema

Every line of `results.jsonl` is one object with these keys. Keys are sorted, so
lines are stable byte-for-byte for unchanged input.

| key | type | meaning |
|---|---|---|
| `schema` | integer | the record schema this line was written in; `3` today |
| `id` | string | `file:line:operator:index` — stable across runs for unchanged source |
| `status` | string | one of the seven keywords |
| `file` | string | path relative to the project root |
| `line` | integer | 1-based line number |
| `operator` | string | which mutation operator produced it |
| `category` | string | same as `operator` today; a separate taxonomy is deferred until there is survivor data to design one against |
| `nearest_test` | string or null | for survivors, the first covering test — where to start reading; always null for `NO_COVERAGE`, which has none |
| `tests_run` | integer | how many tests were selected for this mutant |
| `duration` | number | seconds spent running this mutant's tests |
| `module_level` | boolean | true when the line runs at import time, which widens selection to the whole suite |
| `suppressed` | boolean | true when the mutant was settled without running: the skip marker, or a suppressed logging mutant |
| `logging_call` | boolean | true when the mutation sits inside the arguments of a logging call, whether or not it was run |
| `original` | string | the source line before mutation, stripped of surrounding whitespace |
| `mutated` | string | the same line after mutation, stripped the same way |
| `diff` | string | two lines: `- original` then `+ mutated` |
| `accepted` | boolean | true when a live entry in the accepted-equivalents ledger covers this mutant |
| `accept_reason` | string or null | the reason recorded for it, or null |

`original` and `mutated` are the two operands `diff` is assembled from. They
are carried separately so a reader that wants the delta never has to parse a
rendered diff back apart — which is what the human report would otherwise have
to do to itself.

The `id` is worth understanding because it is the join key for anything that
tracks findings over time. It ends in an occurrence index because one line can
host several mutants — a line with two `+` operators produces two from the same
operator, and they need separate identities.

`schema` is on every line rather than in a header, because JSONL has no header:
a reader that has one line should be able to tell what that line means. Schema
`1` is anything written before the accepted-equivalents ledger existed and has
no `accepted`/`accept_reason` keys; schema `2` adds them and the `schema` field
itself; schema `3` adds `logging_call`, and with it widened what `suppressed`
means — on a schema-2 line `suppressed` is always the `# moonbuggy: skip`
marker, while on a schema-3 line it is that *or* a suppressed logging call, and
`logging_call` is the discriminator. moonbuggy upgrades an older line to today's shape as it reads it, so
`moonbuggy show` and the human report work on a results file written by an
older version — but a reader of its own should check the field rather than
assume, and a line with no `schema` key at all is schema 1 by definition.

## The run summary

Per-mutant data is JSONL because there is one object per mutant. A run has
exactly one summary, so it is a single JSON object instead: `summary.json`,
written into the output directory by every run. Nothing is added to
`results.jsonl` — every line of that file is still a mutant record, and
`wc -l` is still the mutant count.

`--json` prints that same object to stdout and prints nothing else there, so a
caller never has to parse totals out of a human sentence:

```{code-block} console
$ moonbuggy --json | jq '.counts.survived'
```

The per-mutant views are untouched by the flag: `results.txt` and
`results.jsonl` are written exactly as they always are, and `grep SURVIVED`
works the same with it and without it.

```{code-block} json
{
  "schema": 1,
  "record_schema": 3,
  "moonbuggy": "0.1.2",
  "total": 84,
  "cached": 71,
  "measured": 13,
  "elapsed": 4.117,
  "exit_code": 1,
  "counts": {
    "killed": 68, "killed_by_error": 3, "survived": 11, "no_coverage": 0,
    "timeout": 0, "suspicious": 1, "skipped": 1
  },
  "acceptance": {
    "accepted": 2, "unexplained": 9, "stale": 0, "ambiguous": 0,
    "orphaned": 0, "relocated": 0,
    "ledger": ".moonbuggy/accepted.toml", "fail_on_unexplained": false
  },
  "scope": {
    "diff_scoped": true, "since": "origin/main",
    "merge_base": "9f1c0e2…", "files": 3, "changed_lines": 41
  },
  "config": {
    "operators": null, "operators_selector": null,
    "include": [], "exclude": [],
    "pytest_args": ["-p", "no:randomly"], "timeout": 30.0,
    "include_logging_mutants": false, "logger_names": [],
    "jobs": 0, "workers": 0, "flaky_probe": 1, "cache": true
  }
}
```

| key | meaning |
|---|---|
| `schema` | the summary's own version — key off this, not off the presence of a field |
| `record_schema` | the version of the records in `results.jsonl` beside it |
| `moonbuggy` | the version that produced the run |
| `total` / `cached` / `measured` | mutants reported, served from the cache, and actually run |
| `elapsed` | wall time for the whole run, in seconds |
| `exit_code` | the code the run exited with, so the gate's answer need not be re-derived |
| `counts` | one key per status keyword, lower-cased; every keyword is present, zeroes included |
| `acceptance` | the accepted-equivalents ledger's outcome, as the footer reports it |
| `scope` | whether the run was diff-scoped and against what |
| `config` | the run's effective configuration |

`config` is what makes a results directory self-describing: which operators
produced it, which paths were in and out, and what pytest was told — the same
inputs the cache key covers, so two results files that disagree can be told
apart by their inputs rather than by guesswork. `operators` is `null` for a
default run rather than the expanded list, because "the default tier" and "the
operators that version put in it" are different claims. When `--operators` *was*
given, `operators` is the resolved set — sorted names, never the shorthand —
because `deep` and `+boundary` say nothing to a consumer about which operators
produced these results, and a later version would resolve them differently.
`operators_selector` carries the shorthand as typed, which is what answers "why
did this run use these seven?". `--since` is deliberately not
in there: how a run reached a mutant is scope, not configuration, and it is
reported under `scope`.

A diff-scoped run that finds nothing to mutate still writes a summary and still
prints one under `--json` — zeroes, not an empty stream. A consumer that asked
for an object on every run gets one for the pull request that touched only
docs.

## Exit codes

| code | meaning |
|---|---|
| 0 | ran to completion, no findings |
| 1 | ran to completion, at least one `SURVIVED` or `NO_COVERAGE` |
| 2 | did not run: bad layout, red baseline, no tests, unreadable source, unreadable accept file |
| 130 | interrupted (Ctrl-C); the partial `results.jsonl` written so far is valid |

With `--fail-on-unexplained`, exit `1` means something narrower: at least one
finding that is neither killed nor covered by a live entry in the
accepted-equivalents ledger. A stale acceptance — one whose line has changed
since it was written — counts as unexplained, so the flag cannot be quietly
satisfied by an old decision. Without the flag the codes are unchanged, and a
run whose every survivor is accepted still exits `1`. Exit `2` is unaffected
either way: it always means the run could not happen.

See [Equivalent mutants](equivalent-mutants.md#recording-the-decision-the-ledger)
for the ledger itself.

Exit 1 is a *result*, not an error. In CI, `moonbuggy || true` is usually wrong
and `moonbuggy; test $? -le 1` is usually what you meant — unless you want
findings to fail the build, which is the point of the distinction.

`NO_COVERAGE` has counted toward exit 1 since it was introduced, on purpose: it
took its cases from `SURVIVED`, and a status split that quietly turned a red
build green would be the worst possible way to ship one.

## Recipes

Every finding, one per line:

```{code-block} console
$ grep -E '^(SURVIVED|NO_COVERAGE)' .moonbuggy/results.txt
```

Just the survivors, or just the lines nothing reaches:

```{code-block} console
$ grep '^SURVIVED' .moonbuggy/results.txt
$ grep '^NO_COVERAGE' .moonbuggy/results.txt
```

Count survivors per file, worst first:

```{code-block} console
$ jq -r 'select(.status=="SURVIVED") | .file' .moonbuggy/results.jsonl | sort | uniq -c | sort -rn
```

Which operators are producing the findings — useful for deciding whether an
operator is earning its place:

```{code-block} console
$ jq -r 'select(.status=="SURVIVED") | .operator' .moonbuggy/results.jsonl | sort | uniq -c | sort -rn
```

Survivors with their diffs, ready to paste into a review:

```{code-block} console
$ jq -r 'select(.status=="SURVIVED") | "\(.file):\(.line)\n\(.diff)\n"' .moonbuggy/results.jsonl
```

The mutation score, killed over killed-plus-survived (`NO_COVERAGE` is not in
this denominator; moonbuggy's own footer score keeps it, so the two numbers
differ on a project with unreached lines — decide which question you are
asking). Both kill statuses go in the numerator, because both are kills:

```{code-block} text
$ jq -s '[.[] | select(.status | startswith("KILLED"))] | length as $k
    | ([.[] | select(.status=="SURVIVED")] | length) as $s
    | $k / ($k + $s)' .moonbuggy/results.jsonl
```

How much of that numerator is crashes rather than assertions — the number that
says whether the score above means anything:

```{code-block} console
$ jq -r 'select(.status=="KILLED_BY_ERROR") | .id' .moonbuggy/results.jsonl | wc -l
```

Survivors in code you touched on this branch:

```{code-block} console
$ git diff --name-only main... | while read f; do
    jq -r --arg f "$f" 'select(.status=="SURVIVED" and .file==$f) | "\(.file):\(.line) \(.id)"' .moonbuggy/results.jsonl
  done
```

Anything not killed or skipped, which is the set worth a human's time:

```{code-block} console
$ grep -Ev '^(KILLED|KILLED_BY_ERROR|SKIPPED)' .moonbuggy/results.txt
```

## Checking a recipe against the schema

The two artifacts are guaranteed to agree, and that guarantee is worth relying
on. Verified here rather than asserted:

```{doctest}
>>> project = make_project({
...     "calc.py": "def clamp(value, ceiling):\n    if value > ceiling:\n        return ceiling\n    return value\n",
...     "test_calc.py": "from calc import clamp\n\ndef test_low():\n    assert clamp(1, 10) == 1\n\ndef test_high():\n    assert clamp(99, 10) == 10\n",
... })
>>> _ = moonbuggy(cwd=project)
>>> text_lines = (project / ".moonbuggy" / "results.txt").read_text().strip().splitlines()
>>> len(text_lines) == len(records(project))
True
>>> grepped = sum(1 for line in text_lines if line.startswith("SURVIVED"))
>>> grepped == sum(1 for r in records(project) if r["status"] == "SURVIVED")
True
```

Every plaintext line's first token is a real status keyword, so a `grep` on any
of the seven is meaningful:

```{doctest}
>>> {line.split()[0] for line in text_lines} <= {
...     "KILLED", "KILLED_BY_ERROR", "SURVIVED", "NO_COVERAGE", "TIMEOUT",
...     "SUSPICIOUS", "SKIPPED"}
True
```

## Checking that `run` re-measures

The claim that `moonbuggy run` reflects a test you have just written, and that
it leaves the run's artifacts alone, is checked here rather than asserted:

```{doctest}
>>> project = make_project({
...     "lib.py": "def used(value):\n    return value + 1\n\n\ndef never_called(value):\n    return value * 2\n",
...     "test_lib.py": "from lib import used\n\ndef test_used():\n    assert used(1) == 2\n",
... })
>>> _ = moonbuggy(cwd=project)
>>> [r["status"] for r in records(project) if r["id"] == "lib.py:6:constant_int:0"]
['NO_COVERAGE']
>>> _ = (project / "test_never.py").write_text(
...     "from lib import never_called\n\ndef test_never():\n    assert never_called(3) == 6\n")
>>> proc = moonbuggy("run", "lib.py:6:constant_int:0", cwd=project)
>>> proc.stdout.split()[0], proc.returncode
('KILLED', 0)
```

The verdict changed; `results.jsonl` did not, because no run has happened:

```{doctest}
>>> [r["status"] for r in records(project) if r["id"] == "lib.py:6:constant_int:0"]
['NO_COVERAGE']
```

## Checking that `why` explains without measuring

The two claims that matter — that `why` names the selected tests, and that it
reports the cache honestly rather than by re-running — are checked here rather
than asserted. Start with a mutant no test reaches:

```{doctest}
>>> project = make_project({
...     "lib.py": "def used(value):\n    return value + 1\n\n\ndef never_called(value):\n    return value * 2\n",
...     "test_lib.py": "from lib import used\n\ndef test_used():\n    assert used(1) == 2\n",
... })
>>> explained = json.loads(moonbuggy("why", "--json", "lib.py:6:constant_int:0", cwd=project).stdout)
>>> explained["selected"], explained["tests_run"], explained["next_run"]
([], 0, 'no_coverage')
```

No run has happened yet, so there is nothing cached and nothing recorded:

```{doctest}
>>> explained["cache_hit"], explained["last_run_status"]
(False, None)
```

Write the test that reaches the line, and selection picks it up — which is the
question "is my new test being ignored?" answered without running anything:

```{doctest}
>>> _ = (project / "test_never.py").write_text(
...     "from lib import never_called\n\ndef test_never():\n    assert never_called(3) == 6\n")
>>> explained = json.loads(moonbuggy("why", "--json", "lib.py:6:constant_int:0", cwd=project).stdout)
>>> explained["selected"], explained["next_run"]
(['test_never.py::test_never'], 'measure')
```

Now run for real, and the same question gets the other answer — the verdict
that comes back next time will be a replay, and `why` says which one:

```{doctest}
>>> _ = moonbuggy(cwd=project)
>>> explained = json.loads(moonbuggy("why", "--json", "lib.py:6:constant_int:0", cwd=project).stdout)
>>> explained["next_run"], explained["cached_status"]
('cache', 'KILLED')
```

`why` never wrote to that cache and never ran the mutant: `results.jsonl` still
holds exactly what the run left there.

```{doctest}
>>> [r["status"] for r in records(project) if r["id"] == "lib.py:6:constant_int:0"]
['KILLED']
```
