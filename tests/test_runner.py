"""Criterion D: the fast path, checked against the oracle.

D5 is the load-bearing test in the whole project: moonbuggy's fast path -- in
memory mutation, coverage-guided selection, no file ever written -- must agree
with the naive oracle on every mutant, in both serial and xdist modes. If this
disagrees, nothing else about the tool matters.
"""

import hashlib
import tomllib
from pathlib import Path

import pytest

from moonbuggy.coverage_pass import run_coverage_pass
from moonbuggy.generate import generate_mutants
from moonbuggy.runner import run_mutants

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "sample_project"
ORACLE = tomllib.loads((FIXTURES / "oracle.toml").read_text())
MODULES = [
    "sample/discounts.py",
    "sample/inventory.py",
    "sample/loops.py",
    "sample/config.py",
]

pytestmark = pytest.mark.slow


def all_mutants():
    mutants = []
    for module in MODULES:
        mutants.extend(generate_mutants((FIXTURE / module).read_text(), module=module))
    return mutants


@pytest.fixture(scope="module")
def linemap():
    return run_coverage_pass(FIXTURE, FIXTURE / "sample")


@pytest.fixture(scope="module")
def serial_results(linemap):
    return run_mutants(FIXTURE, all_mutants(), linemap, timeout=8)


@pytest.fixture(scope="module")
def xdist_results(linemap):
    return run_mutants(FIXTURE, all_mutants(), linemap, timeout=20, xdist_workers=2)


def expected_status():
    return {
        (m["module"], m["line"], m["operator"]): m["status"] for m in ORACLE["mutant"]
    }


def compare(results):
    expected = expected_status()
    return [
        f"{r.mutant.id}: expected {expected[key]}, got {r.status}"
        for r in results
        for key in [(r.mutant.module, r.mutant.line, r.mutant.operator)]
        if r.status != expected[key]
    ]


def test_fast_path_agrees_with_oracle_serially(serial_results):
    assert not compare(serial_results), "\n".join(compare(serial_results))


def test_fast_path_agrees_with_oracle_under_xdist(xdist_results):
    # Risk 2 in section 4.2. A worker re-importing unmutated code reports a
    # false SURVIVED, silently, on a common configuration.
    assert not compare(xdist_results), "\n".join(compare(xdist_results))


def test_every_mutant_has_a_result(serial_results):
    assert len(serial_results) == ORACLE["meta"]["total_mutants"]


def test_status_counts_match_oracle(serial_results):
    counts = {}
    for result in serial_results:
        counts[result.status] = counts.get(result.status, 0) + 1

    assert counts == ORACLE["meta"]["expected_counts"]


def test_selection_runs_fewer_tests_than_the_whole_suite(serial_results, linemap):
    # Criterion D2. The whole speed argument rests on this being true.
    total = len(linemap.all_tests())
    narrowed = [
        r for r in serial_results if r.status == "KILLED" and not r.mutant.module_level
    ]

    assert narrowed
    assert all(0 < r.tests_run < total for r in narrowed)


def test_module_level_mutants_deliberately_run_everything(serial_results, linemap):
    """Module-level lines run at import, so the map attributes them to no test.

    Selection widens to the whole suite for these rather than trusting an empty
    covering set, because "no covering tests" would otherwise be
    indistinguishable from "genuinely uncovered" and produce a false SURVIVED.
    Wasteful on a rare case, and the alternative is wrong-and-fast on a silent
    failure mode.
    """
    total = len(linemap.all_tests())
    module_level = [
        r for r in serial_results if r.mutant.module_level and not r.mutant.suppressed
    ]

    assert module_level
    assert all(r.tests_run == total for r in module_level)


def test_uncovered_mutant_runs_no_tests_at_all(serial_results):
    # inventory-L15-const: no covering tests, so nothing to run. It must still
    # be reported SURVIVED rather than hidden.
    uncovered = [
        r
        for r in serial_results
        if (r.mutant.module, r.mutant.line) == ("sample/inventory.py", 15)
    ]

    assert len(uncovered) == 1
    assert uncovered[0].tests_run == 0
    assert uncovered[0].status == "SURVIVED"


def test_suppressed_mutant_runs_no_tests(serial_results):
    suppressed = [r for r in serial_results if r.status == "SKIPPED"]

    assert len(suppressed) == 1
    assert suppressed[0].tests_run == 0


def test_survived_mutants_carry_a_nearest_test(serial_results):
    # Criterion E8. A survivor without a pointer to the test to extend is a
    # finding the reader cannot act on.
    survived = [r for r in serial_results if r.status == "SURVIVED" and r.tests_run]

    assert survived
    assert all(r.nearest_test and "::" in r.nearest_test for r in survived)


def test_nearest_test_actually_covers_the_mutated_line(serial_results, linemap):
    for result in serial_results:
        if result.nearest_test:
            covering = linemap.tests_covering(result.mutant.module, result.mutant.line)
            assert result.nearest_test in covering or result.mutant.module_level


def test_no_source_file_is_modified(serial_results):
    # Criterion D3. Hash every fixture source before and after the whole run.
    digests = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((FIXTURE / "sample").glob("*.py"))
    }

    assert digests == _BASELINE_DIGESTS


def test_no_stray_mutant_files_left_behind(serial_results):
    strays = [p for p in FIXTURE.rglob("*") if "mutant" in p.name.lower()]

    assert strays == []


_BASELINE_DIGESTS = {
    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted((FIXTURE / "sample").glob("*.py"))
}
