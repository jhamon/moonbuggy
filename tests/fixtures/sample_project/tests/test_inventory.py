"""Tests for sample.inventory.

Deliberately weak. No test exercises stock == 0, stock == target, or the
restock_amount early-return branch, so several mutants survive by design.
"""

from sample.inventory import is_available, restock_amount


def test_in_stock_item_is_available():
    assert is_available(5, False) is True


def test_discontinued_item_is_not_available():
    assert is_available(5, True) is False


def test_restock_fills_to_target():
    assert restock_amount(2, 10) == 8
