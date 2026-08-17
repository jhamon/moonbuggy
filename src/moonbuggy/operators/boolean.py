"""Boolean operator swaps.

Binary boolean operators only. `not` is deliberately not mutated -- see
operators.boolean_swap in oracle.toml.
"""

import ast
from collections.abc import Iterator

from . import register, replace_operator

SWAPS = {
    ast.And: ast.Or,
    ast.Or: ast.And,
}


@register
class BooleanSwap:
    """Swap `and` for `or` and back.

    Binary boolean operators only. `not` is deliberately left alone -- see
    operators.boolean_swap in oracle.toml.
    """

    name = "boolean_swap"

    def mutations(self, node: ast.AST) -> Iterator[ast.AST]:
        """Yield the node with its boolean operator swapped.

        Args:
            node: any AST node; only `BoolOp` produces a mutation.

        Yields:
            One replacement node, if this node is a boolean operation.
        """
        if not isinstance(node, ast.BoolOp):
            return
        replacement = SWAPS.get(type(node.op))
        if replacement is None:
            return
        yield replace_operator(node, op=replacement())
