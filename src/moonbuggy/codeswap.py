"""Apply a mutation to an ALREADY-IMPORTED module, in place.

This is the "function-level swap" option from section 4.2, and it exists for a
reason the design doc did not anticipate: it is what makes a warm process usable.

The import-hook approach in inmemory.py can only mutate a module that has not
been imported yet, so every mutant needs a process where imports have not
happened -- and that means paying test-module import, assert rewriting and
pytest collection per mutant. Measured at ~90ms per mutant against ~12ms for
pytest.main() in a process where those imports are already done.

Swapping in place lifts that restriction. A test module that did
`from app.thing import compute` holds a reference to the FUNCTION OBJECT, so
replacing that object's __code__ changes what the test calls, with no re-import
and no reference chasing.

Two shapes of mutation, two mechanisms:

- inside a function: replace the function's __code__ with the code compiled
  from mutated source.
- at module level: exec the mutated line in the module's __dict__. Functions
  read their globals dynamically at call time, so a rebound module-level
  constant takes effect immediately for every already-imported caller.

Where neither applies (a decorator has replaced the function object with a
wrapper, say), this reports failure rather than guessing, and the caller falls
back to the import-hook path. A silently-unapplied mutation is a false SURVIVED.
"""

import ast
import linecache

from .inmemory import mutated_source


class SwapFailed(Exception):
    """The mutation could not be applied in place. Caller should fall back."""


def apply_in_place(module, path, line, mutated_text):
    """Mutate an imported module object. Raises SwapFailed if it cannot."""
    source = mutated_source(path, line, mutated_text)
    tree = ast.parse(source)
    qualname = _enclosing_function_path(tree, line)

    # Keep tracebacks honest even though nothing was re-imported (D4).
    linecache.cache[str(path)] = (len(source), None, source.splitlines(keepends=True), str(path))

    if qualname is None:
        _exec_module_level(module, source, line)
    else:
        _swap_code(module, source, path, qualname)


def _exec_module_level(module, source, line):
    """Re-execute one module-level statement in the module's own namespace."""
    statement = _statement_at(source, line)
    if statement is None:
        raise SwapFailed(f"no module-level statement at line {line}")
    try:
        exec(compile(statement, "<moonbuggy>", "exec"), module.__dict__)
    except Exception as error:  # noqa: BLE001 - any failure means fall back
        raise SwapFailed(f"could not exec module-level statement: {error}") from error


def _statement_at(source, line):
    """The full source of the top-level statement covering `line`."""
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in tree.body:
        if node.lineno <= line <= (node.end_lineno or node.lineno):
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return None


def _swap_code(module, source, path, qualname):
    new_code = _find_code(compile(source, str(path), "exec"), qualname)
    if new_code is None:
        raise SwapFailed(f"no compiled code object for {'.'.join(qualname)}")

    target = _resolve(module, qualname)
    if target is None:
        raise SwapFailed(f"no live function object for {'.'.join(qualname)}")
    try:
        target.__code__ = new_code
    except (AttributeError, ValueError, TypeError) as error:
        raise SwapFailed(f"could not replace __code__: {error}") from error


def _enclosing_function_path(tree, line):
    """Qualname path of the innermost function containing `line`, or None.

    None means the line is at module or class-body level, which executes at
    import time and therefore needs the exec mechanism instead.
    """
    best = None

    def walk(node, path):
        nonlocal best
        for child in ast.iter_child_nodes(node):
            name = getattr(child, "name", None)
            is_scope = isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
            child_path = path + [name] if (is_scope and name) else path

            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start, end = child.lineno, child.end_lineno or child.lineno
                # The `def` line itself (decorators, signature) is not part of
                # the body's code object, so a mutation there cannot be swapped.
                body_start = child.body[0].lineno if child.body else start
                if body_start <= line <= end:
                    if best is None or len(child_path) > len(best):
                        best = child_path
            walk(child, child_path)

    walk(tree, [])
    return best


def _find_code(code, qualname):
    """Descend co_consts following the qualname path to the target code object."""
    current = code
    for name in qualname:
        found = None
        for const in current.co_consts:
            if hasattr(const, "co_name") and const.co_name == name:
                found = const
                break
        if found is None:
            return None
        current = found
    return current


def _resolve(module, qualname):
    """The live function object at `qualname` within an imported module."""
    current = module
    for name in qualname:
        current = getattr(current, name, None)
        if current is None:
            return None
    # Unwrap staticmethod/classmethod, which hide the function one level down.
    current = getattr(current, "__func__", current)
    return current if hasattr(current, "__code__") else None
