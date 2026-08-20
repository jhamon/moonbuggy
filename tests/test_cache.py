"""Criterion F: the persistent results cache.

Borrowed from Hypothesis's replay database and independently from Ruff's
incremental re-linting (6.1, 6.3). The correctness risk is staleness: a cache
that serves a SURVIVED after the user added the test that kills it actively
hides the thing they just fixed. So the key covers everything the outcome
depends on, and the tests below are mostly about invalidation, not hits.
"""

import json

import pytest

from moonbuggy.cache import ResultCache, run_fingerprint
from moonbuggy.mutant import Mutant


def make_mutant(module="sample/inventory.py", line=9):
    return Mutant(
        id=f"{module}:{line}:comparison_swap:0",
        module=module,
        line=line,
        operator="comparison_swap",
        original="return stock > 0",
        mutated="return stock >= 0",
    )


@pytest.fixture
def project(tmp_path):
    (tmp_path / "sample").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "sample" / "inventory.py").write_text(
        "def is_available(stock):\n    return stock > 0\n"
    )
    (tmp_path / "sample" / "other.py").write_text("VALUE = 1\n")
    (tmp_path / "tests" / "test_inventory.py").write_text(
        "def test_x():\n    assert True\n"
    )
    return tmp_path


def key(cache, project, tests=("tests/test_inventory.py::test_x",)):
    return cache.key_for(make_mutant(), project, tests)


def test_stores_and_retrieves_a_result(project, tmp_path):
    cache = ResultCache(tmp_path / "cache.json")
    k = key(cache, project)
    cache.put(k, {"status": "SURVIVED"})

    assert cache.get(k) == {"status": "SURVIVED"}


def test_returns_none_for_an_unknown_key(project, tmp_path):
    cache = ResultCache(tmp_path / "cache.json")

    assert cache.get("nope") is None


def test_survives_a_save_and_reload(project, tmp_path):
    path = tmp_path / "cache.json"
    cache = ResultCache(path)
    k = key(cache, project)
    cache.put(k, {"status": "KILLED"})
    cache.save()

    assert ResultCache(path).get(k) == {"status": "KILLED"}


def test_editing_an_unrelated_file_keeps_the_entry_valid(project, tmp_path):
    # Criterion F2. A cache invalidated by any change in the project is a cache
    # that never hits.
    cache = ResultCache(tmp_path / "cache.json")
    before = key(cache, project)

    (project / "sample" / "other.py").write_text("VALUE = 2\n")

    assert key(cache, project) == before


def test_editing_the_mutated_module_invalidates_the_entry(project, tmp_path):
    cache = ResultCache(tmp_path / "cache.json")
    before = key(cache, project)

    (project / "sample" / "inventory.py").write_text(
        "def is_available(stock):\n    return stock >= 1\n"
    )

    assert key(cache, project) != before


def test_editing_a_covering_test_invalidates_the_entry(project, tmp_path):
    """The subtle one. If the key covered only source, adding the test that
    kills a mutant would serve the stale SURVIVED -- hiding the very gap the
    user just closed, and reporting it as an outstanding finding."""
    cache = ResultCache(tmp_path / "cache.json")
    before = key(cache, project)

    (project / "tests" / "test_inventory.py").write_text(
        "from sample.inventory import is_available\n"
        "def test_x():\n    assert is_available(0) is False\n"
    )

    assert key(cache, project) != before


def test_different_mutants_get_different_keys(project, tmp_path):
    cache = ResultCache(tmp_path / "cache.json")
    first = cache.key_for(make_mutant(line=9), project, ())
    second = cache.key_for(make_mutant(line=13), project, ())

    assert first != second


def test_clear_empties_the_cache(project, tmp_path):
    path = tmp_path / "cache.json"
    cache = ResultCache(path)
    k = key(cache, project)
    cache.put(k, {"status": "KILLED"})
    cache.save()

    ResultCache(path).clear()

    assert ResultCache(path).get(k) is None


def test_corrupt_cache_file_degrades_to_a_cold_run(tmp_path):
    """Criterion F4. A corrupt cache must cost a slow run, never a wrong result
    and never a crash -- it is a performance artifact, not a source of truth."""
    path = tmp_path / "cache.json"
    path.write_text("{not valid json at all")

    cache = ResultCache(path)

    assert cache.get("anything") is None
    cache.put("k", {"status": "KILLED"})
    cache.save()
    assert ResultCache(path).get("k") == {"status": "KILLED"}


def test_cache_file_from_a_future_version_is_ignored(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(
        json.dumps({"version": 999, "entries": {"k": {"status": "KILLED"}}})
    )

    assert ResultCache(path).get("k") is None


# --- The run fingerprint -------------------------------------------------
#
# The key above covers the code. It does not cover the command line, and the
# command line decides which tests pytest collects and whether they pass:
# `--pytest-arg=--doctest-modules` adds a whole class of tests, `-W error`
# turns a warning into a failure. Two runs with different arguments must not
# read each other's verdicts.


def test_the_fingerprint_is_optional(project, tmp_path):
    """Fingerprinting is opt-in: a library caller that never varies its run
    inputs still gets a stable key from the bare constructor."""
    plain = ResultCache(tmp_path / "a.json")
    also_plain = ResultCache(tmp_path / "b.json")

    assert key(plain, project) == key(also_plain, project)


def test_a_different_pytest_arg_invalidates_every_entry(project, tmp_path):
    bare = ResultCache(tmp_path / "a.json", fingerprint=run_fingerprint())
    doctests = ResultCache(
        tmp_path / "b.json", fingerprint=run_fingerprint(["--doctest-modules"])
    )

    assert key(bare, project) != key(doctests, project)


def test_the_same_pytest_args_keep_hitting(project, tmp_path):
    """The other half of the bargain. A key that churns is a cache that never
    hits, which is its own kind of broken."""
    first = ResultCache(
        tmp_path / "a.json", fingerprint=run_fingerprint(["-W", "error"])
    )
    second = ResultCache(
        tmp_path / "b.json", fingerprint=run_fingerprint(["-W", "error"])
    )

    assert key(first, project) == key(second, project)


def test_pytest_arg_order_is_part_of_the_fingerprint(project, tmp_path):
    """Unlike test selection, argument order is meaningful to pytest -- the
    later `-p` wins, `-W` filters apply last-match-first. So the fingerprint
    hashes the sequence, not the set."""
    forward = run_fingerprint(["-p", "no:randomly"])
    backward = run_fingerprint(["no:randomly", "-p"])

    assert forward != backward


def test_a_different_timeout_invalidates_every_entry(project, tmp_path):
    """TIMEOUT is a verdict about the clock, so the clock is an input."""
    quick = ResultCache(tmp_path / "a.json", fingerprint=run_fingerprint(timeout=5.0))
    patient = ResultCache(
        tmp_path / "b.json", fingerprint=run_fingerprint(timeout=60.0)
    )

    assert key(quick, project) != key(patient, project)


def test_a_different_interpreter_invalidates_every_entry(project, tmp_path):
    older = ResultCache(
        tmp_path / "a.json", fingerprint=run_fingerprint(python="/x/py312")
    )
    newer = ResultCache(
        tmp_path / "b.json", fingerprint=run_fingerprint(python="/x/py313")
    )

    assert key(older, project) != key(newer, project)


def test_a_cache_written_under_other_args_is_not_read_back(project, tmp_path):
    """End to end over one cache file, which is how the bug was reported: the
    second run's verdicts came from the first run's arguments."""
    path = tmp_path / "cache.json"
    first = ResultCache(path, fingerprint=run_fingerprint(["-W", "error"]))
    first.put(key(first, project), {"status": "SURVIVED"})
    first.save()

    second = ResultCache(path, fingerprint=run_fingerprint())

    assert second.get(key(second, project)) is None
