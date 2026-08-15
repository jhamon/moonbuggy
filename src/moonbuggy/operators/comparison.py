"""Comparison operator swaps.

Each comparison maps to exactly one replacement, chosen to probe the boundary
rather than to invert the predicate -- see the operators.comparison_swap section
of tests/fixtures/oracle.toml.
"""

import ast
import copy

from . import register

SWAPS = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}


@register
class ComparisonSwap:
    name = "comparison_swap"

    def mutations(self, node):
        if not isinstance(node, ast.Compare):
            return
        for index, op in enumerate(node.ops):
            replacement = SWAPS.get(type(op))
            if replacement is None:
                continue
            mutated = copy.deepcopy(node)
            mutated.ops[index] = replacement()
            yield mutated
