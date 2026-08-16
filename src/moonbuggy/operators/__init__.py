"""Mutation operators, and the registry that discovers them.

This is the operator half of the criterion-B4 seam. Operators are discovered by
importing every module in this package, so adding one means adding a file here
and nothing else -- no edit to the engine's traversal, no import list to update,
no registration call to remember. The engine asks for `all_operators()` and
never learns their names.

An operator is any class decorated with @register that provides:

    name        str -- stable identifier, matches the operator names in oracle.toml
    mutations() takes an AST node, yields replacement nodes (possibly none)

Operators must not mutate the node they are given. `replace_operator` is the
supported way to obey that without paying for a deep copy.
"""

import ast
import copy
import importlib
import pkgutil
from collections.abc import Iterator
from typing import Protocol


class Operator(Protocol):
    """The shape every operator implements.

    A class decorated with @register needs a stable `name` and a `mutations`
    method that takes an AST node and yields its mutated replacements (or
    nothing). This is a structural type, not a base class -- operator modules
    never import it and never subclass anything; they just happen to match it.
    """

    name: str

    def mutations(self, node: ast.AST) -> Iterator[ast.AST]:
        """Yield replacement nodes for `node`, or nothing if none apply."""
        ...


def replace_operator[NodeT: ast.AST](node: NodeT, **changes: object) -> NodeT:
    """A shallow copy of `node` with some fields replaced.

    Every operator needs the same thing: the original node with one field
    different. The obvious way to write that is `copy.deepcopy(node)` followed
    by an assignment, and every operator did -- which makes mutating one
    expression cost a copy of its entire subtree. An expression with *n* nested
    operators then costs O(n^2) node copies, and a 6000-term expression took
    over a minute to generate (found while writing the M1.4.8 tests; hypothesis
    H6 in docs/perf-hypotheses.md).

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
