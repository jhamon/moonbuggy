"""Criterion B4: the operator seam.

The checkable property is that adding an operator requires no edit to the
engine's traversal or orchestration -- only a new file in the operators package.
This test does exactly that: drops a throwaway operator module into the package,
and asserts it reaches generated output with generate.py untouched.

Written as a criterion check rather than a design test. If someone later
"simplifies" the registry into a hardcoded list in generate.py, everything else
keeps passing and only this fails.
"""

import contextlib
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


def reset_discovery(name="zz_throwaway_probe"):
    operators_pkg._discovered = False
    operators_pkg._REGISTRY[:] = [c for c in operators_pkg._REGISTRY if c.name != name]


def test_new_operator_is_discovered_without_touching_the_engine():
    engine_before = Path(moonbuggy_generate_path()).read_text()
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


# --------------------------------------------------------------------------
# The widened protocol: context, and targeted yields.
#
# Both halves are checked the same way as the seam itself -- by dropping a
# throwaway operator into the package -- because both are contracts with
# operator authors rather than internals. #8, #16 and #19 are all written
# against what these two tests pin.
# --------------------------------------------------------------------------

CONTEXT_PROBE = Path(operators_pkg.__file__).parent / "_throwaway_context_probe.py"

CONTEXT_SOURCE = '''
"""Throwaway contextual operator, written by tests/test_operator_seam.py."""

import ast

from . import register


@register
class ContextProbe:
    name = "zz_context_probe"

    def mutations_in_context(self, node, context):
        if not (isinstance(node, ast.Name) and node.id == "probe_me"):
            return
        described = "|".join(
            [
                type(context.parent).__name__,
                str(context.field),
                str(context.index),
                "/".join(type(a).__name__ for a in context.ancestors),
                type(context.nearest(ast.Call)).__name__,
            ]
        )
        yield ast.Name(id=described.replace("|", "_").replace("/", "_"), ctx=ast.Load())
'''

TARGET_PROBE = Path(operators_pkg.__file__).parent / "_throwaway_target_probe.py"

TARGET_SOURCE = '''
"""Throwaway targeting operator, written by tests/test_operator_seam.py."""

import ast

from . import register


@register
class TargetProbe:
    name = "zz_target_probe"

    def mutations(self, node):
        # Handed a compound statement, which `_splice` can never rewrite as a
        # single line -- and targets its test child, which it can.
        if isinstance(node, ast.If):
            yield node.test, ast.Constant(value=False)
'''


@contextlib.contextmanager
def probe(path, source, name):
    """Install a throwaway operator module for the duration of the block."""
    path.write_text(source)
    try:
        reset_discovery(name)
        importlib.invalidate_caches()
        yield
    finally:
        path.unlink(missing_ok=True)
        reset_discovery(name)


def test_a_contextual_operator_is_told_where_the_node_sits():
    with probe(CONTEXT_PROBE, CONTEXT_SOURCE, "zz_context_probe"):
        mutants = generate_mutants("x = f(1, probe_me)\n", module="m.py")

    probes = [m for m in mutants if m.operator == "zz_context_probe"]
    assert len(probes) == 1
    # parent, field, index within that field, the ancestor chain outermost
    # first, and the nearest enclosing Call.
    assert probes[0].mutated == "x = f(1, Call_args_1_Module_Assign_Call_Call)"


def test_an_operator_may_target_a_node_other_than_the_one_it_was_given():
    with probe(TARGET_PROBE, TARGET_SOURCE, "zz_target_probe"):
        mutants = generate_mutants("if ready:\n    pass\n", module="m.py")

    probes = [m for m in mutants if m.operator == "zz_target_probe"]
    # The `If` itself spans two lines and could never be spliced. Targeting
    # its test puts the edit on the `if` line, where it fits.
    assert [(m.line, m.original, m.mutated) for m in probes] == [
        (1, "if ready:", "if False:")
    ]
