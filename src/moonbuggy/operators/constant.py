"""Constant mutation.

Split into two operators because integers and booleans need different handling
and, more importantly, because `bool` is a subclass of `int` in Python. A single
operator that checked `isinstance(value, int)` would emit a nonsense `True -> 2`
mutant and double-count every boolean site.

Floats are deliberately not mutated: it invites float-comparison flakiness for
no extra signal. Strings are never mutated, so no mutation ever lands inside a
string literal -- a guarantee that falls out of the AST approach for free.
"""

import ast
from collections.abc import Iterator

from . import register


@register
class ConstantInt:
    """Increment an integer literal by one.

    Floats are deliberately not mutated: it invites float-comparison
    flakiness for no extra signal.
    """

    name = "constant_int"

    def mutations(self, node: ast.AST) -> Iterator[ast.AST]:
        """Yield the constant, one larger.

        Args:
            node: any AST node; only a non-boolean integer constant qualifies.

        Yields:
            One replacement node, if this node is such a constant.
        """
        if not isinstance(node, ast.Constant):
            return
        if isinstance(node.value, bool) or not isinstance(node.value, int):
            return
        yield ast.Constant(value=node.value + 1)


@register
class ConstantBool:
    """Flip a boolean literal.

    Separate from ConstantInt because `bool` is a subclass of `int` in
    Python, so one operator checking `isinstance(value, int)` would emit a
    nonsense `True -> 2` mutant and double-count every boolean site.
    """

    name = "constant_bool"

    def mutations(self, node: ast.AST) -> Iterator[ast.AST]:
        """Yield the constant, negated.

        Args:
            node: any AST node; only a boolean constant qualifies.

        Yields:
            One replacement node, if this node is such a constant.
        """
        if not isinstance(node, ast.Constant):
            return
        if not isinstance(node.value, bool):
            return
        yield ast.Constant(value=not node.value)
