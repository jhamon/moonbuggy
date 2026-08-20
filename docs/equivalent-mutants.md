# Equivalent mutants

**Audience:** you have a survivor you cannot kill, and you are starting to
wonder whether the tool is wrong.

Sometimes it is. More often the mutant is *equivalent*: the mutated program and
the original are the same program, so no test can tell them apart. There is
nothing to catch, and any test you write to "fix" it will be a test that asserts
something arbitrary.

## Why this cannot be automated away

Deciding whether two programs behave identically for all inputs is equivalent
to the halting problem. It is not that nobody has written the check yet — the
check cannot exist. Every mutation testing tool has this limitation, and a tool
that claims otherwise is either guessing or restricting itself to mutations too
weak to be worth making.

So the honest position is: moonbuggy will produce equivalent mutants, it cannot
tell you which ones they are, and the judgement is yours. What it can do is give
you enough information to make that judgement quickly, which is what the diff in
`moonbuggy show` is for — and, when you would rather test the hypothesis than
reason about it, `moonbuggy run <id>` re-measures that one mutant against the
test you just wrote.

## Recognising one

Three shapes cover most of them.

**The unreachable boundary.** The mutation changes a condition that cannot be
reached in the changed state:

```{code-block} python
def take(items, count):
    if count <= 0:
        return []
    return items[:count]           # count > 0 guaranteed here
```

Mutating `items[:count]` in a way that only matters for `count == 0` cannot be
observed, because the guard above has already returned.

**The redundant clamp.** The mutation changes something a later step undoes:

```{code-block} python
def percentage(part, whole):
    if whole == 0:
        return 0.0
    return round(part / whole * 100, 2)
```

Mutating `round(..., 2)` to `round(..., 3)` is only observable for values whose
third decimal place is non-zero. If every caller passes values that cannot
produce one, the two versions are equivalent *in this program* — though that is
a fragile property, and the tutorial shows one that looked equivalent and was
not.

**The initialisation that is immediately overwritten.**

```{code-block} python
def first_match(items, predicate):
    found = None                   # mutating this constant changes nothing
    for item in items:
        if predicate(item):
            found = item
            break
    return found
```

If the loop always assigns before any read, the initial value is unobservable —
except when the loop body never runs, which is exactly the case worth checking
before you dismiss it.

## Deleted statements

`statement_deletion` — the `deep` tier — produces more equivalent mutants than
any other operator, because "this line can be removed" is true of more lines
than people expect. The operator already refuses to generate the ones it can
*prove* are equivalent: docstrings, `pass`, `...`, `global`/`nonlocal`, a bare
name or literal on its own line, and a local binding with a pure right-hand
side that nothing in the enclosing function reads again. Anything it cannot
prove, it generates — the alternative is declining to mutate lines that hide
real gaps, and a missed finding costs more than a survivor you dismiss.

So expect survivors of this shape, and expect some of them to be genuine:

```{code-block} python
def load(path):
    path = Path(path)
    cache.setdefault(path, {})     # deleting this may be equivalent
    return _read(path)
```

Whether that `setdefault` matters depends on what `_read` does with a missing
key, which is exactly the judgement no analysis can make for you. Ask the
question in the next section, and when the answer is "nothing observable",
record it in the ledger rather than deleting the line — the ledger is what
stops the next run asking you again.

## Swapped arguments

`argument_swap` — also the `deep` tier — has one source of equivalents it
cannot close, and it is worth knowing about before you read its survivors.
Swapping two adjacent positional arguments is only observable when the two mean
different things, and moonbuggy has no type inference with which to ask.

```{code-block} python
total = add(subtotal, shipping)    # swapping these changes nothing
```

The operator does skip the cases it can settle for free: a starred position,
and two arguments identical as source (`f(x, x)`, `f(0, 0)`). Anything past
that is a judgement about the callee. Apply the test below, and when the answer
is "the two arguments are interchangeable", put it in the ledger — that is the
case `moonbuggy accept` exists for, and it is the main reason this operator is
opt-in rather than part of every run.

## The test before you dismiss it

Before calling a survivor equivalent, answer this concretely:

> **Name an input for which the two versions produce different output.**

If you can, it is not equivalent — it is a real gap, and you have just written
the test case. If you genuinely cannot, and you have thought about the empty
collection, the zero, the boundary and the negative, it is probably equivalent.

That question is doing real work. Most survivors that *feel* equivalent turn out
to have an input that separates them, and it is almost always the boundary
nobody tested.

## Suppressing one honestly

When you are confident, suppress it at the line with a marker and a reason:

```{code-block} python
def first_match(items, predicate):
    found = None  # moonbuggy: skip -- overwritten before any read; None only
                  # survives for an empty `items`, which test_empty covers
    ...
```

The mutant is then reported `SKIPPED` rather than `SURVIVED`, and drops out of
the set that needs attention.

```{doctest}
>>> project = make_project({
...     "lib.py": "def scale(n):\n    factor = 2  # moonbuggy: skip\n    return n * factor\n",
...     "test_lib.py": "from lib import scale\n\ndef test_scale():\n    assert scale(3) == 6\n",
... })
>>> _ = moonbuggy(cwd=project)
>>> [(r["line"], r["status"]) for r in records(project) if r["suppressed"]]
[(2, 'SKIPPED')]
```

Two rules for suppression, and they are what separate it from lying:

**Always write the reason.** The marker without a justification is a claim
nobody can check and nobody will revisit. `# moonbuggy: skip` alone will be read
next year as "someone was in a hurry".

**Suppress the line, never the file.** A blanket exclusion hides the equivalent
mutant and every real gap that arrives on that line later. moonbuggy has no
file-level suppression for this reason.

## Logging calls

One family of unkillable mutant is common enough, and recognisable enough, that
moonbuggy handles it for you. Nothing asserts on the contents of a debug line,
so a mutation inside one survives every suite that will ever be written against
that code:

```{code-block} python
logger.debug("retrying in %ds", delay * 2)
```

`delay * 2` becomes `delay / 2`, the log line reads differently, and no test
notices — nor should one. Left alone these dominate a survivor list: in the
session that prompted this feature, two thirds of the survivors in a retry
region were arithmetic inside `logger.debug(...)` arguments.

So a mutation inside a logging call's arguments is **tagged** `logging_call` in
`results.jsonl` and, by default, reported `SKIPPED`:

```{doctest}
>>> project = make_project({
...     "lib.py": (
...         "import logging\n\n"
...         "logger = logging.getLogger(__name__)\n\n"
...         "def charge(n):\n"
...         "    if n > 10:\n"
...         "        logger.info('big charge: %d', n * 2)\n"
...         "    return n\n"
...     ),
...     "test_lib.py": (
...         "from lib import charge\n\n"
...         "def test_charge():\n    assert charge(3) == 3\n"
...     ),
... })
>>> _ = moonbuggy(cwd=project)
>>> sorted({r["status"] for r in records(project) if r["logging_call"]})
['SKIPPED']
```

**The guard around a log call is still a finding.** This is the line the
policy is drawn on, and it is drawn narrowly on purpose. In the module above,
`n > 10` decides whether anything is logged at all; mutating it changes which
branch runs, which is exactly what a mutation tester exists to find. Only the
*argument expressions* of the call itself are suppressed:

```{doctest}
>>> [r["operator"] for r in records(project) if not r["logging_call"]]
['comparison_swap', 'condition_negation', 'constant_int']
```

The same rule keeps a real call nested inside a log line honest:
`logger.info("%s", compute(n + 1))` mutates `n + 1` as usual, because the
nearest enclosing call is `compute`, and `compute` really runs.

**Recognising a logger is a heuristic.** moonbuggy looks for a level method —
`debug`, `info`, `warning`, `error`, `critical`, `exception`, `log` — called on
something named like a logger: `log`, `logger`, `logging`, `LOG`, `LOGGER`, the
underscore-prefixed spellings, and any attribute chain ending in one, so
`self.logger.debug(...)` and `mypkg.util.LOG.info(...)` both count. If your
project wraps its logger under another name, add it:

```{code-block} console
$ moonbuggy --logger-name audit --logger-name telemetry
```

Names are *added* to the built-in ones, never replacing them. The same flag is
accepted by `moonbuggy run <id>` and `moonbuggy why <id>`, and passing it to
one and not another is how those three commands end up disagreeing about the
same line.

**To see them anyway**, pass `--include-logging-mutants`. They run like any
other mutant and keep the `logging_call` tag, so a project that asserts on log
output — with `caplog`, or against a structured-logging sink — gets the
findings and can still filter on them:

```{code-block} console
$ moonbuggy --include-logging-mutants
$ jq 'select(.logging_call and .status == "SURVIVED")' .moonbuggy/results.jsonl
```

**This does not flatter your score.** A suppressed logging mutant is `SKIPPED`,
and `SKIPPED` leaves the denominator exactly the way a `# moonbuggy: skip` line
does — the kill rate is unchanged, and the human report says how many mutants
were suppressed and how to see them. What changes is the length of the list you
have to read.

## Recording the decision: the ledger

Suppression is for a line you own and want marked in the source. The other
half of the problem is the survivor you have reviewed, decided is equivalent,
and do not want to review again next week — and that nobody else on the team
should have to review either. That decision goes in a ledger:

```{code-block} console
$ moonbuggy accept 'shipping.py:5:comparison_swap:0' \
    --reason "equivalent: both branches return the same value for every reachable input"
moonbuggy: accepted shipping.py:5:comparison_swap:0 in .moonbuggy/accepted.toml.
It still runs and is still reported; it is counted separately.
```

The ledger is `.moonbuggy/accepted.toml` by default (`--accept-file` moves it),
and it is **meant to be committed**. It records human decisions about your
code, not output from a run, which is why it does not move when you point
`--output-dir` somewhere else.

:::{admonition} `.moonbuggy/` is usually gitignored
:class: warning

Most projects ignore the whole directory, because everything else in it is run
output. A ledger nobody can commit vanishes on the next clone, so exclude the
*contents* instead and let the ledger back in:

```{code-block} text
.moonbuggy/*
!.moonbuggy/accepted.toml
```

`git` will not re-include a file below an excluded directory, which is why
`.moonbuggy/` followed by a negation does not work. `moonbuggy accept` checks
for this the first time it writes the file and warns you.
:::

Listing and un-accepting:

```{code-block} console
$ moonbuggy accept --list
shipping.py:5:comparison_swap:0  operator=comparison_swap  accepted_at=2026-08-20  reason=equivalent: both branches ...
$ moonbuggy accept --remove 'shipping.py:5:comparison_swap:0'
```

### An acceptance is never a way to hide something

Accepted mutants still run, and are still reported. They move out of the punch
list into their own section, and the summary counts them separately:

```{code-block} text
73 killed, 3 survived in 12.4s -- 73/76 killed, 96%
3 accepted as equivalent, 0 unexplained -- ledger .moonbuggy/accepted.toml
```

In `results.jsonl` each one carries `"accepted": true` and the reason, so
`jq 'select(.status=="SURVIVED" and (.accepted|not))'` is the list of survivors
that still need a human.

### Drift: an acceptance expires when the line changes

Each entry stores a fingerprint of the mutation it was made for — the operator,
the original line, and the mutated line. Edit that line and the fingerprint no
longer matches: the acceptance is **stale**, and the mutant is reported as
unexplained again, with a message saying so. Silently honouring it is exactly
how a real regression gets in behind a decision somebody made about different
code last year.

The fingerprint covers the mutated line rather than the whole module on
purpose. A module-wide hash is stricter, and it expires every acceptance in a
file for a change to a comment — after which re-accepting fifty entries in bulk
is a ritual rather than a review. The cost is worth naming: a change *elsewhere*
in the module can undermine an equivalence argument without expiring the entry.
That is what the reason field is for, and why writing one is mandatory.

### Id stability: an insertion above does not lose your work

Mutant ids are `path:line:operator:index`, so adding a line at the top of a file
shifts every id below it. An acceptance keyed on the id alone would evaporate;
one keyed on content alone could not tell two identical lines apart. moonbuggy
keys on both: the id first, and then, if the id no longer resolves, exactly one
mutant in the same file with the same fingerprint. Two equally good candidates
are refused rather than guessed — equivalence is a judgement about a line in its
context, and moonbuggy will not apply your decision to a line you did not make
it about.

### The CI gate

```{code-block} console
$ moonbuggy --fail-on-unexplained
```

Exits `1` only for findings that are neither killed nor accepted. Without the
flag the exit code is exactly what it has always been — a run whose every
survivor is accepted still exits `1` — so adding a ledger can never silently
turn a red build green.

## When you are not sure

Leave it. A survivor you have looked at and cannot decide about is not costing
you anything except a line of output, and suppressing it converts a small
ongoing question into a permanent silent assumption. Suppression is for
certainty, not for tidiness.

## What this means for the score

A codebase with genuinely equivalent mutants cannot reach 100%, and chasing the
last few points will make your test suite worse — you will end up asserting
implementation details to kill mutants that do not matter.

Read the survivors. Ignore the percentage.
