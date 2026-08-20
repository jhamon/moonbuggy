"""Condition negation.

Wraps the test of an `if`, an `elif`, a conditional expression, or a
comprehension guard in `not (...)`. One mutant per condition. See
operators.condition_negation in oracle.toml.

Two deliberate exclusions. A `while` test is never negated: the mutant either
does nothing observable or loops forever and burns the whole `--timeout`.
A literal test is never negated: `if True:` already gets `if False:` from
`constant_bool`, and `if not True:` says the same thing twice. The reasoning
for each is written at its site -- above `TEST_HOLDERS` and in
`mutations_in_context` -- and both are enforced, not aspirational.

This is the operator that reaches conditions the other five cannot. They all
need a specific node type to bite -- a `Compare`, a `BoolOp`, a literal -- so
`if is_valid(x):`, `if flag:` and `if not ready:` were completely unmutated,
and a suite entirely blind to `is_valid` produced no finding at all. Predicate
helpers are the ordinary way people write conditionals, not an exotic shape.

It is the first operator to use the context seam: negating every `ast.Name` in
a module would be absurd, and negating the ones in test position is exactly
right, so the decision is about where the node sits rather than what it is.
"""

import ast
from collections.abc import Iterator

from . import Context, Mutation, register

# Nodes whose `test` field is a condition in the sense that matters here:
# a value the language itself evaluates for truth and branches on.
#
# `ast.While` is deliberately absent, and it is the one exclusion worth
# explaining. Negating a loop test has two possible outcomes and neither is
# worth its price. If the loop ran under the test, the negated loop is skipped
# and the work simply does not happen -- caught by nearly any assertion, so the
# mutant teaches nothing. If the loop did *not* run under the test, which is
# what an empty-input or already-finished case looks like, the negated loop
# runs forever: `while queue:` becomes `while not queue:` and never terminates.
# That mutant burns the whole `--timeout` rather than failing fast, and
# empty-collection tests are common enough that a loop-heavy module would pay
# it many times over. A `while` is also rarely unmutated in practice -- its
# test usually carries a comparison or a constant the other operators already
# reach. Excluded on the fourth rule in docs/writing-an-operator.md: a mutation
# earns its place by being a mistake a human could plausibly have made, and
# `while not queue:` where `while queue:` belongs is not that mistake.
TEST_HOLDERS = (ast.If, ast.IfExp)


@register
class ConditionNegation:
    """Invert a condition, whatever shape it has.

    Fires on the test of `if`/`elif`, on the test of a conditional
    expression, and on each `if` clause of a comprehension. A survivor is
    unambiguous: an entire branch was inverted and no test noticed.

    Does not fire on a `while` test or on a literal test; see the module
    docstring for why.
    """

    name = "condition_negation"

    def mutations_in_context(
        self, node: ast.AST, context: Context
    ) -> Iterator[Mutation]:
        """Yield the condition wrapped in `not`, if this node is one.

        Args:
            node: any AST node; only an expression in test position qualifies.
            context: where `node` sits, which is the whole of the decision.

        Yields:
            One replacement node, if this node is a condition.
        """
        if not isinstance(node, ast.expr) or not _is_condition(context):
            return
        # A literal test is already covered: `if True:` gets `if False:` from
        # constant_bool, and `if not True:` says the same thing twice.
        if isinstance(node, ast.Constant):
            return
        # `ast.unparse` parenthesises by precedence, so a `not` over an `and`
        # chain or a lambda comes back correctly bracketed without this
        # having to know the precedence table.
        yield ast.UnaryOp(op=ast.Not(), operand=node)


def _is_condition(context: Context) -> bool:
    """Whether the node in this context is evaluated for truth and branched on."""
    if context.field == "test" and isinstance(context.parent, TEST_HOLDERS):
        return True
    # A comprehension's guards live in `ifs`, and the `ast.comprehension` node
    # holding them carries no position of its own -- so the guard expression,
    # which does, is the only node here that can be spliced.
    return context.field == "ifs" and isinstance(context.parent, ast.comprehension)
