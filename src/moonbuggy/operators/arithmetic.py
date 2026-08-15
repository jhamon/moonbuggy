"""Arithmetic operator swaps.

Applies to binary operations and to augmented assignment -- `+=` is a site in
its own right, not merely a `+`. See operators.arithmetic_swap in oracle.toml.
"""

import ast
import copy

from . import register

SWAPS = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.FloorDiv: ast.Div,
    ast.Mod: ast.FloorDiv,
    ast.Pow: ast.Mult,
}


@register
class ArithmeticSwap:
    name = "arithmetic_swap"

    def mutations(self, node):
        if not isinstance(node, (ast.BinOp, ast.AugAssign)):
            return
        replacement = SWAPS.get(type(node.op))
        if replacement is None:
            return
        mutated = copy.deepcopy(node)
        mutated.op = replacement()
        yield mutated
