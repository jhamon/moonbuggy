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

`mutations(node)`
: Takes one AST node, yields zero or more replacement nodes. Called for every
  node in the tree, so the first thing it does is decide whether this node is
  its business.

The engine asks the registry for `all_operators()` and never learns their names.
Operators are discovered by importing every module in the package, which is why
adding a file is enough.

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

That is the whole operator. It is now discovered, applied, and selectable with
`--operators emptiness_swap`.

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

**Does it hold the properties?** `make check-properties` runs seven invariants
over generated modules — every mutant compiles, no mutation touches a string or
a comment, splicing round-trips byte for byte, ids stay unique. A new operator
is covered by all of them automatically, and this is where a mistake in
splicing shows up.

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
