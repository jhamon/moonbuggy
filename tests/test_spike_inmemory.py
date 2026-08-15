"""Spike A -- criteria B1 and B2. The highest-risk piece in the design (4.2).

Two questions this has to answer before anything is built on top of it:

B1  Does in-memory mutation coexist with pytest's assert-rewriting import hook,
    or does one displace the other? Checked in a single run: the mutation must
    be observed AND pytest's rewritten assertion output must still appear. Only
    checking the mutation would pass just as well with rewriting broken.

B2  Do pytest-xdist workers execute the mutated code? Workers are separate
    processes that re-import from disk, so a worker running unmutated code
    reports a false SURVIVED -- silent, and on a common configuration. The
    xdist test here is paired with a negative test that disables propagation
    and asserts the mutant survives, which is what proves the positive test
    would actually catch the regression rather than passing for its own reasons.

These run pytest as a subprocess, because that is the only way to observe a
real worker process and a real import sequence.
"""

import json
import shutil
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "sample_project"

# discounts-L11-cmp: the cross-file mutant. Killed only by test_pricing.py.
MUTANT = {
    "path": str(FIXTURE / "sample" / "discounts.py"),
    "line": 11,
    "mutated": "return quantity > BULK_THRESHOLD",
}

pytestmark = pytest.mark.slow


def run_fixture_suite(*extra_args, env_extra=None):
    env = dict(os.environ)
    env["MOONBUGGY_MUTANT"] = json.dumps(MUTANT)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "moonbuggy.plugin", *extra_args],
        cwd=FIXTURE, capture_output=True, text=True, timeout=120, env=env,
    )


def test_mutation_is_observed_by_the_test_run():
    result = run_fixture_suite()

    assert result.returncode == 1, result.stdout
    assert "test_threshold_exactly_qualifies" in result.stdout


def test_assert_rewriting_still_produces_rich_output_under_mutation():
    # B1. `+ where False = qualifies_for_bulk(10)` is produced only by pytest's
    # assert rewriting -- with --assert=plain the failure is a bare
    # AssertionError. Asserting on it proves both hooks ran in the same process.
    result = run_fixture_suite()

    assert "assert False is True" in result.stdout, result.stdout
    assert "where False = qualifies_for_bulk(10)" in result.stdout, result.stdout


def test_unmutated_run_is_green():
    # Control. Without the mutant the suite passes, so the failure above is the
    # mutation talking and not the plugin breaking the run.
    env = dict(os.environ)
    env.pop("MOONBUGGY_MUTANT", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "moonbuggy.plugin"],
        cwd=FIXTURE, capture_output=True, text=True, timeout=120, env=env,
    )

    assert result.returncode == 0, result.stdout


def test_mutation_reaches_xdist_workers():
    # B2. Workers are separate processes that import from disk independently.
    result = run_fixture_suite("-n", "2")

    assert result.returncode == 1, result.stdout
    assert "test_threshold_exactly_qualifies" in result.stdout


def test_xdist_test_has_teeth():
    # The negative half of B2. With propagation to workers disabled, the very
    # same run must report the mutant as surviving. If this ever starts failing,
    # test_mutation_reaches_xdist_workers is passing for the wrong reason and
    # no longer detects the failure mode it exists to catch.
    result = run_fixture_suite("-n", "2", env_extra={"MOONBUGGY_SPIKE_CONTROLLER_ONLY": "1"})

    assert result.returncode == 0, (
        "Expected the mutant to survive with worker propagation disabled. "
        "It was killed anyway, so the xdist test proves nothing.\n" + result.stdout
    )


def test_mutated_bytecode_is_never_cached_to_disk():
    """The nastiest failure mode found so far.

    SourceFileLoader writes compiled bytecode to __pycache__ as a side effect of
    importing. Inheriting from it means a mutated module leaves a mutated .pyc
    behind, and every LATER run -- moonbuggy's, the naive oracle's, or the
    user's own plain `pytest` -- silently executes the mutation.

    The source files stay byte-identical throughout, so hashing .py files (the
    obvious reading of criterion D3) does not catch it. The check that does is
    running the untouched suite afterwards and requiring it green.
    """
    cache_dir = FIXTURE / "sample" / "__pycache__"
    shutil.rmtree(cache_dir, ignore_errors=True)

    run_fixture_suite()

    # The mutated module must leave no compiled artefact at all. Checking for
    # absence is the precise test: a .pyc that exists is stamped with the real
    # file's mtime and size, so it will be treated as valid for the unmutated
    # source by every later import.
    written = list(cache_dir.glob("discounts*.pyc")) if cache_dir.exists() else []
    assert written == [], f"mutated bytecode cached to disk: {written}"

    clean = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=FIXTURE, capture_output=True, text=True, timeout=120,
        env={k: v for k, v in os.environ.items() if k != "MOONBUGGY_MUTANT"},
    )

    assert clean.returncode == 0, (
        "The fixture suite fails after a mutated run, which means mutated "
        "bytecode was left in __pycache__:\n" + clean.stdout
    )


def test_source_file_on_disk_is_untouched():
    # Criterion D3. In-memory means in-memory.
    run_fixture_suite()

    assert "return quantity >= BULK_THRESHOLD" in (
        FIXTURE / "sample" / "discounts.py"
    ).read_text()
