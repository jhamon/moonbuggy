"""Regression tests for the defect the M4 open-source hunt found.

Three of the five libraries came back with almost every mutant `SUSPICIOUS` and
no explanation. The cause was not in any of them.

pytest computes node ids relative to its **rootdir**, and rootdir is inferred by
walking upwards until it finds a config file. A project checked out inside
another project that has one — a monorepo, a vendored dependency, or in this
case five clones that were placed inside moonbuggy's own repository — gets node
ids relative to the *outer* directory:

    .oss-hunt/tomli/tests/test_data.py::TestData::test_invalid

moonbuggy recorded those ids in the coverage map and handed them straight back
to pytest from the project root, where that path does not exist. pytest exited
with a usage error, which is not a clean kill, so every mutant was reported
`SUSPICIOUS` — 233 of them for tomli, 322 for more-itertools — and nothing said
why.

Two fixes, and both are tested here because they fail differently:

1. rootdir is pinned to the project being mutated, so the ids are relative to
   it and resolvable from it. This makes the case work.
2. selection verifies that the tests it is about to run actually exist, and
   refuses with a message naming one if they do not. This makes any *remaining*
   case of this shape say so, rather than degrading into a wall of SUSPICIOUS
   that reads like a property of the code under test.
"""

import pytest

from support import assert_no_traceback, moonbuggy, records, write_project

pytestmark = pytest.mark.slow

# Chosen so that at least one mutant is definitely killable: `running += value`
# becoming `running -= value` cannot survive a test that checks the sum. A
# module whose every mutant legitimately survives would make the assertion
# below pass for the wrong reason.
CALC = (
    "def total(values):\n"
    "    running = 0\n"
    "    for value in values:\n"
    "        running += value\n"
    "    return running\n"
)
CALC_TESTS = (
    "from calc import total\n\n"
    "def test_sums():\n    assert total([1, 2, 3]) == 6\n\n"
    "def test_empty():\n    assert total([]) == 0\n"
)


def test_a_project_nested_inside_another_still_gets_confident_statuses(tmp_path):
    """The M4 defect, reduced to its smallest reproduction.

    The outer directory carries a pyproject.toml with a pytest section, which
    is all it takes for pytest to treat it as the rootdir for everything
    beneath it. That is a completely ordinary thing for a repository to
    contain.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        "addopts = \"\"\n"
    )
    inner = write_project(tmp_path / "inner", {
        "calc.py": CALC,
        "test_calc.py": CALC_TESTS,
    })
    # No pytest.ini in the inner project: that is what sends rootdir upwards.
    (inner / "pytest.ini").unlink()

    proc = moonbuggy(cwd=inner)

    assert_no_traceback(proc)
    assert proc.returncode in (0, 1), proc.stderr

    statuses = [r["status"] for r in records(inner)]
    assert statuses, "no mutants were reported"
    # The precise breakdown is not the point. The point is that moonbuggy
    # reached a confident answer at all: before the fix every one of these was
    # SUSPICIOUS, because every selected node id was unresolvable.
    assert set(statuses) != {"SUSPICIOUS"}, (
        "every mutant is SUSPICIOUS, which is the M4 defect: selection handed "
        "pytest node ids it could not resolve\n" + proc.stderr
    )
    assert "KILLED" in statuses


def test_unresolvable_selected_tests_are_refused_with_a_message(tmp_path):
    """The second half of the fix: say so rather than degrade quietly.

    A wall of SUSPICIOUS is technically "degrading visibly" and practically
    useless — it looks like a property of the code under test, and the M4 run
    is the evidence that it reads that way even to the people who wrote it.
    """
    from moonbuggy.baseline import BaselineError
    from moonbuggy.runner import check_selection_is_runnable

    project = write_project(tmp_path, {"calc.py": CALC, "test_calc.py": CALC_TESTS})

    # Resolvable: no complaint.
    check_selection_is_runnable(project, {"test_calc.py::test_low"})

    with pytest.raises(BaselineError) as raised:
        check_selection_is_runnable(
            project, {"nowhere/test_calc.py::test_low", "test_calc.py::test_high"}
        )

    message = str(raised.value)
    assert "nowhere/test_calc.py" in message
    assert "rootdir" in message
