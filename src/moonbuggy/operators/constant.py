"""Constant mutation.

Split into two operators because integers and booleans need different handling
and, more importantly, because `bool` is a subclass of `int` in Python. A single
operator that checked `isinstance(value, int)` would emit a nonsense `True -> 2`
mutant and double-count every boolean site.

Floats are deliberately not mutated: it invites float-comparison flakiness for
no extra signal. Strings are never mutated, which is what makes criterion C2
(no mutation inside string literals) fall out of the AST approach for free.
"""

import ast

from . import register


@register
class ConstantInt:
    name = "constant_int"

    def mutations(self, node):
        if not isinstance(node, ast.Constant):
            return
        if isinstance(node.value, bool) or not isinstance(node.value, int):
            return
        yield ast.Constant(value=node.value + 1)


@register
class ConstantBool:
    name = "constant_bool"

    def mutations(self, node):
        if not isinstance(node, ast.Constant):
            return
        if not isinstance(node.value, bool):
            return
        yield ast.Constant(value=not node.value)
