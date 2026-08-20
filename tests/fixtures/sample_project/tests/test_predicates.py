"""Tests for sample.predicates.

Four of the five conditions are tested on both sides, so inverting them is
caught. `wanted` is tested only on the empty list -- the shape of a suite that
covers a comprehension without ever exercising its guard -- so the guard mutant
survives by design. See oracle.toml.
"""

from types import SimpleNamespace

from sample.predicates import describe, gate, label, verdict, wanted


def test_ready_item_is_described_as_ready():
    assert describe(SimpleNamespace(ready=True)) == "ready"


def test_unready_item_is_described_as_waiting():
    assert describe(SimpleNamespace(ready=False)) == "waiting"


def test_label_is_on_when_the_flag_is_set():
    assert label(True) == "on"


def test_label_is_off_when_the_flag_is_clear():
    assert label(False) == "off"


def test_gate_is_open_when_not_blocked():
    assert gate(False) == "open"


def test_gate_is_closed_when_blocked():
    assert gate(True) == "closed"


def test_verdict_is_yes_when_ok():
    assert verdict(True) == "yes"


def test_verdict_is_no_when_not_ok():
    assert verdict(False) == "no"


def test_wanted_of_nothing_is_nothing():
    # Deliberately the only test of `wanted`: the guard is never exercised
    # with a value that would be filtered, so negating it changes nothing
    # this suite can see.
    assert wanted([]) == []
