"""Mutant generation: source -> AST -> candidate mutants.

AST-based rather than text substitution, which is what keeps mutations out of
string literals and comments for free (criterion C2) and gives exact line and
column positions for reporting.

This module knows nothing about which operators exist. It asks the registry and
walks the tree -- adding an operator requires no change here (criterion B4).
"""

import ast

from .mutant import Mutant
from .operators import all_operators

SUPPRESS_MARKER = "# moonbuggy: skip"


def generate_mutants(source, module):
    """Return every mutant for one module's source, in a stable order."""
    tree = ast.parse(source)
    lines = source.splitlines()
    operators = all_operators()

    found = []
    for node, in_function in _walk(tree, in_function=False):
        for operator in operators:
            for mutated_node in operator.mutations(node):
                mutant = _build(
                    node, mutated_node, operator, lines, module, found,
                    module_level=not in_function,
                )
                if mutant is not None:
                    found.append(mutant)

    found.sort(key=lambda m: (m.line, m.operator, m.id))
    return found


def _walk(node, in_function):
    """Walk the tree, tracking whether each node sits inside a function body.

    ast.walk cannot do this -- it discards the parent relationship, and scope is
    a property of ancestry. Class bodies count as module level too: they also
    execute at import time, so they have the same attribution problem.
    """
    yield node, in_function
    entering_function = isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    )
    for child in ast.iter_child_nodes(node):
        yield from _walk(child, in_function or entering_function)


def _build(node, mutated_node, operator, lines, module, found, module_level):
    lineno = getattr(node, "lineno", None)
    if lineno is None or lineno > len(lines):
        return None

    original_line = lines[lineno - 1]
    mutated_line = _splice(node, mutated_node, original_line)
    if mutated_line is None:
        return None

    # Occurrence index disambiguates several mutants from the same operator on
    # the same line (two `+` operators in one expression, say).
    index = sum(1 for m in found if m.line == lineno and m.operator == operator.name)

    return Mutant(
        id=f"{module}:{lineno}:{operator.name}:{index}",
        module=module,
        line=lineno,
        operator=operator.name,
        original=original_line.strip(),
        mutated=mutated_line.strip(),
        suppressed=SUPPRESS_MARKER in original_line,
        module_level=module_level,
    )


def _splice(node, mutated_node, original_line):
    """Rebuild the source line with the mutated fragment in place.

    Splicing by column offset rather than unparsing the whole statement, so the
    rest of the line keeps its original formatting -- unparsing an `if` would
    drag its entire body along, and unparsing anything reformats it.

    Returns None for nodes spanning multiple lines, which this cannot represent
    as a single-line diff. No such site exists in the fixture; a multi-line
    mutation would need a real diff rather than a line pair.
    """
    if node.end_lineno != node.lineno:
        return None
    return (
        original_line[: node.col_offset]
        + ast.unparse(mutated_node)
        + original_line[node.end_col_offset :]
    )
