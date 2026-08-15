"""Off-by-one boundary mutation.

range(x) -> range(x - 1), single-argument form only. See operators.boundary in
oracle.toml.
"""

import ast
import copy

from . import register


@register
class Boundary:
    name = "boundary"

    def mutations(self, node):
        if not isinstance(node, ast.Call):
            return
        if not isinstance(node.func, ast.Name) or node.func.id != "range":
            return
        if len(node.args) != 1 or node.keywords:
            return
        mutated = copy.deepcopy(node)
        mutated.args[0] = ast.BinOp(
            left=mutated.args[0], op=ast.Sub(), right=ast.Constant(value=1)
        )
        yield mutated
