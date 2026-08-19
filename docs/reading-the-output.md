# Reading the output

**Audience:** agent authors and CLI users. Anything that has to consume
moonbuggy's output programmatically, or read it at 2am.

Every run writes two files into `.moonbuggy/`:

`results.jsonl`
: The canonical record. One JSON object per line, one line per mutant.

`results.txt`
: A plaintext view, **derived from the JSONL** rather than written alongside it,
  so the two cannot drift apart. The same content is printed to stdout.

The plaintext is also the format printed to your terminal, so what you grep in a
file is exactly what you saw scroll past.

## Statuses

Five keywords, and only five. Each line of plaintext begins with one, so
`grep KILLED` needs no knowledge of anything else on this page.

| status | meaning | what to do |
|---|---|---|
| `KILLED` | a selected test failed under the mutation | nothing — your suite caught it |
| `SURVIVED` | every selected test passed under the mutation | **read it**; this is the finding |
| `TIMEOUT` | the mutation made the tests take longer than `--timeout` | usually an infinite loop; treat as killed-ish |
| `SUSPICIOUS` | moonbuggy cannot give a confident answer | investigate the *run*, not the code |
| `SKIPPED` | the line carries `# moonbuggy: skip` | nothing — you asked for this |

Two of these are worth expanding, because they are where a mutation tool
usually lies to you.

**`SURVIVED` with `tests_run=0`** means no test executes that line at all. It is
still a survivor rather than a skip, because an untested line is a finding —
just a different one from "tested but not checked".

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
| 1 | status | one of the five keywords |
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

This is the real output of `tests/fixtures/sample_project`, run with
`--report human` to force it regardless of terminal detection:

```{code-block} text
moonbuggy: 22 mutants across 5 files
moonbuggy: running coverage pass...

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

sample/inventory.py:15
  SURVIVED  constant_int
    - return 0
    + return 1
             ^
  no test runs this line at all

sample/loops.py:10
  SURVIVED  comparison_swap
    - while n > 0:
    + while n >= 0:
              ^^
  2 tests run this line; first is
  tests/test_loops.py::test_countdown_of_zero_is_zero

Problems with the run

sample/loops.py:12
  TIMEOUT  arithmetic_swap  (timed out after 30s)
    - n -= 1
    + n += 1
        ^^

5 survived, 1 timeout, 15 killed, 1 skipped in 30.2s -- 15/21 killed, 71%
Full records: .moonbuggy/results.jsonl
exit 1 -- survivors
```

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
| `status` | string | one of the five keywords |
| `file` | string | path relative to the project root |
| `line` | integer | 1-based line number |
| `operator` | string | which mutation operator produced it |
| `category` | string | same as `operator` today; a separate taxonomy is deferred until there is survivor data to design one against |
| `nearest_test` | string or null | for survivors, the first covering test — where to start reading |
| `tests_run` | integer | how many tests were selected for this mutant |
| `duration` | number | seconds spent running this mutant's tests |
| `module_level` | boolean | true when the line runs at import time, which widens selection to the whole suite |
| `suppressed` | boolean | true when the line carries the skip marker |
| `diff` | string | two lines: `- original` then `+ mutated` |

The `id` is worth understanding because it is the join key for anything that
tracks findings over time. It ends in an occurrence index because one line can
host several mutants — a line with two `+` operators produces two from the same
operator, and they need separate identities.

## Exit codes

| code | meaning |
|---|---|
| 0 | ran to completion, no survivors |
| 1 | ran to completion, at least one survivor |
| 2 | did not run: bad layout, red baseline, no tests, unreadable source |

Exit 1 is a *result*, not an error. In CI, `moonbuggy || true` is usually wrong
and `moonbuggy; test $? -le 1` is usually what you meant — unless you want
survivors to fail the build, which is the point of the distinction.

## Recipes

Every survivor, one per line:

```{code-block} console
$ grep '^SURVIVED' .moonbuggy/results.txt
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

The mutation score, killed over killed-plus-survived:

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
...     "KILLED", "SURVIVED", "TIMEOUT", "SUSPICIOUS", "SKIPPED"}
True
```
