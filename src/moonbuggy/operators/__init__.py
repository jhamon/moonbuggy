"""Mutation operators, and the registry that discovers them.

This is the operator half of the criterion-B4 seam. Operators are discovered by
importing every module in this package, so adding one means adding a file here
and nothing else -- no edit to the engine's traversal, no import list to update,
no registration call to remember. The engine asks for `all_operators()` and
never learns their names.

An operator is any class decorated with @register that provides:

    name        str -- stable identifier, matches the operator names in oracle.toml
    mutations() takes an AST node, yields replacement nodes (possibly none)

Operators must not mutate the node they are given; yield a copy.
"""

import importlib
import pkgutil

_REGISTRY = []


def register(cls):
    """Register an operator class. Used as a decorator."""
    _REGISTRY.append(cls)
    return cls


def all_operators():
    """Every registered operator, instantiated.

    Sorted by name so mutant ordering -- and therefore mutant ids -- stay stable
    across runs regardless of filesystem iteration order (criterion C3).
    """
    _discover()
    return [cls() for cls in sorted(_REGISTRY, key=lambda c: c.name)]


_discovered = False


def _discover():
    global _discovered
    if _discovered:
        return
    for info in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{info.name}")
    _discovered = True
