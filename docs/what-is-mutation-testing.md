# What mutation testing is

**Audience:** you know pytest. You have never used mutation testing, and you are
reasonably suspicious of tools that promise to grade your tests.

## Coverage measures the wrong thing

Here is a function and a test. The test passes. Coverage is 100%.

```{doctest}
>>> def shipping_cost(weight_kg):
...     if weight_kg > 20:
...         return 15.0
...     return 5.0
>>> def test_shipping():
...     assert shipping_cost(1) == 5.0
...     assert shipping_cost(50) == 15.0
>>> test_shipping()
```

Every line ran. Both branches ran. By any coverage report, this function is
completely tested.

Now change one character — `> 20` becomes `>= 20`:

```{doctest}
>>> def shipping_cost(weight_kg):
...     if weight_kg >= 20:   # the only change
...         return 15.0
...     return 5.0
>>> test_shipping()
```

The test still passes. It did not notice.

The bug this hides is real: a 20kg parcel now costs £15 instead of £5, and
nothing in the suite says which is correct. Coverage cannot see this, because
coverage asks *"did this line run?"* and the answer is yes either way. The
question that matters is *"would anything have complained if this line were
wrong?"*, and there is only one way to find out.

## So: break it on purpose

Mutation testing makes small, plausible changes to your source — one at a time —
and runs your tests against each one.

- If a test fails, the mutant is **killed**. Your suite noticed. Good.
- If every test passes, the mutant **survived**. Your suite did not notice, and
  that is a gap with a file, a line, and a description.

The changes are called *mutants*, and they are deliberately the kind of mistake
people actually make: an off-by-one in a comparison, a `+` where a `-` belongs,
an `and` that should be an `or`, a boolean flipped. moonbuggy's set is small on
purpose; [Writing an operator](writing-an-operator.md) covers adding to it.

The proportion killed is your **mutation score**. It is a more honest number
than coverage because it cannot be raised by executing code without checking
anything.

## Why this is not just a slower coverage tool

Consider a test suite with this shape, which is extremely common:

```{code-block} python
def test_it_runs():
    result = process(order)
    assert result is not None
```

100% coverage of `process`. Mutation score near zero, because almost any change
to `process` still returns something that is not None. The test executes the
code and checks essentially nothing, and mutation testing is the tool that says
so out loud.

## The catch, stated up front

**Some survivors cannot be killed.** A mutation that produces code with
identical behaviour — swapping `<` for `!=` in a loop bound that can only be
reached one way — will survive every possible test, because there is nothing to
catch. These are *equivalent mutants*, detecting them in general is
undecidable, and pretending otherwise is how mutation tools waste people's
afternoons. [Equivalent mutants](equivalent-mutants.md) is about recognising
them and suppressing them honestly.

**It is slower than your test suite.** It has to be: it runs tests once per
mutant. moonbuggy's answer is to run only the tests that execute the mutated
line, which is usually a handful rather than all of them, and to keep the
process warm between mutants. See [Making runs fast](making-runs-fast.md).

**A low score is not automatically bad.** Code that is genuinely
untested-by-design — a debug helper, a `__repr__` — will produce survivors that
nobody should write tests for. The output is evidence, not a grade.

## What to do with it

Do not chase the score. Read the survivors and ask, for each one: *if this
change shipped, what would break, and would anyone find out?*

- If the answer is "a customer would be charged the wrong amount and nobody
  would know" — write the test.
- If the answer is "nothing, the two versions are the same program" — it is an
  equivalent mutant, suppress it with a note.
- If the answer is "nothing anyone cares about" — leave it.

Next: the [Quickstart](quickstart.md) if you want it running now, or the
[Tutorial](tutorial.md) for a realistic module walked end to end.
