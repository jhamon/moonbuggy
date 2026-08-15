"""Tests for sample.loops.

Thorough, so that most mutants here are killed -- except the one that makes
countdown() never terminate (timeout case) and the one that is genuinely
equivalent (while n > 0 -> while n >= 0, which sums the same total).
"""

from sample.loops import countdown, sum_first


def test_countdown_sums_descending_run():
    assert countdown(3) == 6


def test_countdown_of_zero_is_zero():
    assert countdown(0) == 0


def test_sum_first_excludes_upper_bound():
    assert sum_first(4) == 6


def test_sum_first_of_zero_is_zero():
    assert sum_first(0) == 0
