"""Regression test for the third defect the M4 hunt exposed.

boltons' real test command is `pytest --doctest-modules boltons tests`, and a
great deal of its behaviour is asserted in docstring examples rather than in
`tests/`. Run with bare `pytest`, moonbuggy measured it against a suite the
project does not use, and reported survivors its own CI catches.

`--pytest-arg` fixes the measurement, and then exposed a second, sharper
problem: the flag reached the baseline run but not the mutant runs. The
coverage map therefore contained doctest node ids that the mutant runs could
not select -- pytest exits with a usage error for an unknown node id, which is
not a clean kill, so it becomes SUSPICIOUS. 315 of boltons' 434 mutants came
back that way, reading as a property of boltons rather than a missing argument
in our own command line.

The test below is the whole thing in miniature: a module whose only test is a
doctest. Without the argument reaching every run, no mutant can be killed.
"""

import pytest

from support import assert_no_traceback, moonbuggy, records, status_of_mutation, write_project

pytestmark = pytest.mark.slow

DOCTESTED = '''\
def double(value):
    """Return twice `value`.

    >>> double(3)
    6
    """
    return value * 2
'''


def test_extra_pytest_args_reach_the_mutant_runs_not_just_the_baseline(tmp_path):
    project = write_project(tmp_path, {
        "lib.py": DOCTESTED,
        # A test file has to exist or pytest collects nothing at all, which is
        # a different failure. It deliberately asserts nothing about `double`.
        "test_placeholder.py": "def test_placeholder():\n    assert True\n",
    })

    proc = moonbuggy("--pytest-arg=--doctest-modules", cwd=project)

    assert_no_traceback(proc)
    assert proc.returncode in (0, 1), proc.stderr

    statuses = {r["status"] for r in records(project)}
    assert "SUSPICIOUS" not in statuses, (
        "a mutant was SUSPICIOUS, which is what happens when the mutant run is "
        "asked to select a node id its own arguments cannot produce\n" + proc.stderr
    )
    assert status_of_mutation(
        project, "return value * 2", "return value / 2"
    ) == "KILLED", "the doctest catches this; the mutant run must have run it"


def test_without_the_flag_the_doctest_is_simply_not_part_of_the_suite(tmp_path):
    """The cost of not passing it, recorded rather than implied.

    Not a bug -- moonbuggy measures the suite it is given. But it is the reason
    a survivor is only as meaningful as the test command it survived, and why
    `--pytest-arg` exists at all.
    """
    project = write_project(tmp_path, {
        "lib.py": DOCTESTED,
        "test_placeholder.py": "def test_placeholder():\n    assert True\n",
    })

    moonbuggy(cwd=project)

    assert status_of_mutation(project, "return value * 2", "return value / 2") == "SURVIVED"
