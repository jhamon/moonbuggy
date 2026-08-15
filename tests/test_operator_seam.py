"""Criterion B4: the operator seam.

The checkable property is that adding an operator requires no edit to the
engine's traversal or orchestration -- only a new file in the operators package.
This test does exactly that: drops a throwaway operator module into the package,
and asserts it reaches generated output with generate.py untouched.

Written as a criterion check rather than a design test. If someone later
"simplifies" the registry into a hardcoded list in generate.py, everything else
keeps passing and only this fails.
"""

import importlib
from pathlib import Path

import moonbuggy.operators as operators_pkg
from moonbuggy.generate import generate_mutants

THROWAWAY = Path(operators_pkg.__file__).parent / "_throwaway_seam_probe.py"

SOURCE = '''
"""Throwaway operator, written by tests/test_operator_seam.py."""

import ast

from . import register


@register
class UnaryNegate:
    name = "zz_throwaway_probe"

    def mutations(self, node):
        if isinstance(node, ast.Name) and node.id == "probe_me":
            yield ast.Name(id="probe_mutated", ctx=ast.Load())
'''


def reset_discovery():
    operators_pkg._discovered = False
    operators_pkg._REGISTRY[:] = [
        c for c in operators_pkg._REGISTRY if c.name != "zz_throwaway_probe"
    ]


def test_new_operator_is_discovered_without_touching_the_engine():
    engine_before = (Path(moonbuggy_generate_path()).read_text())
    THROWAWAY.write_text(SOURCE)
    try:
        reset_discovery()
        importlib.invalidate_caches()

        mutants = generate_mutants("x = probe_me\n", module="m.py")

        assert [m.operator for m in mutants] == ["zz_throwaway_probe"]
        assert mutants[0].mutated == "x = probe_mutated"
    finally:
        THROWAWAY.unlink(missing_ok=True)
        reset_discovery()

    # The engine file is byte-identical: the operator was added by adding a
    # file, which is the whole claim.
    assert Path(moonbuggy_generate_path()).read_text() == engine_before


def moonbuggy_generate_path():
    import moonbuggy.generate

    return moonbuggy.generate.__file__


def test_registry_is_restored_after_probe():
    # Guards the test above from leaking state into the rest of the suite.
    names = [op.name for op in operators_pkg.all_operators()]

    assert "zz_throwaway_probe" not in names
    assert "comparison_swap" in names
