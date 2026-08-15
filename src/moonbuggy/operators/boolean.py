"""Boolean operator swaps.

Binary boolean operators only. `not` is deliberately not mutated -- see
operators.boolean_swap in oracle.toml.
"""

import ast

from . import register, replace_operator

SWAPS = {
    ast.And: ast.Or,
    ast.Or: ast.And,
}


@register
class BooleanSwap:
    name = "boolean_swap"

    def mutations(self, node):
        if not isinstance(node, ast.BoolOp):
            return
        replacement = SWAPS.get(type(node.op))
        if replacement is None:
            return
        yield replace_operator(node, op=replacement())
