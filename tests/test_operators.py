"""Operator-level tests.

The operator semantics under test are pinned in tests/fixtures/oracle.toml,
which was written before any of this code existed. That file is the spec; these
tests check the implementation matches it.
"""

import ast

import pytest

from moonbuggy.operators.arithmetic import ArithmeticSwap
from moonbuggy.operators.boolean import BooleanSwap
from moonbuggy.operators.boundary import Boundary
from moonbuggy.operators.comparison import ComparisonSwap
from moonbuggy.operators.constant import ConstantBool, ConstantInt


def parse_expr(source):
    """Return the single expression node from a source snippet."""
    return ast.parse(source, mode="eval").body


def parse_stmt(source):
    """Return the single statement node from a source snippet."""
    return ast.parse(source).body[0]


def mutate(operator, node):
    return [ast.unparse(m) for m in operator.mutations(node)]


# --- comparison_swap -------------------------------------------------------


def test_swaps_greater_equal_to_greater():
    assert mutate(ComparisonSwap(), parse_expr("x >= 1")) == ["x > 1"]


@pytest.mark.parametrize(
    "source,expected",
    [
        ("x < 1", "x <= 1"),
        ("x <= 1", "x < 1"),
        ("x > 1", "x >= 1"),
        ("x >= 1", "x > 1"),
        ("x == 1", "x != 1"),
        ("x != 1", "x == 1"),
    ],
)
def test_comparison_swap_table_matches_oracle_spec(source, expected):
    assert mutate(ComparisonSwap(), parse_expr(source)) == [expected]


def test_comparison_swap_ignores_identity_and_membership():
    # `is` and `in` are deliberately outside the MVP operator set.
    assert mutate(ComparisonSwap(), parse_expr("x is None")) == []
    assert mutate(ComparisonSwap(), parse_expr("x in items")) == []


# --- arithmetic_swap -------------------------------------------------------


@pytest.mark.parametrize(
    "source,expected",
    [
        ("a + b", "a - b"),
        ("a - b", "a + b"),
        ("a * b", "a / b"),
        ("a / b", "a * b"),
        ("a // b", "a / b"),
        ("a % b", "a // b"),
        ("a ** b", "a * b"),
    ],
)
def test_arithmetic_swap_table_matches_oracle_spec(source, expected):
    assert mutate(ArithmeticSwap(), parse_expr(source)) == [expected]


def test_arithmetic_swap_mutates_augmented_assignment():
    # loops-L11-arith and loops-L12-arith in the oracle are both `+=`/`-=`
    # sites, so augmented assignment must be a target, not just binary ops.
    assert mutate(ArithmeticSwap(), parse_stmt("total += n")) == ["total -= n"]
    assert mutate(ArithmeticSwap(), parse_stmt("n -= 1")) == ["n += 1"]


# --- boolean_swap ----------------------------------------------------------


def test_boolean_swap_maps_and_to_or():
    assert mutate(BooleanSwap(), parse_expr("a and b")) == ["a or b"]


def test_boolean_swap_maps_or_to_and():
    assert mutate(BooleanSwap(), parse_expr("a or b")) == ["a and b"]


def test_boolean_swap_leaves_not_alone():
    # `not` is explicitly excluded by the oracle's operator spec.
    assert mutate(BooleanSwap(), parse_expr("not a")) == []


# --- constant_int / constant_bool ------------------------------------------


def test_constant_int_increments():
    assert mutate(ConstantInt(), parse_expr("10")) == ["11"]


def test_constant_int_ignores_booleans():
    # bool is a subclass of int in Python; treating True as an int would emit
    # a nonsense `2` mutant and double-count every boolean site.
    assert mutate(ConstantInt(), parse_expr("True")) == []


def test_constant_int_ignores_floats_and_strings():
    # Floats are excluded by the oracle spec (float-comparison flakiness for no
    # extra signal). Strings must never be mutated -- that is criterion C2.
    assert mutate(ConstantInt(), parse_expr("1.5")) == []
    assert mutate(ConstantInt(), parse_expr("'hello'")) == []


def test_constant_bool_inverts():
    assert mutate(ConstantBool(), parse_expr("True")) == ["False"]
    assert mutate(ConstantBool(), parse_expr("False")) == ["True"]


def test_constant_bool_ignores_integers():
    assert mutate(ConstantBool(), parse_expr("1")) == []


# --- boundary --------------------------------------------------------------


def test_boundary_shrinks_single_argument_range():
    assert mutate(Boundary(), parse_expr("range(n)")) == ["range(n - 1)"]


def test_boundary_ignores_multi_argument_range():
    # Only the single-argument form, per the oracle spec.
    assert mutate(Boundary(), parse_expr("range(a, b)")) == []


def test_boundary_ignores_other_calls():
    assert mutate(Boundary(), parse_expr("len(n)")) == []
