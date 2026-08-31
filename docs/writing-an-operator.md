# Writing an operator

**Audience:** contributors adding a new kind of mutation.

Adding an operator means adding one file. No edit to the engine's traversal, no
import list to update, no registration call to remember. If you find yourself
changing anything outside `src/moonbuggy/operators/`, something has gone wrong.

## The seam

An operator is any class decorated with `@register` that provides:

`name`
: A stable string identifier. It appears in every mutant id, in the JSONL
  `operator` field, and in `--operators`. Changing it invalidates cache entries,
  so choose it once.

`mutations(node)` **or** `mutations_in_context(node, context)`
: One mutation method, in one of two forms. `mutations` takes one AST node and
  yields zero or more replacement nodes. `mutations_in_context` takes the node
  plus a `Context` saying where it sits, and is what you write when the
  decision depends on the surroundings rather than on the node alone — see
  "Asking where you are" below. Either way it is called for every node in the
  tree, so the first thing it does is decide whether this node is its business.

  These are alternatives, not a base and an extension: an operator provides one
  or the other, never both. The type is
  `Operator = NodeOperator | ContextualOperator` in
  `src/moonbuggy/operators/__init__.py`, and the contextual half is not a
  special case — three of the eleven built-ins are contextual:
  `condition_negation`, `statement_deletion` and `default_arg`.

The engine asks the registry for `all_operators()` and never learns their names.
Operators are discovered by importing every module in the package, which is why
adding a file is enough.

## Saying what it costs

Three optional class attributes decide how your operator appears in
`moonbuggy operators` and which tier selects it. All three are plain class
attributes with sensible defaults, so an operator that says nothing is a cheap
`default`-tier one — which every built-in outside the `deep` tier is.

`tier`
: `"default"` (the default) or `"deep"`. `default` is what a bare `moonbuggy`
  runs: cheap in wall clock, high signal. `deep` is for an operator that is
  expensive to run or noisy to read, and is opted into with `--operators deep`
  or `--operators +your_operator`. Put it in `deep` if it multiplies the mutant
  count, if a fair share of its mutants will time out, or if you expect more
  survivors-that-are-noise than survivors-that-are-findings. Put it there too
  when you simply do not know yet — see "Deciding whether it earns its place"
  below, and `src/moonbuggy/operators/function.py`, whose three operators are
  all in `deep` for exactly that reason.
  `statement_deletion` is the worked example: roughly one extra mutant per
  statement, and a real equivalent-mutant rate even after its heuristic has
  thrown the provable ones away. Read
  `src/moonbuggy/operators/deletion.py` before writing a `deep` operator of
  your own — its module docstring is where the "prove inert, not impactful"
  argument is written down.

`cost`
: `"low"` (the default), `"medium"` or `"high"`. A rough ordering for the
  listing, not a measurement — the real cost depends on the code being mutated.

`description`
: One line about what it mutates, for the listing. Omit it and the first line
  of the class docstring is used, which is the line you were going to write
  anyway.

The tier lives on the operator rather than in a table somewhere central, and
that is deliberate: a table would mean adding an operator required editing two
files, and "adding an operator is adding a file" is the one property this
package is built around.

The three tier *selector* words — `default`, `deep`, `all` — are reserved.
`@register` raises if an operator claims one as its `name`, because an operator
called `deep` would silently change what every existing `--operators deep`
means. It also raises for a `tier` or `cost` this version does not know, so a
typo is a loud import error rather than an operator quietly filed under
nothing.

One further thing an operator may ask for, optional. It does not change
registration, discovery, or `all_operators()`.

`yield target, replacement`
: Instead of `yield replacement`, when the node you want to *edit* is not the
  node you had to *see*. The engine rewrites `target` instead of the node it
  handed you.

## Asking where you are

`Context` answers three questions, and they are the ones that come up:

`context.parent` and `context.field`
: The enclosing node and the field of it this node occupies. "Am I in test
  position?" is `context.field == "test"` and `isinstance(context.parent,
  ast.If)`. Without it you see a bare `ast.Name` and cannot tell the test of an
  `if` from the right-hand side of an assignment — and negating every name in a
  module is absurd where negating the ones in test position is exactly right.

`context.index`
: Position within `field` when that field is a list. "Where am I in the
  enclosing body?" is this, plus `parent`.

`context.nearest(ast.Call)` and `context.ancestors`
: The innermost enclosing node of some type, and the whole chain outermost
  first. The chain is built on demand rather than stored, so asking for it is
  the only thing that costs anything.

The built-in `condition_negation` is the whole of the pattern:

```{code-block} python
def mutations_in_context(self, node, context):
    if not isinstance(node, ast.expr) or not _is_condition(context):
        return
    yield ast.UnaryOp(op=ast.Not(), operand=node)
```

## Targeting a different node

`_splice` rewrites one source line by column offset, and returns nothing for a
node whose `end_lineno` differs from its `lineno`. That structurally excludes
every compound statement: an operator handed an `ast.If` can never yield a
replacement *for the `If`*, because an `if` and its body do not fit on one line.

A pair is the way past that. Yield the child you actually want rewritten:

```{code-block} python
def mutations(self, node):
    if isinstance(node, ast.If):
        # The `If` spans its whole body; its test does not.
        yield node.test, ast.Constant(value=False)
```

The same mechanism reaches nodes the walk cannot report on their own.
`ast.arguments` and `ast.comprehension` carry no `lineno` at all — nothing
downstream can place a mutant on them — but their children do, and a pair (or
context, from the child's side) is how an operator gets at a default argument
or a comprehension guard.

Line, scope and suppression all follow the target, not the node you were
handed: the mutant is reported on the line it actually changed.

## A complete example

Say you want to catch tests that do not check what happens when a container is
empty. A plausible mistake is `len(x) == 0` being written as `len(x) != 0`, so
mutate `==` and `!=` where one side is a call to `len`.

`src/moonbuggy/operators/emptiness.py`:

```{code-block} python
"""Emptiness-check inversion.

`len(x) == 0` -> `len(x) != 0`, and the reverse. Narrower than the general
comparison operator on purpose: it fires only where one side is a `len()` call
and the other is the literal 0, which is the shape that reads as an emptiness
check rather than as arithmetic.
"""

import ast

from . import register, replace_operator

SWAPS = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}


@register
class EmptinessSwap:
    name = "emptiness_swap"

    def mutations(self, node):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            return
        replacement = SWAPS.get(type(node.ops[0]))
        if replacement is None:
            return
        if not _is_length_against_zero(node):
            return
        yield replace_operator(node, ops=[replacement()])


def _is_length_against_zero(node):
    """Whether this compares `len(...)` with the literal 0, either way round."""
    left, right = node.left, node.comparators[0]
    return (_is_len_call(left) and _is_zero(right)) or (
        _is_len_call(right) and _is_zero(left)
    )


def _is_len_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
    )


def _is_zero(node):
    # `isinstance(True, int)` is True in Python, so booleans have to be excluded
    # explicitly or `len(x) == False` becomes a site.
    return (
        isinstance(node, ast.Constant)
        and not isinstance(node.value, bool)
        and node.value == 0
    )
```

That is the whole operator. It is now discovered, applied, listed by
`moonbuggy operators`, and selectable with `--operators emptiness_swap` or
`--operators +emptiness_swap`. It declares no `tier`, so it is a `default`-tier
operator — which is right for this one: it fires on a narrow shape and produces
at most one mutant per site.

## Four rules

**Never mutate the node you are given.** The tree is shared, and an operator
that writes to it corrupts every later operator's view. `replace_operator`
returns a shallow copy with fields replaced and is the supported way to obey
this — it also avoids a deep copy, which used to make one deeply-nested
expression cost quadratic time.

**Yield one node per mutation site, not one per node.** A chained comparison
`a < b < c` is a single `Compare` node with two operators, and each is a
separate site. Mutating both at once is a different and weaker test of the same
line. The built-in `comparison_swap` shows the pattern.

**Produce something that parses.** The engine splices your unparsed node back
into the original line by column offset, so a replacement of a different shape
than the original can produce invalid source. A property test asserts that every
mutant of every generated module compiles, and it will find this.

**Be narrow enough to be a real mistake.** The value of a mutation is that a
human could plausibly have written it. `x + 1` → `x - 1` is a plausible typo;
`x + 1` → `x ** 1` is not, and a survivor from it teaches nobody anything.
Precision here is what keeps the report worth reading.

The narrowing is often about a node type rather than a whole operator.
`condition_negation` inverts the test of an `if` and does not touch a `while`,
for two reasons that are worth reading together. A negated loop test that ran
under the test now skips, which nearly any assertion catches — no signal. A
negated loop test that did *not* run, which is what an empty-input case looks
like, now never terminates: `while queue:` becomes `while not queue:` and the
mutant burns the entire `--timeout` instead of failing fast. `TIMEOUT` is a
real status, so this is not wrong; it is just expensive and quiet, and paid on
every loop in the file. An operator's cost is wall-clock as well as noise.

That exclusion is also the shape of thing the `deep` tier exists for. A
while-negating operator would be a plausible `deep` member — expensive, opted
into deliberately, useful on a suite you specifically suspect of not testing
its loops. Nothing here implements one; the tier is the place it would go, and
`statement_deletion` plus the three function-interface operators are what
already live there.

## Testing it

Three levels, and the first two are quick.

**Does it fire where it should, and only there?** A direct unit test over source
strings. `tests/test_operators.py` is the model:

```{code-block} python
def test_emptiness_swap_fires_on_a_length_check():
    mutants = generate_mutants(
        "def is_empty(xs):\n    return len(xs) == 0\n", module="lib.py"
    )
    diffs = [m.diff for m in mutants if m.operator == "emptiness_swap"]
    assert diffs == ["- return len(xs) == 0\n+ return len(xs) != 0"]


def test_emptiness_swap_ignores_ordinary_comparisons():
    mutants = generate_mutants(
        "def check(a, b):\n    return a == b\n", module="lib.py"
    )
    assert not [m for m in mutants if m.operator == "emptiness_swap"]
```

The second test is the one that matters. An operator that fires too widely
produces noise, and noise is what stops people reading the report.

**Does it hold the properties?** `make check-properties` runs six invariants
over generated modules — every mutant compiles, no mutation edits a string or a
comment, splicing round-trips byte for byte, ids stay unique, every mutant
reports the line it changed, scope classification is sound — plus a seventh
test that is not an invariant but a check that the generator actually reaches
every feature those six depend on.

A new operator is covered by all six automatically, and this is where a mistake
in splicing shows up. "Automatically" is load-bearing and was not always true:
the suite asks for the `all` tier by name (`EVERY_OPERATOR` in
`tests/test_properties.py`), because `generate_mutants`'s default is the
`default` tier, and for a while every property silently exempted the whole
`deep` tier. If you add a call to `generate_mutants` in that file, go through
`_mutants` rather than calling it directly.

If your operator legitimately cannot hold one of the six, say so in that
property's docstring with the reasoning, the way the suite handles statement
deletion — do not weaken the property for everyone. A narrowing that is written
down is a design decision; a narrowing that is not is a hole.

**Does it agree with the slow, obvious implementation?** `make check-oracle`
runs every fixture mutant through the naive runner — copy the tree, edit the
file, run the whole suite — and compares against hand-written labels. If your
operator adds mutants to the fixture project, add their expected labels to
`tests/fixtures/oracle.toml` and this becomes a real check on it.

## Deciding whether it earns its place

The MVP set is small on purpose. Before proposing an addition, the useful
evidence is: run it against a real codebase and count how many of its survivors
are real gaps versus noise. `docs/oss-findings.md` records exactly that for the
existing set, and is the format to match.

An operator that produces many survivors and few real findings makes every other
finding harder to see. That is a cost, and it is paid by everyone who reads the
output.

**`deep` is where an operator waits for that evidence.** You do not have to
choose between "in the default set" and "not merged". `argument_swap`,
`default_arg` and `kwarg_drop` all landed in `deep` with no `oss-findings.md`
entry yet: they are opt-in, they cost nothing to anyone who has not asked for
them, and the run that produces the evidence is `--operators +argument_swap`
against a real project. Promoting an operator to `default` afterwards is a
one-line change. Demoting one after it has shipped in the default set changes
what every existing run reports, which is why the asymmetry is worth
respecting.
