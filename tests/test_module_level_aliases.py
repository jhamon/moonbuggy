"""Regression tests for the false SURVIVEDs the M4 hand verification found.

`scripts/verify_findings.py` applies a reported survivor to the real checkout
and runs the project's *entire* suite. Two of twenty came back **refuted**: the
suite failed, so something did catch the mutation, so moonbuggy's `SURVIVED`
was wrong. Both were in more-itertools:

    def tabulate(function, start=0):   ->  start=1
    def unique(iterable, key=None, reverse=False):  ->  reverse=True

Both are `def` lines, so the mutation lands in a default argument, which is
evaluated at import time. The warm path applies an import-time mutation by
re-executing that statement in the module's namespace — and that rebinds the
name **in that module only**. `more_itertools/__init__.py` does
`from .recipes import *`, so `more_itertools.tabulate` still pointed at the
original function object, and every test that used it tested unmutated code.

This is the exact failure mode the whole design is organised around: not a
crash, not a refusal, but a confident `SURVIVED` that looks like a finding.

Two mechanisms are tested below because there are two shapes of it: a
rebound function, and a rebound constant. A constant is the harder one --
`from m import LIMIT` copies the value, and nothing about the copy says where
it came from.
"""

import pytest

from support import assert_no_traceback, moonbuggy, status_of_mutation, write_project

pytestmark = pytest.mark.slow


def test_a_mutated_default_argument_is_seen_through_a_re_export(tmp_path):
    """The more-itertools case, reduced.

    `pkg/__init__.py` re-exports the function, the test imports it from there,
    and the mutation is in a default argument on the `def` line.
    """
    project = write_project(tmp_path, {
        "pkg/__init__.py": "from pkg.core import tabulate\n",
        "pkg/core.py": (
            "def tabulate(function, start=0):\n"
            "    return [function(n) for n in range(start, start + 3)]\n"
        ),
        "test_pkg.py": (
            "import pkg\n\n"
            "def test_tabulate_starts_at_zero():\n"
            "    assert pkg.tabulate(lambda n: n) == [0, 1, 2]\n"
        ),
    })

    proc = moonbuggy(cwd=project)

    assert_no_traceback(proc)
    assert proc.returncode in (0, 1), proc.stderr
    assert status_of_mutation(
        project, "def tabulate(function, start=0):", "def tabulate(function, start=1):"
    ) == "KILLED", (
        "the mutation was applied by rebinding the name in pkg.core, but the "
        "test calls pkg.tabulate, which still points at the original function"
    )


def test_a_mutated_module_level_constant_is_seen_through_a_from_import(tmp_path):
    """The same failure with a value rather than a function.

    `from pkg.core import LIMIT` copies the value at import time, so rebinding
    `pkg.core.LIMIT` afterwards changes nothing the test can see.
    """
    project = write_project(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/core.py": (
            "LIMIT = 3 + 4\n"
            "\n\n"
            "def headroom(used):\n"
            "    return LIMIT - used\n"
        ),
        "test_pkg.py": (
            "from pkg.core import LIMIT\n\n"
            "def test_limit_is_seven():\n"
            "    assert LIMIT == 7\n"
        ),
    })

    proc = moonbuggy(cwd=project)

    assert_no_traceback(proc)
    assert proc.returncode in (0, 1), proc.stderr
    assert status_of_mutation(project, "LIMIT = 3 + 4", "LIMIT = 3 - 4") == "KILLED", (
        "the test holds its own reference to LIMIT, taken at import time, so "
        "rebinding it in pkg.core alone leaves the test checking the original"
    )


def test_a_decorated_function_is_not_silently_half_mutated(tmp_path):
    """A decorator that returns a different object cannot be swapped safely.

    The honest outcomes are KILLED (the fallback path applied it) or
    SUSPICIOUS (moonbuggy could not tell). SURVIVED would mean claiming the
    mutation had no effect when it was never applied.
    """
    project = write_project(tmp_path, {
        "deco.py": (
            "import functools\n\n\n"
            "def logged(fn):\n"
            "    @functools.wraps(fn)\n"
            "    def wrapper(*args, **kwargs):\n"
            "        return fn(*args, **kwargs)\n"
            "    return wrapper\n"
            "\n\n"
            "@logged\n"
            "def scaled(value, factor=2):\n"
            "    return value * factor\n"
        ),
        "test_deco.py": (
            "from deco import scaled\n\n"
            "def test_scaled():\n    assert scaled(3) == 6\n"
        ),
    })

    proc = moonbuggy(cwd=project)

    assert_no_traceback(proc)
    assert status_of_mutation(
        project, "def scaled(value, factor=2):", "def scaled(value, factor=3):"
    ) in {"KILLED", "SUSPICIOUS"}
