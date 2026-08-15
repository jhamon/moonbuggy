"""Criterion F: the persistent results cache.

Borrowed from Hypothesis's replay database and independently from Ruff's
incremental re-linting (6.1, 6.3). The correctness risk is staleness: a cache
that serves a SURVIVED after the user added the test that kills it actively
hides the thing they just fixed. So the key covers everything the outcome
depends on, and the tests below are mostly about invalidation, not hits.
"""

import json

import pytest

from moonbuggy.cache import ResultCache
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
    (tmp_path / "sample" / "inventory.py").write_text("def is_available(stock):\n    return stock > 0\n")
    (tmp_path / "sample" / "other.py").write_text("VALUE = 1\n")
    (tmp_path / "tests" / "test_inventory.py").write_text("def test_x():\n    assert True\n")
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

    (project / "sample" / "inventory.py").write_text("def is_available(stock):\n    return stock >= 1\n")

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
    path.write_text(json.dumps({"version": 999, "entries": {"k": {"status": "KILLED"}}}))

    assert ResultCache(path).get("k") is None
