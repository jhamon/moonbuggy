"""Mutant generation: source -> AST -> candidate mutants.

AST-based rather than text substitution, which is what keeps mutations out of
string literals and comments for free (criterion C2) and gives exact line and
column positions for reporting.

This module knows nothing about which operators exist. It asks the registry and
walks the tree -- adding an operator requires no change here (criterion B4).
"""

import ast
import sys

from .mutant import Mutant
from .operators import all_operators
from .srcio import strip_coding_cookie

SUPPRESS_MARKER = "# moonbuggy: skip"


class GenerationError(RuntimeError):
    """This module's source cannot be turned into mutants.

    Raised instead of letting SyntaxError or RecursionError escape, so the CLI
    can name the offending file and carry on with the rest (criterion M1.4.1).
    """


# Raised for the duration of generation. `ast.unparse` and `copy.deepcopy` are
# both plain Python recursion over the tree, so an expression nested a few
# hundred deep -- a long `a + b + c + ...` chain is enough -- exhausts the
# default limit of 1000 at a depth CPython's own parser accepts happily.
# Measured: at 20000 the interpreter still raises RecursionError cleanly rather
# than exhausting the C stack, so this buys depth without buying a crash.
DEEP_RECURSION_LIMIT = 20_000


def generate_mutants(source, module, on_skip=None):
    """Return every mutant for one module's source, in a stable order.

    Args:
        source: the module's full source text.
        module: the module's path, used as the first field of each mutant id.
        on_skip: called as ``(lineno, reason)`` for each site that could not be turned
            into a mutant. Sites are skipped rather than silently dropped, so
            the caller can say how much of the file it gave up on.

    Returns:
        a list of :class:`~moonbuggy.mutant.Mutant`, sorted by line then operator then
            id, so ids are stable across runs (criterion C3).

    Raises:
        GenerationError: if the source does not parse, or is nested so deeply that the
            parser itself gives up.
    """
    try:
        tree = ast.parse(strip_coding_cookie(source))
    except SyntaxError as error:
        raise GenerationError(
            f"syntax error at line {error.lineno}: {error.msg}"
        ) from error
    except (RecursionError, MemoryError, ValueError) as error:
        raise GenerationError(
            f"cannot parse: {type(error).__name__}: {error}"
        ) from error

    lines = source.splitlines()
    operators = all_operators()
    deferred = _function_body_lines(tree)

    found = []
    previous_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(previous_limit, DEEP_RECURSION_LIMIT))
    try:
        for node in _walk(tree):
            lineno = getattr(node, "lineno", None)
            for operator in operators:
                _mutate_node(
                    node,
                    operator,
                    lines,
                    module,
                    found,
                    module_level=lineno not in deferred,
                    on_skip=on_skip,
                )
    finally:
        sys.setrecursionlimit(previous_limit)

    found.sort(key=lambda m: (m.line, m.operator, m.id))
    return found


def _function_body_lines(tree):
    """Every line that runs only when some function is called.

    The complement -- everything else -- runs at import time, and that is what
    `Mutant.module_level` records. Selection uses the flag to widen a mutant's
    test set to the whole suite, because an import-time line is attributed to
    no test by a coverage map built from test-body execution, and "no covering
    tests" is indistinguishable from "genuinely uncovered".

    Three subtleties, each of which was wrong before the M1.2.6 property
    caught it:

    - A function's *body* defers, but its `def` line does not. Default
      arguments, decorators and annotations are all evaluated where the `def`
      is written. `def f(p=1 + 2)` at module level has a mutable expression
      that runs at import time, and calling it deferred selected no tests for
      it and reported a false SURVIVED.
    - Class bodies do not defer. They execute at import time like any other
      module-level statement, despite being indented.
    - Lambdas do not count as deferring their line, and this is deliberately
      conservative rather than exact. A module-level lambda's body really does
      run later, but it shares a line with the module-level statement that
      creates it, and one line carries one flag. Widening is the safe error
      here: it costs time, and the other direction costs correctness.

    Args:
        tree: a parsed module.

    Returns:
        the set of line numbers belonging to some function body.
    """
    deferred = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for statement in node.body:
            end = statement.end_lineno or statement.lineno
            deferred.update(range(statement.lineno, end + 1))
    return deferred


def _mutate_node(node, operator, lines, module, found, module_level, on_skip):
    """Apply one operator to one node, tolerating a site too deep to rewrite.

    Beyond the raised limit, deep-copying or unparsing a single expression can
    still fail. That costs one mutant, which is a smaller loss than abandoning
    the file -- but it is reported through `on_skip` rather than swallowed,
    because "fewer mutants than expected" is otherwise indistinguishable from
    "this code has fewer mutable sites".
    """
    try:
        for mutated_node in operator.mutations(node):
            mutant = _build(
                node,
                mutated_node,
                operator,
                lines,
                module,
                found,
                module_level=module_level,
            )
            if mutant is not None:
                found.append(mutant)
    except RecursionError:
        if on_skip is not None:
            on_skip(
                getattr(node, "lineno", 0),
                f"expression too deeply nested for the {operator.name} operator",
            )


def _walk(tree):
    """Yield every node, depth first, left to right.

    `ast.walk` would do the same job breadth first. The order matters here and
    is not cosmetic: it decides the occurrence index inside a mutant id, and
    an id that changes shape silently invalidates every cache entry.

    Iterative rather than recursive (criterion M1.4.8). A recursive walk raises
    RecursionError on deeply nested source at a depth well below what CPython
    itself will parse, and the traceback it produces names this function rather
    than the user's file -- a crash report about us for a property of their code.
    """
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        # Reversed so children are visited left to right despite the LIFO stack.
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


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
