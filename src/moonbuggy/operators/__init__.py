"""Mutation operators, and the registry that discovers them.

This is the operator half of the criterion-B4 seam. Operators are discovered by
importing every module in this package, so adding one means adding a file here
and nothing else -- no edit to the engine's traversal, no import list to update,
no registration call to remember. The engine asks for `all_operators()` and
never learns their names.

An operator is any class decorated with @register that provides:

    name        str -- stable identifier, matches the operator names in oracle.toml
    mutations() takes an AST node, yields replacement nodes (possibly none)

An operator whose decision depends on where the node sits implements
`mutations_in_context(node, context)` instead, and is handed a :class:`Context`
describing its parent, the field it occupies, and the chain of enclosing nodes.
Either method may yield a bare replacement for the node it was given, or a
`(target, replacement)` pair naming a different node to rewrite.

Operators must not mutate the node they are given. `replace_operator` is the
supported way to obey that without paying for a deep copy.
"""

import ast
import copy
import importlib
import pkgutil
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# What an operator yields. A bare node replaces the node the operator was
# handed; a pair names the node to replace and its replacement.
#
# The pair form exists because the node an operator has to *see* to make its
# decision is not always the node it wants to *edit*. `_splice` rewrites one
# source line by column offset and so refuses any node spanning several lines,
# which structurally excludes every compound statement: an operator handed an
# `ast.If` could never yield a replacement for it. Targeting a child -- the
# test expression, a single statement in a body -- keeps the edit inside one
# line while leaving the decision at whatever altitude it needs.
Mutation = ast.AST | tuple[ast.AST, ast.AST]


@dataclass(frozen=True, slots=True)
class Context:
    """Where a node sits in the tree it was walked from.

    Handed to operators that implement `mutations_in_context`. Without it an
    operator sees a bare `ast.Name` and cannot tell the test of an `if` from
    the right-hand side of an assignment -- and "negate every name" is absurd
    where "negate the ones in test position" is exactly right.

    Frozen, and built lazily: `outer` links to the parent node's own context
    rather than materialising an ancestor tuple per node, so a deeply nested
    expression costs one small object per node instead of one tuple per node
    whose length grows with depth.
    """

    #: The enclosing node, or None for the root of the walk.
    parent: ast.AST | None
    #: The field of `parent` holding this node -- "test", "body", "ifs",
    #: "defaults". None at the root.
    field: str | None
    #: This node's position within `field` when that field is a list, and None
    #: when it holds a single node. "Where am I in the enclosing body?" is
    #: this, plus `parent`.
    index: int | None
    #: The parent node's own context, or None at the root.
    outer: "Context | None"

    @property
    def ancestors(self) -> tuple[ast.AST, ...]:
        """Every enclosing node, outermost first.

        Returns:
            the chain from the root of the walk down to the immediate parent,
            empty at the root.
        """
        chain = []
        context: Context | None = self
        while context is not None and context.parent is not None:
            chain.append(context.parent)
            context = context.outer
        chain.reverse()
        return tuple(chain)

    def nearest(self, *types: type[ast.AST]) -> ast.AST | None:
        """The closest enclosing node of any of `types`.

        "What is my nearest enclosing Call?" is the question this answers, and
        it walks outwards lazily rather than through `ancestors`, so the common
        case of a hit one or two levels up costs two comparisons.

        Args:
            *types: AST node classes to look for.

        Returns:
            the innermost enclosing node matching any of `types`, or None.
        """
        context: Context | None = self
        while context is not None and context.parent is not None:
            if isinstance(context.parent, types):
                return context.parent
            context = context.outer
        return None


class NodeOperator(Protocol):
    """The shape of an operator that needs nothing but the node.

    A class decorated with @register needs a stable `name` and a `mutations`
    method that takes an AST node and yields its mutated replacements (or
    nothing). This is a structural type, not a base class -- operator modules
    never import it and never subclass anything; they just happen to match it.
    """

    name: str

    def mutations(self, node: ast.AST) -> Iterator[Mutation]:
        """Yield replacements for `node`, or nothing if none apply."""
        ...


@runtime_checkable
class ContextualOperator(Protocol):
    """The shape of an operator that needs to know where the node sits.

    A second method name rather than a second parameter on `mutations`, so
    that an operator which does not need context does not have to accept it.
    The engine tells the two apart by which method is present; nothing else
    about registration or discovery changes.
    """

    name: str

    def mutations_in_context(
        self, node: ast.AST, context: Context
    ) -> Iterator[Mutation]:
        """Yield replacements for `node` in `context`, or nothing if none apply."""
        ...


# What the engine holds. Widened rather than replaced: the five operators that
# predate the context seam are still exactly the shape they were.
Operator = NodeOperator | ContextualOperator


def replace_operator[NodeT: ast.AST](node: NodeT, **changes: object) -> NodeT:
    """A shallow copy of `node` with some fields replaced.

    Every operator needs the same thing: the original node with one field
    different. The obvious way to write that is `copy.deepcopy(node)` followed
    by an assignment, and every operator did -- which makes mutating one
    expression cost a copy of its entire subtree. An expression with *n* nested
    operators then costs O(n^2) node copies, and a 6000-term expression took
    over a minute to generate (found while writing the M1.4.8 tests; hypothesis
    H6 in docs/development/perf-hypotheses.md).

    A shallow copy is enough because nothing downstream writes to the tree.
    Generation unparses the replacement node and throws it away; the children
    it shares with the original are only ever read. What the deep copy actually
    bought was protection against an operator that mutated its input, and that
    protection is better provided by not writing such an operator -- which this
    function makes the path of least resistance.

    Args:
        node: the AST node being mutated. Not modified.
        **changes: field values to replace on the copy. Untyped because the
            fields vary per AST node type (`op` for a `BinOp`, `ops` for a
            `Compare`, `args` for a `Call`); `ast.AST` itself declares no
            common field set to check them against.

    Returns:
        a new node of the same type, sharing the untouched children.
    """
    replacement = copy.copy(node)
    for field, value in changes.items():
        setattr(replacement, field, value)
    return ast.copy_location(replacement, node)


_REGISTRY: list[type[Operator]] = []


def register[OperatorT: type[Operator]](cls: OperatorT) -> OperatorT:
    """Register an operator class. Used as a decorator."""
    _REGISTRY.append(cls)
    return cls


def all_operators() -> list[Operator]:
    """Every registered operator, instantiated.

    Sorted by name so mutant ordering -- and therefore mutant ids -- stay stable
    across runs regardless of filesystem iteration order (criterion C3).
    """
    _discover()
    return [cls() for cls in sorted(_REGISTRY, key=lambda c: c.name)]


_discovered = False


def _discover() -> None:
    global _discovered
    if _discovered:
        return
    for info in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{info.name}")
    _discovered = True
