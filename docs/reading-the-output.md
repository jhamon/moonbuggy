# Reading the output

**Audience:** agent authors and CLI users. Anything that has to consume
moonbuggy's output programmatically, or read it at 2am.

Every run writes two files into `.moonbuggy/`:

`results.jsonl`
: The canonical record. One JSON object per line, one line per mutant.

`results.txt`
: A plaintext view, **derived from the JSONL** rather than written alongside it,
  so the two cannot drift apart.

The plaintext is also what moonbuggy prints to stdout whenever the output is
piped, redirected, or pinned to the agent format, so what you grep in a file is
exactly what you saw scroll past. At an interactive terminal stdout carries the
human report instead — the same findings, rendered to be read, described under
*The human report* below. Both files are written either way.

## Statuses

Six keywords, and only six. Each line of plaintext begins with one, so
`grep KILLED` needs no knowledge of anything else on this page.

| status | meaning | what to do |
|---|---|---|
| `KILLED` | a selected test failed under the mutation | nothing — your suite caught it |
| `SURVIVED` | every selected test passed under the mutation | **read it**; this is the finding |
| `NO_COVERAGE` | no test executes the line, so none was selected | **read it**; this is the other finding |
| `TIMEOUT` | the mutation made the tests take longer than `--timeout` | usually an infinite loop; treat as killed-ish |
| `SUSPICIOUS` | moonbuggy cannot give a confident answer | investigate the *run*, not the code |
| `SKIPPED` | the line carries `# moonbuggy: skip` | nothing — you asked for this |

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
| 1 | status | one of the six keywords |
| 2 | `file:line` | the location, in the form editors and terminals linkify |
| 3 | category | currently the operator name |
| 4+ | `key=value` | `line`, `nearest_test`, `tests_run`, `id` |

A field with no value prints as `-` rather than as an empty string, so the token
count per line is constant and a naive parser never has to special-case a
missing field.

**The diff is deliberately not on this line.** One line per mutant is what keeps
`grep`, `awk` and `wc -l` usable; a multi-line record would break all three.
Retrieve a diff with `moonbuggy show <id>`.

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
moonbuggy  22 mutants across 5 files

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

4 survived, 1 no_coverage, 1 timeout, 15 killed, 1 skipped in 30.5s -- 15/21 killed, 71%
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
| `id` | string | `file:line:operator:index` — stable across runs for unchanged source |
| `status` | string | one of the six keywords |
| `file` | string | path relative to the project root |
| `line` | integer | 1-based line number |
| `operator` | string | which mutation operator produced it |
| `category` | string | same as `operator` today; a separate taxonomy is deferred until there is survivor data to design one against |
| `nearest_test` | string or null | for survivors, the first covering test — where to start reading; always null for `NO_COVERAGE`, which has none |
| `tests_run` | integer | how many tests were selected for this mutant |
| `duration` | number | seconds spent running this mutant's tests |
| `module_level` | boolean | true when the line runs at import time, which widens selection to the whole suite |
| `suppressed` | boolean | true when the line carries the skip marker |
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

## Exit codes

| code | meaning |
|---|---|
| 0 | ran to completion, no findings |
| 1 | ran to completion, at least one `SURVIVED` or `NO_COVERAGE` |
| 2 | did not run: bad layout, red baseline, no tests, unreadable source, unreadable accept file |

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
asking):

```{code-block} text
$ jq -s '[.[] | select(.status=="KILLED")] | length as $k
    | ([.[] | select(.status=="SURVIVED")] | length) as $s
    | $k / ($k + $s)' .moonbuggy/results.jsonl
```

Survivors in code you touched on this branch:

```{code-block} console
$ git diff --name-only main... | while read f; do
    jq -r --arg f "$f" 'select(.status=="SURVIVED" and .file==$f) | "\(.file):\(.line) \(.id)"' .moonbuggy/results.jsonl
  done
```

Anything not `KILLED` or `SKIPPED`, which is the set worth a human's time:

```{code-block} console
$ grep -Ev '^(KILLED|SKIPPED)' .moonbuggy/results.txt
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
of the five is meaningful:

```{doctest}
>>> {line.split()[0] for line in text_lines} <= {
...     "KILLED", "SURVIVED", "NO_COVERAGE", "TIMEOUT", "SUSPICIOUS", "SKIPPED"}
True
```
