"""Regression test for #37: the cache key omitted conftest.py, so editing a
fixture served a stale verdict.

The unit tests in ``test_cache.py`` prove ``key_for`` *sees* conftest.py and
imported modules. This file proves the whole pipeline acts on it: a run whose
verdict depends on a fixture must re-run the mutant when that fixture changes,
not replay the previous answer with a reassuring ``cached=1``.

The shape mirrors ``test_pytest_args.py``'s argument-invalidation test exactly:
same code, same selection, one input that the old key could not see changing
between two runs. There, it was the command line; here, it is a fixture.
"""

import pytest
from support import assert_no_traceback, moonbuggy, status_of_mutation, write_project

pytestmark = pytest.mark.slow

# `running += value` becoming `running -= value` cannot survive a test that
# checks the sum -- but only when the fixture hands it at least one non-zero
# value. With ``[0]`` the mutant is indistinguishable from the original, so it
# survives; with ``[1, 2, 3]`` it is caught. The fixture change is the *only*
# thing that changes between the two runs.
LIB = (
    "def total(values):\n"
    "    running = 0\n"
    "    for value in values:\n"
    "        running += value\n"
    "    return running\n"
)

TEST = (
    "from lib import total\n\n"
    "def test_total(values):\n"
    "    assert total(values) == sum(values)\n"
)

MUTATION = ("running += value", "running -= value")


def _conftest(returned: str) -> str:
    return (
        "import sys\n"
        "from pathlib import Path\n"
        "import pytest\n\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n\n"
        "@pytest.fixture\n"
        f"def values():\n    return {returned}\n"
    )


def test_editing_a_conftest_fixture_does_not_serve_the_previous_run_s_verdict(tmp_path):
    """The reported bug, in miniature: edit a fixture and get a fresh verdict.

    Run one hands the mutant ``[0]``, where ``+=`` and ``-=`` both total to
    zero, and it survives. Run two hands it ``[1, 2, 3]``, where ``-=`` is
    caught -- unless a stale cache entry answers first and reports SURVIVED.
    """
    project = write_project(
        tmp_path,
        {
            "lib.py": LIB,
            "test_lib.py": TEST,
            "conftest.py": _conftest("[0]"),
        },
    )

    first = moonbuggy(cwd=project)
    assert_no_traceback(first)
    assert status_of_mutation(project, *MUTATION) == "SURVIVED", first.stderr

    # Only the fixture changes. lib.py and test_lib.py are byte-for-byte the
    # same files the first run selected.
    (project / "conftest.py").write_text(_conftest("[1, 2, 3]"))

    second = moonbuggy(cwd=project)
    assert_no_traceback(second)
    assert "cached=0" in second.stderr, (
        "the fixture changed, so none of the first run's verdicts may be "
        "reused\n" + second.stderr
    )
    assert status_of_mutation(project, *MUTATION) == "KILLED", second.stderr
