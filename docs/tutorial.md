# Tutorial

**Audience:** you have run moonbuggy once. Now you want to know what to actually
do with a report.

We will take a small, realistic module, run moonbuggy on it, triage every
survivor, add the tests that were missing, and watch the score move. Every
command and every result on this page is executed when the documentation is
built, so nothing here is aspirational.

## The module

A pricing rule of the kind that exists in every commercial codebase: a bulk
discount with a threshold, a cap, and a rounding rule.

```{doctest}
>>> PRICING = '''
... def discount_rate(quantity):
...     """Fraction off, as a float between 0 and 1."""
...     if quantity > 10:
...         return 0.2
...     return 0.0
...
...
... def total(unit_price, quantity):
...     rate = discount_rate(quantity)
...     gross = unit_price * quantity
...     return round(gross * (1 - rate), 2)
... '''
```

And a test suite that looks perfectly reasonable:

```{doctest}
>>> TESTS = '''
... from pricing import discount_rate, total
...
...
... def test_no_discount_for_small_orders():
...     assert discount_rate(3) == 0.0
...
...
... def test_bulk_orders_get_a_discount():
...     assert discount_rate(50) == 0.2
...
...
... def test_total_applies_the_discount():
...     assert total(10.0, 50) == 400.0
... '''
```

Three tests, every line executed, every branch taken. Coverage has nothing to
say about this module.

## The first run

```{doctest}
>>> project = make_project({"pricing.py": PRICING, "test_pricing.py": TESTS})
>>> run = moonbuggy(cwd=project)
>>> sorted({r["status"] for r in records(project)})
['KILLED', 'SURVIVED']
```

Let us see what survived, as diffs rather than line numbers:

```{doctest}
>>> for record in records(project):
...     if record["status"] == "SURVIVED":
...         print(record["diff"])
- if quantity > 10:
+ if quantity >= 10:
- if quantity > 10:
+ if quantity > 11:
- return round(gross * (1 - rate), 2)
+ return round(gross * (1 - rate), 3)
```

Three survivors, out of a module three tests claimed to cover completely.

## Triaging survivor one: the threshold

```{code-block} text
- if quantity > 10:
+ if quantity >= 10:
```

Ask the question the mutant is asking: **at exactly 10 items, does the discount
apply?**

The tests use 3 and 50. Neither is near the boundary, so neither can tell the
two versions apart. This is the single most common real finding in mutation
testing, and it is a genuine gap: if someone "tidies" that `>` into a `>=` next
year, or if the requirement was always `>=` and the code is wrong, no test says
anything.

**Verdict: a real gap.** Write the boundary test.

## Triaging survivor two: the other side of the threshold

```{code-block} text
- if quantity > 10:
+ if quantity > 11:
```

The same line, a different question: **is the threshold 10 or 11?** The tests
use 3 and 50, so moving the boundary by one changes nothing they check.

This is worth seeing as separate from the first survivor. `>` versus `>=` asks
whether 10 itself qualifies; `10` versus `11` asks whether 11 does. A single
test at one side of the boundary answers neither on its own, which is why both
mutants are here.

**Verdict: a real gap**, and the same test session closes both.

## Triaging survivor three: the rounding

```{code-block} text
- return round(gross * (1 - rate), 2)
+ return round(gross * (1 - rate), 3)
```

**Does anything check that money is rounded to two decimal places?**

`total(10.0, 50)` is `400.0` either way — rounding to 2 or 3 places makes no
difference to a number that is already exact. The test cannot distinguish them.

This one deserves a moment's thought, because it could go either way:

- If this is money, rounding to 2dp is a **requirement**, and nothing tests it.
  Real gap.
- If the rounding is incidental tidying, it may not be worth a test.

It is money. Real gap.

## Adding the tests

Each new test is written to kill a specific survivor, which is a much more
focused way to write tests than "add coverage".

```{doctest}
>>> NAIVE_ROUNDING_TEST = '''
...
... def test_totals_are_rounded_to_pennies():
...     assert total(9.99, 3) == 29.97
... '''
```

That test is worth reading carefully before adding it, because it does not
work. `9.99 * 3` is `29.970000000000002` in binary floating point, and rounding
that to two places and to three places give the same answer:

```{doctest}
>>> round(9.99 * 3, 2), round(9.99 * 3, 3)
(29.97, 29.97)
```

So the mutant survives the test written specifically to kill it. This is exactly
the sort of thing mutation testing is for — including, as here, when the useless
test is the one you just wrote to satisfy it.

The value has to be chosen so the two roundings genuinely disagree:

```{doctest}
>>> round(1.005 * 7, 2), round(1.005 * 7, 3)
(7.03, 7.035)
```

Now all three gaps have a test:

```{doctest}
>>> MORE_TESTS = TESTS + '''
...
... def test_exactly_ten_is_not_a_bulk_order():
...     assert discount_rate(10) == 0.0
...
...
... def test_eleven_is_a_bulk_order():
...     assert discount_rate(11) == 0.2
...
...
... def test_totals_are_rounded_to_pennies():
...     assert total(1.005, 7) == 7.03
... '''
>>> _ = (project / "test_pricing.py").write_text(MORE_TESTS)
```

## The second run

```{doctest}
>>> run = moonbuggy(cwd=project)
>>> [r["diff"].replace("\\n", "  |  ") for r in records(project) if r["status"] == "SURVIVED"]
[]
```

No survivors. All three gaps are closed, and the three tests that closed them
are tests a human would recognise as worth having: two pin a boundary from
either side, one pins a money-rounding rule.

## What the score did

```{doctest}
>>> statuses = [r["status"] for r in records(project)]
>>> killed = statuses.count("KILLED")
>>> print(f"{killed}/{len(statuses)} killed")
7/7 killed
```

Seven mutants, seven killed. Before the three new tests, four of seven.

## The habit this builds

Notice what the workflow was not: it was not "get the number up". Every step
was a question about behaviour — *does the boundary matter? is the rounding a
requirement?* — and the answer was sometimes "write a test" and could equally
have been "no, and here is why".

The survivor that is genuinely impossible to kill is common enough to have its
own page: [Equivalent mutants](equivalent-mutants.md).
