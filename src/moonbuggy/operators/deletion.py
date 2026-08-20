"""Statement deletion: replace one statement with `pass`.

The flagship of the `deep` tier, and the highest-yield operator in the
mutation-testing literature. It subsumes others for free -- `return x` becomes
`pass`, which is return-value mutation without a separate operator -- and it
reaches lines no expression-level operator can touch, because plenty of
statements contain no comparison, no arithmetic and no literal at all.

It fits the single-line splice model better than deletion might suggest:

- splicing at `col_offset` preserves indentation, so `    foo(x)` becomes
  `    pass` and not a dedented `pass` at column zero;
- `pass` keeps a one-statement `if`/`for` body syntactically valid, which
  literal removal of the line would not;
- multi-line statements self-exclude through `_splice`'s existing guard, so
  nothing here has to special-case a call spread over four lines.

It is genuinely expensive -- roughly one extra mutant per statement -- which is
why it declares `tier = "deep"` and is opted into rather than run by default.

PROVE INERT, NOT IMPACTFUL
==========================

The instinct is to detect the statements that "have an effect" and mutate
those. That is the wrong direction, and the reason is asymmetric cost. Proving
that a statement matters is the hard half of the problem, and a wrong answer
there *loses a finding* -- the tool silently declines to mutate a line that a
suite does not check, and reports a cleaner score than the truth. Proving that
a statement is inert is the easy half, and a wrong answer costs one equivalent
mutant: noise in the survivor list, which a human can see and dismiss, and
which `moonbuggy accept` exists to retire.

So the exclusions below are a closed list of shapes that are *provably* free of
observable effect (plus one, imports, excluded for a different reason), and
everything else is mutated. The impactful set is arrived at by subtraction, not
by enumeration.
"""

import ast
from collections.abc import Iterator

from . import Context, Mutation, register

# The statements this operator will consider. An allowlist, not a denylist of
# the compound statements, and the difference is not stylistic: `if x: return`
# fits on one line, so `_splice`'s multi-line guard would let an `ast.If`
# through and "delete one statement" would quietly become "delete a whole
# branch and its body". Naming the simple statements is the only spelling of
# this rule that cannot be widened by accident.
#
# `ast.Pass`, `ast.Import`, `ast.ImportFrom`, `ast.Global` and `ast.Nonlocal`
# are simple statements too and are deliberately absent: they are the always-
# inert set, and leaving them out of the allowlist says so once instead of
# twice.
DELETABLE = (
    ast.Return,
    ast.Delete,
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    ast.Raise,
    ast.Assert,
    ast.Expr,
    ast.Break,
    ast.Continue,
)

# The fields that hold a statement list. A statement in one of these is in a
# body; a node anywhere else in the tree is not a statement at all.
BODY_FIELDS = frozenset({"body", "orelse", "finalbody"})

# Names whose presence anywhere in a function means its local bindings can be
# read without any `Name` node saying so. One of these in scope and the dead
# -store analysis below has no claim to make.
INTROSPECTION = frozenset({"locals", "vars", "eval", "exec", "globals"})


@register
class StatementDeletion:
    """Replace a statement with `pass`.

    A survivor means the statement can be removed from the program entirely
    and the suite still passes -- either the line is dead, or nothing checks
    what it does. Statements that are provably inert are never mutated, so a
    survivor here is a claim about the tests rather than about the mutation.
    """

    name = "statement_deletion"
    tier = "deep"
    cost = "high"

    def mutations_in_context(
        self, node: ast.AST, context: Context
    ) -> Iterator[Mutation]:
        """Yield `pass` in place of this statement, unless it is provably inert.

        Args:
            node: any AST node; only a simple statement in a body qualifies.
            context: where `node` sits. Both halves of the decision need it --
                whether this is a body position at all, and which function
                body the dead-store check should look at.

        Yields:
            One `ast.Pass`, if deleting this statement could be observable.
        """
        if not isinstance(node, DELETABLE):
            return
        if context.field not in BODY_FIELDS or context.index is None:
            return
        if _is_inert(node, context):
            return
        # Located onto the statement it replaces so `_splice` reads the right
        # `col_offset`, which is what preserves the indentation.
        yield ast.copy_location(ast.Pass(), node)


def _is_inert(node: ast.stmt, context: Context) -> bool:
    """Whether deleting this statement provably cannot change the program.

    Args:
        node: the statement being considered.
        context: where it sits, used to find its enclosing function.

    Returns:
        True when the mutation would be an equivalent mutant by construction,
        and so is not worth generating.
    """
    if isinstance(node, ast.Expr):
        # A docstring is this shape, and so is `...`. So is a stray `x` left
        # behind by a refactor. None of them do anything, and a bare name or
        # constant cannot hide a call that does -- deleting one is a
        # guaranteed equivalent mutant, not a survivor worth reading.
        return isinstance(node.value, (ast.Constant, ast.Name))
    return _is_dead_store(node, context)


def _is_dead_store(node: ast.stmt, context: Context) -> bool:
    """Whether this is `x = <pure expr>` with `x` never read in the function.

    The one local analysis this operator does, and the one that pays: a
    scratch binding nothing reads again is the single most common shape of
    equivalent mutant statement deletion produces, and it is decidable by
    walking one function body.

    Deliberately not interprocedural and deliberately not type-aware. The
    right-hand side has to be free of calls and `await` -- anything that could
    have an effect of its own means the assignment is not the only thing being
    deleted -- and the target has to be a plain name, because `self.x = 1` and
    `d[k] = 1` write somewhere this function cannot see the readers of.

    "Never read *again*" is implemented as "never read anywhere in the
    function", which is stricter than it sounds and deliberately so: a read
    textually above the write is still a read after it when the two sit in a
    loop, and a flow-sensitive answer would be a dataflow analysis rather than
    a walk.

    Args:
        node: the statement being considered.
        context: where it sits, used to find the enclosing function.

    Returns:
        True when deletion is provably equivalent.
    """
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return False
    target = node.targets[0]
    if not isinstance(target, ast.Name):
        return False
    if _has_effect(node.value):
        return False
    function = context.nearest(ast.FunctionDef, ast.AsyncFunctionDef)
    if function is None:
        # A module-level or class-level binding is read by name from anywhere
        # that imports the module, and there is no body to prove otherwise
        # against. Not inert as far as this operator can tell.
        return False
    return not _is_read_in(function, target.id)


def _has_effect(value: ast.expr) -> bool:
    """Whether evaluating this expression could do something besides produce a value."""
    for child in ast.walk(value):
        if isinstance(child, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom)):
            return True
    return False


def _is_read_in(function: ast.AST, name: str) -> bool:
    """Whether `name` could be read anywhere inside this function body.

    Args:
        function: the enclosing `def`, walked in full -- nested functions,
            comprehensions and lambdas included, since all of them can close
            over the binding.
        name: the bound name.

    Returns:
        True if any node reads it, declares it `global`/`nonlocal`, deletes it,
        or if the function can reach its own locals by introspection.
    """
    for child in ast.walk(function):
        if isinstance(child, ast.AugAssign):
            # `x += 1` is a read as well as a write, and its target `Name`
            # carries a `Store` context that says otherwise. Asked of the
            # `AugAssign` rather than of the `Name` for exactly that reason.
            target = child.target
            if isinstance(target, ast.Name) and target.id == name:
                return True
        elif isinstance(child, ast.Name):
            # Store is the write this is asking about; Load and Del are both
            # reads of the binding as far as deletability goes.
            if child.id == name and not isinstance(child.ctx, ast.Store):
                return True
            # `locals()` and friends make every local readable without a
            # `Name` node ever naming it, so the analysis has no claim here.
            if child.id in INTROSPECTION:
                return True
        elif isinstance(child, (ast.Global, ast.Nonlocal)) and name in child.names:
            # The binding is not local at all; its readers are elsewhere.
            return True
    return False
