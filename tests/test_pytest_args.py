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
from support import (
    assert_no_traceback,
    moonbuggy,
    records,
    status_of_mutation,
    write_project,
)

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
    project = write_project(
        tmp_path,
        {
            "lib.py": DOCTESTED,
            # A test file has to exist or pytest collects nothing at all, which is
            # a different failure. It deliberately asserts nothing about `double`.
            "test_placeholder.py": "def test_placeholder():\n    assert True\n",
        },
    )

    proc = moonbuggy("--pytest-arg=--doctest-modules", cwd=project)

    assert_no_traceback(proc)
    assert proc.returncode in (0, 1), proc.stderr

    statuses = {r["status"] for r in records(project)}
    assert "SUSPICIOUS" not in statuses, (
        "a mutant was SUSPICIOUS, which is what happens when the mutant run is "
        "asked to select a node id its own arguments cannot produce\n" + proc.stderr
    )
    assert (
        status_of_mutation(project, "return value * 2", "return value / 2") == "KILLED"
    ), "the doctest catches this; the mutant run must have run it"


def test_without_the_flag_the_doctest_is_simply_not_part_of_the_suite(tmp_path):
    """The cost of not passing it, recorded rather than implied.

    Not a bug -- moonbuggy measures the suite it is given. But it is the reason
    a finding is only as meaningful as the test command it survived, and why
    `--pytest-arg` exists at all.

    The status is NO_COVERAGE rather than SURVIVED, and that is the point put
    plainly: without `--doctest-modules` the doctest is not a test, so nothing
    in the suite executes this line and moonbuggy says exactly that.
    """
    project = write_project(
        tmp_path,
        {
            "lib.py": DOCTESTED,
            "test_placeholder.py": "def test_placeholder():\n    assert True\n",
        },
    )

    moonbuggy(cwd=project)

    assert (
        status_of_mutation(project, "return value * 2", "return value / 2")
        == "NO_COVERAGE"
    )


# A module whose mutation is invisible to the suite as it stands, and fatal
# under one extra pytest argument. `double` warns when its own arithmetic is
# inconsistent, which only a mutant makes it do; the test calls it and asserts
# nothing, so a warning passes and an error does not.
#
# The scenario matters because the *selection* is identical either way: the
# same one test file, unedited, covering the same line. Nothing about the code
# changes between the two runs, so nothing but the command line can invalidate
# the cache entry -- which is precisely the case the key used to miss.
WARNING_LIB = """\
import warnings


def double(value):
    result = value * 2
    if result != value + value:
        warnings.warn("doubling is inconsistent", UserWarning)
    return result
"""

WARNING_TEST = """\
from lib import double


def test_double_runs():
    double(3)
"""

MUTATION = ("result = value * 2", "result = value / 2")


def test_changing_pytest_args_does_not_serve_the_previous_run_s_verdicts(tmp_path):
    """The reported bug, in miniature.

    A user runs, gets a survivor, changes the arguments so that the suite can
    actually catch it, reruns -- and is handed the first run's SURVIVED, with
    only a suspiciously high `cached=` count to hint at it. The second run must
    re-run the mutant and report KILLED.
    """
    project = write_project(
        tmp_path, {"lib.py": WARNING_LIB, "test_lib.py": WARNING_TEST}
    )

    first = moonbuggy(cwd=project)
    assert_no_traceback(first)
    assert status_of_mutation(project, *MUTATION) == "SURVIVED"

    # Two values rather than one token, so argument order is exercised too.
    second = moonbuggy(
        "--pytest-arg=-W", "--pytest-arg=error::UserWarning", cwd=project
    )
    assert_no_traceback(second)
    assert "cached=0" in second.stderr, (
        "the second run's arguments differ, so none of the first run's "
        "verdicts may be reused\n" + second.stderr
    )
    assert status_of_mutation(project, *MUTATION) == "KILLED", (
        "the warning is an error under these arguments, so the test fails and "
        "the mutant dies -- unless a stale cache entry answered first"
    )


def test_an_unchanged_command_line_still_hits_the_cache(tmp_path):
    """The other half. Invalidating on arguments must not invalidate on
    nothing: a rerun of the same command still skips the work."""
    project = write_project(
        tmp_path, {"lib.py": WARNING_LIB, "test_lib.py": WARNING_TEST}
    )
    args = ("--pytest-arg=-W", "--pytest-arg=error::UserWarning")

    first = moonbuggy(*args, cwd=project)
    assert "cached=0" in first.stderr, first.stderr

    second = moonbuggy(*args, cwd=project)

    cached = int(second.stderr.split("cached=")[1].split()[0])
    assert cached > 0, second.stderr
