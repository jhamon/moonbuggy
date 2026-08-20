"""Criterion A2a: the naive differential oracle, checked against A2b's labels.

This runs the reference implementation -- write the mutant to disk, run the
whole suite under plain pytest -- over the fixture and compares every result to
the hand-written label. Two independently-derived answers agreeing is what makes
the correctness claims in section D worth anything.

Slow by construction: one full pytest process per mutant, including one that
has to hit the timeout. Marked `slow` and excluded from the default run; the
evaluator reaches it through `make check-oracle`.
"""

import tomllib
from pathlib import Path

import pytest

from moonbuggy.generate import generate_mutants
from moonbuggy.naive import run_naive

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "sample_project"
ORACLE = tomllib.loads((FIXTURES / "oracle.toml").read_text())
MODULES = [
    "sample/discounts.py",
    "sample/inventory.py",
    "sample/loops.py",
    "sample/config.py",
    "sample/predicates.py",
]

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def naive_results():
    mutants = []
    for module in MODULES:
        mutants.extend(generate_mutants((FIXTURE / module).read_text(), module=module))
    return run_naive(FIXTURE, mutants, timeout=8)


def test_naive_oracle_agrees_with_hand_written_labels(naive_results):
    by_site = {(m["module"], m["line"], m["operator"]): m for m in ORACLE["mutant"]}

    disagreements = []
    for mutant, status in naive_results.items():
        expected = by_site[(mutant.module, mutant.line, mutant.operator)]
        if status != expected["status"]:
            disagreements.append(
                f"{expected['id']}: hand-labelled {expected['status']}, "
                f"naive oracle says {status}"
            )

    assert not disagreements, "\n".join(disagreements)


def test_naive_oracle_covers_every_mutant(naive_results):
    assert len(naive_results) == ORACLE["meta"]["total_mutants"]


def test_status_counts_match_oracle_meta(naive_results):
    # `expected_counts`, not `expected_counts_fast`: the naive runner has no
    # coverage map, so NO_COVERAGE is a distinction it cannot draw.
    counts = {}
    for status in naive_results.values():
        counts[status] = counts.get(status, 0) + 1

    assert counts == ORACLE["meta"]["expected_counts"]


def test_suppressed_mutant_is_skipped_without_running(naive_results):
    skipped = [m for m, s in naive_results.items() if s == "SKIPPED"]

    assert len(skipped) == 1
    assert (skipped[0].module, skipped[0].line) == ("sample/config.py", 11)


def test_timeout_mutant_is_reported_and_run_continues(naive_results):
    # The hang must not take down the run: every other mutant still has a result.
    timed_out = [m for m, s in naive_results.items() if s == "TIMEOUT"]

    assert len(timed_out) == 1
    assert (timed_out[0].module, timed_out[0].line) == ("sample/loops.py", 12)
    assert len(naive_results) == ORACLE["meta"]["total_mutants"]


def test_fixture_source_is_not_modified(naive_results):
    # The naive runner works on a copy; the real tree must be untouched (D3).
    assert "n -= 1" in (FIXTURE / "sample/loops.py").read_text()
    assert "BULK_THRESHOLD = 10" in (FIXTURE / "sample/discounts.py").read_text()
