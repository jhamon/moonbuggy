# moonbuggy

Fast, agent-first mutation testing for Python.

This site is published at
[jhamon.github.io/moonbuggy](https://jhamon.github.io/moonbuggy/) on every
merge to `main`.

Your test suite tells you which lines ran. It does not tell you whether anything
would have noticed if those lines were wrong. Mutation testing answers the
second question by breaking your code on purpose and seeing whether your tests
complain.

```{code-block} console
$ moonbuggy
moonbuggy: 84 mutants across 3 files
moonbuggy: running coverage pass...
SURVIVED  app/pricing.py:14 comparison_swap line=14 nearest_test=tests/test_pricing.py::test_discount tests_run=3 id=app/pricing.py:14:comparison_swap:0
KILLED    app/pricing.py:15 constant_int line=15 nearest_test=- tests_run=3 id=app/pricing.py:15:constant_int:0
moonbuggy: KILLED=71  SKIPPED=0  SURVIVED=12  SUSPICIOUS=1  TIMEOUT=0  cached=0  -> .moonbuggy/results.jsonl
```

Each `SURVIVED` line is a change moonbuggy made to your code that no test
noticed. That is a gap in your suite, named down to the line.

Handing this to an agent takes no install and no setup: run
`uv run --with moonbuggy moonbuggy -h`, show it the help, and let it drive —
see the [Quickstart](quickstart.md).

## Start here

If you have never used mutation testing, read the two pages in order. If you
have, the quickstart alone is enough.

```{toctree}
:maxdepth: 1
:caption: Getting started

quickstart
what-is-mutation-testing
tutorial
```

```{toctree}
:maxdepth: 1
:caption: Using it

reading-the-output
equivalent-mutants
making-runs-fast
troubleshooting
```

```{toctree}
:maxdepth: 1
:caption: Contributing

architecture
writing-an-operator
api/index
releasing
```

```{toctree}
:maxdepth: 1
:caption: Results

benchmark-results
differential
oss-findings
```

## What makes this one different

**It is built for a reader that greps.** Output is one line per mutant, leading
with a status keyword, with `key=value` tokens after it. `grep SURVIVED` works
with no knowledge of the schema, and the JSONL alongside it is the same data for
anything that wants to parse rather than scan. See
[Reading the output](reading-the-output.md).

**It refuses rather than guesses.** A wrong status is worse than no status: a
false `SURVIVED` looks exactly like a real finding and costs you an
investigation that ends nowhere. So a flaky test makes its mutants
`SUSPICIOUS` rather than confidently anything, an already-failing suite is
refused outright, and a mutation that cannot be applied is an error rather than
a survivor.

**It only runs the tests that could possibly notice.** A mutation on line 14 can
only be caught by a test that executes line 14. One instrumented run of your
suite builds that map, and each mutant then runs against a handful of tests
instead of all of them. See [Making runs fast](making-runs-fast.md).
