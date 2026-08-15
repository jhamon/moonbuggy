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
from types import FunctionType

from .inmemory import mutated_source
from .srcio import strip_coding_cookie


class SwapFailed(Exception):
    """The mutation could not be applied in place. Caller should fall back."""


def apply_in_place(module, path, line, mutated_text):
    """Mutate an imported module object in place.

    Args:
        module: the live module object, already imported.
        path: the module's file path.
        line: 1-based line number to replace.
        mutated_text: the replacement line.

    Raises:
        SwapFailed: if the mutation cannot be applied in place, so the caller falls
            back to the import-hook path rather than reporting a status for a
            mutation that never took effect.
    """
    source = mutated_source(path, line, mutated_text)
    # `compile` and `ast.parse` reject a coding declaration inside a str, so a
    # module with a PEP 263 cookie would raise here and drop the whole batch to
    # the cold path. Neutralising the cookie keeps every offset intact.
    source = strip_coding_cookie(source)
    tree = ast.parse(source)
    qualname = _enclosing_function_path(tree, line)

    # Keep tracebacks honest even though nothing was re-imported (D4).
    linecache.cache[str(path)] = (len(source), None, source.splitlines(keepends=True), str(path))

    if qualname is None:
        _exec_module_level(module, source, line)
    else:
        _swap_code(module, source, path, qualname)


_MISSING = object()


def _exec_module_level(module, source, line):
    """Re-execute one module-level statement in the module's own namespace.

    Re-executing is only half the job, and the missing half caused two false
    SURVIVEDs that the M4 hand verification caught in more-itertools. `exec`
    rebinds the name **in this module only**. Any other module that did
    `from here import thing` holds its own reference, taken at import time, and
    goes on using the unmutated object -- so a test importing from a package's
    `__init__` tests code the mutation never reached, passes, and the mutant is
    reported SURVIVED.

    Two repairs, in order of preference:

    - **Preserve identity.** For a plain function, the existing object's
      `__code__` and defaults are replaced and the *original object* stays
      bound. Every reference anywhere -- other modules, registries, containers,
      partials -- then sees the mutation, because there is still only one
      object.
    - **Rebind aliases.** Where identity cannot be preserved (a decorated
      function, a class, a constant), every module attribute that currently
      *is* the previous object is repointed at the new one. Narrower than the
      first, since it cannot reach a value inside a list, but it covers the
      `from x import y` case that matters.
    """
    statement = _statement_at(source, line)
    if statement is None:
        raise SwapFailed(f"no module-level statement at line {line}")

    names = _bound_names(statement)
    before = {name: module.__dict__.get(name, _MISSING) for name in names}

    try:
        exec(compile(statement, "<moonbuggy>", "exec"), module.__dict__)
    except Exception as error:  # noqa: BLE001 - any failure means fall back
        raise SwapFailed(f"could not exec module-level statement: {error}") from error

    for name in names:
        previous = before[name]
        fresh = module.__dict__.get(name, _MISSING)
        if previous is _MISSING or fresh is _MISSING or fresh is previous:
            continue
        if _adopt(previous, fresh):
            module.__dict__[name] = previous
        else:
            _rebind_aliases(name, previous, fresh)


def _bound_names(statement):
    """The module-level names this statement binds."""
    names = set()
    for node in ast.walk(ast.parse(statement)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _adopt(previous, fresh):
    """Move the new function's body and defaults onto the existing object.

    Returns False when that cannot be done -- a decorated function whose
    wrapper has different free variables, or anything that is not a plain
    function -- and the caller falls back to rebinding aliases.
    """
    if not (isinstance(previous, FunctionType) and isinstance(fresh, FunctionType)):
        return False
    if previous.__qualname__ != fresh.__qualname__:
        return False
    try:
        previous.__code__ = fresh.__code__
    except (AttributeError, ValueError, TypeError):
        return False
    previous.__defaults__ = fresh.__defaults__
    previous.__kwdefaults__ = fresh.__kwdefaults__
    return True


def _rebind_aliases(name, previous, fresh):
    """Repoint every module attribute that is still the pre-mutation object.

    Matched on identity *and* on name, so an unrelated attribute holding an
    equal value is untouched. Where the value is a shared immutable -- a small
    integer, an interned string -- a same-named attribute elsewhere could match
    by coincidence. That is the safe direction to be wrong in: it makes a test
    more likely to notice the mutation, and the failure mode this whole
    function exists to prevent is a mutation nothing notices.
    """
    import sys

    for other in list(sys.modules.values()):
        if other is None:
            continue
        try:
            if getattr(other, name, None) is previous:
                setattr(other, name, fresh)
        except Exception:  # noqa: BLE001 - a module with an exotic __getattr__
            continue


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

    # A decorator can leave a wrapper where the function used to be, and the
    # wrapper's code object is not the one just compiled. Assigning anyway
    # would either raise on a freevars mismatch or -- worse -- succeed and
    # replace the wrapper's body with the wrapped function's. Refusing here
    # sends the batch to the import-hook path, which handles decorators fine.
    if target.__code__.co_name != new_code.co_name:
        raise SwapFailed(
            f"{'.'.join(qualname)} resolves to {target.__code__.co_name}, "
            "probably a decorator wrapper"
        )

    try:
        target.__code__ = new_code
    except (AttributeError, ValueError, TypeError) as error:
        raise SwapFailed(f"could not replace __code__: {error}") from error


def _enclosing_function_path(tree, line):
    """Qualname path of the OUTERMOST function containing `line`, or None.

    Outermost rather than innermost, and that is the whole trick for nested
    functions. A closure has no live object to swap -- `count_to.work` is not an
    attribute of anything, it is a code object in `count_to`'s constants, built
    afresh on every call. Recompiling the *enclosing* function from the mutated
    source produces a code object whose nested constants are already mutated,
    so swapping the outer function's `__code__` reaches the inner one.

    Enclosing classes stay in the path, since `Cls.method` really is resolvable
    both as an attribute and as a nested code object.

    The known limit: a closure created *before* the swap keeps the code it was
    built with. That is a real hole, and it is why this reports failure loudly
    everywhere else -- a mutation that does not take effect reads as SURVIVED.

    None means the line is at module or class-body level, which executes at
    import time and therefore needs the exec mechanism instead.
    """
    found = None

    def walk(node, path, inside_function):
        nonlocal found
        for child in ast.iter_child_nodes(node):
            name = getattr(child, "name", None)
            is_function = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            is_scope = is_function or isinstance(child, ast.ClassDef)
            child_path = path + [name] if (is_scope and name) else path

            if is_function and not inside_function:
                start, end = child.lineno, child.end_lineno or child.lineno
                # The `def` line itself (decorators, signature) is not part of
                # the body's code object, so a mutation there cannot be swapped.
                body_start = child.body[0].lineno if child.body else start
                if body_start <= line <= end and found is None:
                    found = child_path

            walk(child, child_path, inside_function or is_function)

    walk(tree, [], False)
    return found


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
