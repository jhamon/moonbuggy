"""Boolean operator swaps.

Binary boolean operators only. `not` is deliberately not mutated -- see
operators.boolean_swap in oracle.toml.
"""

import ast
import copy

from . import register

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
        mutated = copy.deepcopy(node)
        mutated.op = replacement()
        yield mutated
