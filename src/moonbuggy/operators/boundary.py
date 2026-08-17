"""Off-by-one boundary mutation.

range(x) -> range(x - 1), single-argument form only. See operators.boundary in
oracle.toml.
"""

import ast
from collections.abc import Iterator

from . import register, replace_operator


@register
class Boundary:
    """Shift a single-argument `range()` by one.

    `range(x)` becomes `range(x - 1)`. Off-by-one in a loop bound is the
    mistake this exists to notice, and a test that never checks the last
    iteration will not.
    """

    name = "boundary"

    def mutations(self, node: ast.AST) -> Iterator[ast.AST]:
        """Yield the call with its range shortened by one.

        Args:
            node: any AST node; only a one-argument `range()` call qualifies.

        Yields:
            One replacement node, if this node is such a call.
        """
        if not isinstance(node, ast.Call):
            return
        if not isinstance(node.func, ast.Name) or node.func.id != "range":
            return
        if len(node.args) != 1 or node.keywords:
            return
        # The original argument node is reused, not copied. It is never
        # written to -- only unparsed -- and it is the whole point of H6 that
        # a nested argument is not deep-copied once per enclosing operator.
        shifted = ast.BinOp(
            left=node.args[0], op=ast.Sub(), right=ast.Constant(value=1)
        )
        yield replace_operator(node, args=[shifted])
