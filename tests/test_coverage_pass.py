"""Criterion D1: the line -> covering-tests map.

Built by pytest-cov's per-test contexts (see docs/spike-b-findings.md for why
that mechanism won). The tests here lean on the cases where a wrong map causes
a false SURVIVED rather than merely a slow run, because those are the ones with
no external symptom.
"""

from pathlib import Path

import pytest

from moonbuggy.coverage_pass import run_coverage_pass

FIXTURE = Path(__file__).parent / "fixtures" / "sample_project"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def linemap():
    return run_coverage_pass(FIXTURE, FIXTURE / "sample")


def test_maps_a_line_to_the_test_that_executes_it(linemap):
    covering = linemap.tests_covering("sample/discounts.py", 11)

    assert "tests/test_discounts.py::test_small_order_not_bulk" in covering


def test_finds_covering_tests_in_a_differently_named_file(linemap):
    # The cross-file case. A tool selecting tests by module name would miss
    # test_pricing.py entirely and report discounts-L11-cmp as SURVIVED.
    covering = linemap.tests_covering("sample/discounts.py", 11)

    assert "tests/test_pricing.py::test_threshold_exactly_qualifies" in covering


def test_selects_fewer_tests_than_the_whole_suite(linemap):
    # Criterion D2. The entire point: a mutant on this line runs a subset.
    covering = linemap.tests_covering("sample/discounts.py", 16)

    assert 0 < len(covering) < len(linemap.all_tests())


def test_uncovered_line_maps_to_no_tests(linemap):
    # inventory-L15-const. restock_amount's early return is never reached, so
    # the map must be empty here -- and the runner must report SURVIVED without
    # running anything, rather than falling back to the whole suite.
    assert linemap.tests_covering("sample/inventory.py", 15) == set()


def test_all_tests_lists_the_whole_suite(linemap):
    assert len(linemap.all_tests()) == 14


def test_node_ids_are_usable_as_pytest_arguments(linemap):
    # pytest-cov records contexts as `<nodeid>|<phase>`; the phase suffix has to
    # be stripped or the id cannot be handed back to pytest to select with.
    for test_id in linemap.all_tests():
        assert "|" not in test_id
        assert "::" in test_id


def test_setup_and_teardown_phases_are_merged(linemap):
    # A line executed in a fixture belongs to the tests using that fixture.
    # Keeping only the `run` phase would drop those, select too few tests, and
    # produce false SURVIVEDs -- so all phases are unioned deliberately.
    covering = linemap.tests_covering("sample/discounts.py", 11)

    assert covering
