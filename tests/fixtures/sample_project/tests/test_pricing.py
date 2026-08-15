"""Pricing-level tests that happen to be the only killers for two mutants in
sample/discounts.py.

The file name intentionally does not match the module it covers. Coverage-guided
test selection must find these via the line->test map, not via naming.
"""

from sample.discounts import discount_percent, qualifies_for_bulk


def test_threshold_exactly_qualifies():
    assert qualifies_for_bulk(10) is True


def test_bulk_discount_is_twenty_percent():
    assert discount_percent(10) == 20
