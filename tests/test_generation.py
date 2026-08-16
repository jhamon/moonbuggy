"""Mutant generation against the fixture, checked against the hand-written oracle.

This is the criterion-A2b check: the differential oracle cannot catch a mutant
that is never generated, because both it and the fast path consume these same
operators. Only the hand-written inventory closes that gap, so this test is the
one that makes a missing operator visible.
"""

import tomllib
from pathlib import Path

import pytest

from moonbuggy.generate import generate_mutants

FIXTURE = Path(__file__).parent / "fixtures" / "sample_project"
ORACLE = tomllib.loads((Path(__file__).parent / "fixtures" / "oracle.toml").read_text())
MODULES = [
    "sample/discounts.py",
    "sample/inventory.py",
    "sample/loops.py",
    "sample/config.py",
]


def generate_all():
    mutants = []
    for module in MODULES:
        source = (FIXTURE / module).read_text()
        mutants.extend(generate_mutants(source, module=module))
    return mutants


def test_generated_inventory_matches_oracle():
    generated = {(m.module, m.line, m.operator) for m in generate_all()}
    expected = {(m["module"], m["line"], m["operator"]) for m in ORACLE["mutant"]}

    assert generated == expected


def test_generated_count_matches_oracle_meta():
    assert len(generate_all()) == ORACLE["meta"]["total_mutants"]


@pytest.mark.parametrize("expected", ORACLE["mutant"], ids=lambda m: m["id"])
def test_each_oracle_mutant_is_generated_with_expected_text(expected):
    matches = [
        m
        for m in generate_all()
        if (m.module, m.line, m.operator)
        == (expected["module"], expected["line"], expected["operator"])
    ]

    assert len(matches) == 1, f"expected exactly one mutant, got {len(matches)}"
    assert expected["original"] in matches[0].original
    assert expected["mutated"] in matches[0].mutated


def test_mutant_ids_are_unique():
    ids = [m.id for m in generate_all()]

    assert len(set(ids)) == len(ids)


def test_mutant_ids_are_stable_across_runs():
    # Criterion C3. Re-generating from unchanged source must yield identical ids,
    # or the persistent results cache (F2) keys on a moving target.
    assert [m.id for m in generate_all()] == [m.id for m in generate_all()]


def test_suppression_marker_is_detected():
    # config-L11-const carries an inline `# moonbuggy: skip`.
    suppressed = [m for m in generate_all() if m.suppressed]

    assert len(suppressed) == 1
    assert (suppressed[0].module, suppressed[0].line) == ("sample/config.py", 11)


def test_suppressed_mutant_is_still_generated():
    # It must be reported SKIPPED, not silently omitted -- otherwise the mutant
    # count drifts from the inventory with no way to tell suppression from a
    # generator bug.
    assert any(m.suppressed for m in generate_all())


def test_module_level_mutants_are_flagged():
    """Module-level lines execute at import, not inside any test body.

    Coverage-guided selection therefore cannot find their covering tests the
    normal way -- the line->test map attributes them to nothing, and running
    nothing yields a false SURVIVED. Selection needs to know which mutants are
    module-scoped so it can widen the test set for them, so generation has to
    say so.
    """
    module_level = {
        (m.module, m.line) for m in generate_all() if m.module_level
    }

    # Exactly the oracle's two module-level cases, plus the suppressed CACHE_SIZE
    # line which is also module-level.
    assert module_level == {
        ("sample/discounts.py", 7),
        ("sample/config.py", 9),
        ("sample/config.py", 11),
    }


def test_function_body_mutants_are_not_flagged_module_level():
    inside_functions = [
        m for m in generate_all() if m.module == "sample/loops.py"
    ]

    assert inside_functions
    assert not any(m.module_level for m in inside_functions)


def test_class_body_counts_as_module_level():
    # A class body also executes at import time, so it has the same problem.
    source = "class Config:\n    RETRIES = 3\n"

    mutants = generate_mutants(source, module="m.py")

    assert [m.module_level for m in mutants] == [True]


def test_strings_are_never_mutated():
    # Criterion C2. The string contains text that looks mutable in every operator.
    source = "MESSAGE = 'use x >= 1 and y + 2 for range(n)'\n"

    assert generate_mutants(source, module="m.py") == []
