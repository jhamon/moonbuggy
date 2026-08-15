"""Comparison operator swaps.

Each comparison maps to exactly one replacement, chosen to probe the boundary
rather than to invert the predicate -- see the operators.comparison_swap section
of tests/fixtures/oracle.toml.
"""

import ast

from . import register, replace_operator

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
            # A chained comparison is one node with several ops. Each is a
            # separate mutation site, so the ops list is rebuilt with exactly
            # one entry changed -- mutating two at once would be a different,
            # weaker test of the same line.
            ops = list(node.ops)
            ops[index] = replacement()
            yield replace_operator(node, ops=ops)
