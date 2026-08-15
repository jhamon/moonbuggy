"""Tests for sample.discounts -- the "obvious" test file for that module.

Deliberately does NOT test the threshold boundary. That case lives in
test_pricing.py, which is what makes the boundary mutant a cross-file kill: a
tool that guesses covering tests from the module name alone would wrongly
report it SURVIVED.
"""

from sample.discounts import discount_percent, qualifies_for_bulk


def test_small_order_not_bulk():
    assert qualifies_for_bulk(1) is False


def test_large_order_is_bulk():
    assert qualifies_for_bulk(50) is True


def test_no_discount_for_small_order():
    assert discount_percent(1) == 0
