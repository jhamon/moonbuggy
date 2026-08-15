---
audience: You have a Python project with a pytest suite and have never used mutation testing.
---

# Quickstart

**Audience:** you have a Python project with a pytest suite. You have never used
mutation testing and do not need to understand it yet.

**Goal:** install moonbuggy, run it, read one survivor, and fix it. Ten minutes.

## 1. Install

```{code-block} console
$ pip install moonbuggy
```

moonbuggy needs Python 3.12 or later and a project whose tests run under
`pytest`. It has no configuration file and does not want one.

## 2. Run it

From your project root — the directory where you would run `pytest`:

```{code-block} console
$ moonbuggy
```

No flags. moonbuggy finds your source directory, finds your tests, runs them
once to see which tests execute which lines, and then starts breaking things.

The first thing it does is check your suite is green. If it is not, it stops and
says so, because mutation results against a failing suite are meaningless — see
[Troubleshooting](troubleshooting.md).

## 3. Read the output

Every line is one mutation, and the first word is the verdict:

```{code-block} text
KILLED    app/pricing.py:15 constant_int line=15 nearest_test=- tests_run=3 id=app/pricing.py:15:constant_int:0
SURVIVED  app/pricing.py:14 comparison_swap line=14 nearest_test=tests/test_pricing.py::test_discount tests_run=3 id=app/pricing.py:14:comparison_swap:0
```

`KILLED` is good news. moonbuggy changed line 15, a test failed, your suite
caught it. Nothing to do.

`SURVIVED` is the finding. moonbuggy changed line 14, ran the three tests that
execute line 14, and **all three still passed**. Your suite does not notice that
change.

Only survivors are worth your attention:

```{code-block} console
$ grep SURVIVED .moonbuggy/results.txt
```

## 4. Look at one survivor

The line tells you where, but not what changed. Ask:

```{code-block} console
$ moonbuggy show app/pricing.py:14:comparison_swap:0
id           app/pricing.py:14:comparison_swap:0
status       SURVIVED
location     app/pricing.py:14
operator     comparison_swap
nearest_test tests/test_pricing.py::test_discount
tests_run    3
diff
  - if quantity > 10:
  + if quantity >= 10:
```

Read that as a question about your tests: **if the threshold were `>=` instead
of `>`, would any test fail?** moonbuggy has already answered — no.

So nothing in your suite pins down what happens at exactly 10 items. If the
boundary is wrong, or if someone changes it next year, the tests stay green.

## 5. Fix it

Write the test that would have caught it. The boundary case is the missing one:

```{code-block} python
def test_exactly_ten_does_not_qualify():
    assert discount(quantity=10) == 0
```

Run moonbuggy again:

```{code-block} console
$ moonbuggy
moonbuggy: KILLED=72  SKIPPED=0  SURVIVED=11  SUSPICIOUS=1  TIMEOUT=0  cached=71  -> .moonbuggy/results.jsonl
```

That mutant is now `KILLED`, and `cached=71` means the mutants in files you did
not touch were not re-run. The second run is much faster than the first.

## What you just did

You found a gap in a test suite that had full coverage of that line. Coverage
said the line ran. It could not say whether anything checked the result.

That is the whole idea, and [What mutation testing is](what-is-mutation-testing.md)
is the ten-minute version of why it works.

## Next

- Not every survivor is a real gap. Some are impossible to kill — see
  [Equivalent mutants](equivalent-mutants.md).
- The full output format, including the JSONL schema and `jq` recipes, is in
  [Reading the output](reading-the-output.md).
- A realistic module walked end to end is in the [Tutorial](tutorial.md).
