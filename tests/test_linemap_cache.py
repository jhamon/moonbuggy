"""Tests for the persisted line-map cache (H43).

The correctness floor here mirrors the cache module's: a stale hit must be
impossible, and every failure mode -- absent file, corrupt JSON, wrong
version, wrong key, wrong shape -- must degrade to "no map" (the caller then
runs the coverage pass as before), never to a wrong map.
"""

import json

import pytest

from moonbuggy.coverage_pass import LineMap
from moonbuggy.linemap_cache import LineMapCache, _is_linemap_dict


def _sample_map_dict() -> dict:
    """A to_dict-shaped payload pointing at a real file in the tree."""
    return {
        "tests": ["tests/test_a.py::test_one"],
        "lines": [
            {
                "file": "stub_pkg.py",
                "line": 2,
                "tests": ["tests/test_a.py::test_one"],
            }
        ],
    }


@pytest.fixture
def project(tmp_path):
    """A tiny project tree: one source file, one test file, a conftest."""
    (tmp_path / "stub_pkg.py").write_text("def f():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_one():\n    assert f()\n")
    (tmp_path / "conftest.py").write_text("")
    return tmp_path


def test_roundtrip_loads_stored_map(project, tmp_path_factory):
    """A map stored under this run's key comes back equivalent."""
    resolved = str((project / "stub_pkg.py").resolve())
    path = tmp_path_factory.mktemp("cache") / "linemap.json"
    cache = LineMapCache(path, fingerprint="fp")
    cache.store(
        LineMap(
            {(resolved, 2): {"tests/test_a.py::test_one"}},
            ["tests/test_a.py::test_one"],
            project,
        ),
        project,
    )
    cache.save()

    loaded = LineMapCache(path, fingerprint="fp").load(project)
    assert loaded is not None
    assert loaded.all_tests() == {"tests/test_a.py::test_one"}
    # Relative name in, resolved lookup out: store relativised the entry,
    # load re-resolved it against the current project root.
    assert loaded.tests_covering("stub_pkg.py", 2) == {"tests/test_a.py::test_one"}


def test_changed_tree_is_a_miss(project, tmp_path_factory):
    """Any byte change under the project root invalidates the stored map."""
    path = tmp_path_factory.mktemp("cache") / "linemap.json"
    cache = LineMapCache(path, fingerprint="fp")
    cache.store(LineMap({}, [], project), project)
    cache.save()

    (project / "stub_pkg.py").write_text("def f():\n    return 2\n")
    assert LineMapCache(path, fingerprint="fp").load(project) is None


def test_changed_fingerprint_is_a_miss(project, tmp_path_factory):
    """A map from one command line is not served to another."""
    path = tmp_path_factory.mktemp("cache") / "linemap.json"
    cache = LineMapCache(path, fingerprint="fp-a")
    cache.store(LineMap({}, [], project), project)
    cache.save()

    assert LineMapCache(path, fingerprint="fp-b").load(project) is None


def test_new_file_is_a_miss(project, tmp_path_factory):
    """A file added under the root after storing changes the key."""
    path = tmp_path_factory.mktemp("cache") / "linemap.json"
    cache = LineMapCache(path, fingerprint="fp")
    cache.store(LineMap({}, [], project), project)
    cache.save()

    (project / "new_module.py").write_text("x = 1\n")
    assert LineMapCache(path, fingerprint="fp").load(project) is None


def test_ignored_directories_do_not_change_the_key(project, tmp_path_factory):
    """Tool-state directories are excluded: reformatting is not an edit."""
    path = tmp_path_factory.mktemp("cache") / "linemap.json"
    cache = LineMapCache(path, fingerprint="fp")
    cache.store(LineMap({}, [], project), project)
    cache.save()

    pycache = project / "__pycache__"
    pycache.mkdir()
    (pycache / "stub_pkg.cpython-312.pyc").write_bytes(b"\x00binary")
    git = project / ".git"
    git.mkdir()
    (git / "index").write_bytes(b"\x00binary")

    assert LineMapCache(path, fingerprint="fp").load(project) is not None


def test_hidden_files_do_not_change_the_key(project, tmp_path_factory):
    """A dot-file (editor droppings, tool state) is not project input."""
    path = tmp_path_factory.mktemp("cache") / "linemap.json"
    cache = LineMapCache(path, fingerprint="fp")
    cache.store(LineMap({}, [], project), project)
    cache.save()

    (project / ".editorstate").write_text("whatever")
    assert LineMapCache(path, fingerprint="fp").load(project) is not None


def test_corrupt_file_is_a_miss(project, tmp_path_factory):
    """Unparseable JSON is a miss, not a crash."""
    path = tmp_path_factory.mktemp("cache") / "linemap.json"
    cache = LineMapCache(path, fingerprint="fp")
    cache.store(LineMap({}, [], project), project)
    cache.save()
    path.write_text("{not json")

    assert LineMapCache(path, fingerprint="fp").load(project) is None


def test_wrong_version_is_a_miss(project, tmp_path_factory):
    """An entry from a different cache version is ignored, not misread."""
    path = tmp_path_factory.mktemp("cache") / "linemap.json"
    cache = LineMapCache(path, fingerprint="fp")
    cache.store(LineMap({}, [], project), project)
    cache.save()

    payload = json.loads(path.read_text())
    payload["version"] = 99
    path.write_text(json.dumps(payload))

    assert LineMapCache(path, fingerprint="fp").load(project) is None


def test_wrong_shape_is_a_miss(project, tmp_path_factory):
    """A structurally invalid map is screened out before from_dict."""
    path = tmp_path_factory.mktemp("cache") / "linemap.json"
    cache = LineMapCache(path, fingerprint="fp")
    cache.store(LineMap({}, [], project), project)
    cache.save()

    payload = json.loads(path.read_text())
    payload["map"]["lines"] = [{"file": 3, "line": "two", "tests": "no"}]
    path.write_text(json.dumps(payload))

    assert LineMapCache(path, fingerprint="fp").load(project) is None


def test_missing_file_is_a_miss(project, tmp_path_factory):
    """No cache file at all is simply no map."""
    path = tmp_path_factory.mktemp("cache") / "linemap.json"
    assert LineMapCache(path, fingerprint="fp").load(project) is None


def test_save_without_store_writes_nothing(tmp_path):
    """A run that never built a map must not create a file."""
    path = tmp_path / "linemap.json"
    LineMapCache(path, fingerprint="fp").save()
    assert not path.exists()


def test_save_after_load_hit_rewrites_nothing(project, tmp_path_factory):
    """A served map is already exactly what this run believes: no rewrite."""
    path = tmp_path_factory.mktemp("cache") / "linemap.json"
    cache = LineMapCache(path, fingerprint="fp")
    cache.store(LineMap({}, [], project), project)
    cache.save()
    before = path.read_bytes()

    reader = LineMapCache(path, fingerprint="fp")
    loaded = reader.load(project)
    assert loaded is not None
    reader.save()
    assert path.read_bytes() == before


def test_is_linemap_dict_accepts_and_rejects():
    """The shape guard accepts exactly what from_dict will read."""
    good = _sample_map_dict()
    assert _is_linemap_dict(good)

    assert not _is_linemap_dict(None)
    assert not _is_linemap_dict({})
    assert not _is_linemap_dict({"tests": "x", "lines": []})
    assert not _is_linemap_dict({"tests": [], "lines": "x"})
    bad_line = good["lines"][0]
    for key, value in (
        ("file", 3),
        ("line", "2"),
        ("tests", "nope"),
    ):
        broken = dict(bad_line)
        broken[key] = value
        assert not _is_linemap_dict({"tests": [], "lines": [broken]})
    assert not _is_linemap_dict({"tests": [1], "lines": []})


def test_augment_with_unions_without_mutating(project):
    """The live outcome union matches read_coverage_data's rule, immutably."""
    original = LineMap({}, ["a::t1"], project)
    augmented = original.augment_with(["a::t2", "b", "c::<collection>", "a::t1"])

    assert augmented.all_tests() == {"a::t1", "a::t2"}
    assert original.all_tests() == {"a::t1"}
