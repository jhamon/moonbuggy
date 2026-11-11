"""Persistent line-to-covering-tests map, keyed like the results cache.

Hypothesis H43 (registered in docs/development/perf-hypotheses.md): the
coverage pass is the largest phase of every shape, 30-43% of the wall, and it
runs even when every mutant's verdict is already in the results cache -- on
warm-cache reruns the map is consulted only to compute cache keys. The map is
one more derived artifact of the inputs the results key already hashes, so it
can be persisted under the same discipline:

- the key covers the whole tree moonbuggy reads (source, tests, conftest
  chain, pytest configuration) plus the run fingerprint, computed once per
  run rather than once per mutant,
- whole-file, deliberately-coarse hashing, like the results key,
- and a stale hit here is *worse* than in the results cache: a map that
  loses a covering test is a false SURVIVED -- H2's failure mode. So the key
  errs wide: extra inputs cost a re-run of the coverage pass, missing inputs
  cost wrong verdicts.

Two no-code probes (2026-11-06, recorded in the hypothesis register) hold up
the premise: identical input bytes produce byte-identical LineMaps
(``to_dict`` sha256-compared, 4/4 runs on fast-tests, 3/3 on many-files), and
the every-mutant-cached warm run is routinely reached.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .coverage_pass import LineMap, LineMapDict
else:
    LineMapDict = None  # runtime placeholder; only typing uses the name

# Bumped whenever the key derivation or the stored shape changes, like
# CACHE_VERSION in cache.py: an old map is ignored, not misread.
LINEMAP_CACHE_VERSION = 1

# Directories whose bytes can change without changing anything the map
# depends on: version-control bookkeeping, interpreter and tool caches, and
# moonbuggy's own output (which this cache itself lives under -- hashing it
# would make the key depend on the value). Everything else under the project
# root is hashed: source, tests, conftest.py, and the pytest configuration
# files the results key explicitly does not cover -- here over-covering is
# the cheap direction to be wrong in.
_SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".hypothesis",
        ".tox",
        ".venv",
        "venv",
        "node_modules",
        ".moonbuggy",
        "build",
        "dist",
    }
)


class LineMapCache:
    """A persisted :class:`~moonbuggy.coverage_pass.LineMap`, or nothing.

    The on-disk shape is one JSON object::

        {"version": 1, "key": <hex digest>, "map": <LineMapDict>}

    -- one entry, because every run of a given tree needs exactly one map.
    If the stored key does not match this run's key, something changed under
    the project root, and the map is not this run's map: it is discarded and
    the caller runs the coverage pass as before. The failure asymmetry runs
    that way on purpose -- a wrongly discarded map costs one instrumented
    run; a wrongly served one can cost a false SURVIVED.
    """

    def __init__(self, path: str | os.PathLike[str], fingerprint: str = "") -> None:
        """Open the map cache at `path`.

        Args:
            path: the cache file. It need not exist yet.
            fingerprint: a :func:`moonbuggy.cache.run_fingerprint` digest
                for this run, mixed into the key.
        """
        self.path = Path(path)
        self._fingerprint = fingerprint
        self._key: str | None = None
        self._map: LineMapDict | None = None

    def load(self, project_dir: str | os.PathLike[str]) -> "LineMap | None":
        """The stored map for this run's inputs, or None.

        Computes the key from the project tree and the run fingerprint and
        serves the stored map only on an exact key match. Any mismatch,
        corruption, or absence returns None; the caller then cannot tell --
        and does not need to tell -- a stale map from a missing one.

        Args:
            project_dir: the project root, whose tree is hashed and against
                which the returned map resolves module paths.

        Returns:
            The stored :class:`~moonbuggy.coverage_pass.LineMap`, or None
                when no valid map exists for this run's inputs.
        """
        from .coverage_pass import LineMap

        key = self.key_for(project_dir)
        if key is None:
            return None
        try:
            payload = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != (
            LINEMAP_CACHE_VERSION
        ):
            return None
        stored = payload.get("key")
        map_dict = payload.get("map")
        if not isinstance(stored, str) or not _is_linemap_dict(map_dict):
            return None
        if stored != key:
            return None
        validated = cast("LineMapDict", map_dict)
        self._key = key
        self._map = validated
        return LineMap.from_dict(validated, project_dir)

    def key_for(self, project_dir: str | os.PathLike[str]) -> str | None:
        """The map key for this run, or None if the tree could not be read.

        Hashes every file under `project_dir` outside the skipped
        directories, as path-plus-bytes so a file that moves, appears, or
        disappears changes the key, prefixed with the cache's own version
        tag so a derivation change cannot alias an old key. The cache file
        itself is excluded by name: it lives under the project root (in
        ``.moonbuggy/`` by default, but nowhere is that guaranteed), and a
        key that included its own value could never equal itself.

        Args:
            project_dir: the project root to hash.

        Returns:
            The hex digest, or None when the tree could not be listed.
        """
        root = Path(project_dir)
        digest = hashlib.sha256()
        digest.update(b"moonbuggy-linemap-v1")
        digest.update(self._fingerprint.encode())
        own_name = self.path.name
        try:
            names = sorted(
                name for name in _tree_files(root) if Path(name).name != own_name
            )
        except OSError:
            return None
        for name in names:
            digest.update(name.encode())
            try:
                digest.update((root / name).read_bytes())
            except OSError:
                # Vanished between listing and reading: not an error, a
                # different tree. Hash it as such rather than failing open.
                digest.update(b"\0missing\0")
        return digest.hexdigest()

    def store(self, linemap: "LineMap", project_dir: str | os.PathLike[str]) -> None:
        """Remember `linemap` under this run's key. Written on ``save``.

        The map's file entries are resolved absolute paths (that is what
        ``LineMap`` keys on), so they are stored project-relative: the cache
        file lives inside the project, and a map that names
        ``/Users/someone/elsewhere/pkg.py`` would survive a clone-to-a-new-
        path move only to resolve nothing. The key changes on such a move
        anyway -- the fingerprint does not cover the path, the tree bytes
        are the same but the map file's own location is not hashed -- so
        this is portability insurance under the same key, not a correctness
        dependence. ``load`` re-resolves against the current root.

        Args:
            linemap: the map the coverage pass just built.
            project_dir: the project root, for the key. The same root
                ``load`` was called with on a miss; a caller that never
                called ``load`` passes the root it would have passed.
        """
        self._key = self.key_for(project_dir)
        if self._key is None:
            self._map = None
            return
        root = str(Path(project_dir).resolve())
        data = linemap.to_dict()
        self._map = {
            "tests": data["tests"],
            "lines": [
                {
                    "file": entry["file"][len(root) + 1 :]
                    if entry["file"].startswith(root + os.sep)
                    else entry["file"],
                    "line": entry["line"],
                    "tests": entry["tests"],
                }
                for entry in data["lines"]
            ],
        }

    def save(self) -> None:
        """Persist the stored map, atomically, if this run built one.

        A run that served a stored map wrote nothing: the bytes on disk
        already say what this run believes. A run that built a fresh map
        writes it under the key computed in ``store``.
        """
        if self._key is None or self._map is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": LINEMAP_CACHE_VERSION,
                    "key": self._key,
                    "map": self._map,
                },
                sort_keys=True,
            )
        )
        os.replace(temporary, self.path)


def _is_linemap_dict(value: object) -> "bool":
    """Whether `value` is a :meth:`LineMap.to_dict`-shaped mapping.

    Args:
        value: the parsed JSON value to check.

    Returns:
        True only when every field the :meth:`LineMap.from_dict` reader will
        touch is present with the right type. Guards the cast the JSON
        loader cannot make itself: a truncated or hand-edited cache file is
        a miss, never a crash.
    """
    if not isinstance(value, dict):
        return False
    tests = value.get("tests")
    lines = value.get("lines")
    if not isinstance(tests, list) or not isinstance(lines, list):
        return False
    return all(isinstance(test, str) for test in tests) and all(
        isinstance(entry, dict)
        and isinstance(entry.get("file"), str)
        and isinstance(entry.get("line"), int)
        and isinstance(entry.get("tests"), list)
        and all(isinstance(t, str) for t in entry["tests"])
        for entry in lines
    )


def _tree_files(root: Path) -> "list[str]":
    """Project-relative paths of every file under `root` worth hashing.

    Args:
        root: the project root.

    Returns:
        Sorted POSIX-style relative paths. Symlinks to files are followed
        as files; directories named in ``_SKIPPED_DIRECTORIES`` are pruned
        whole, as are hidden ones -- a dot-directory is tool state, not
        project input, and hashing it would key the map on things like
        editor droppings.
    """
    names: list[str] = []
    stack: list[Path] = [root]
    while stack:
        directory = stack.pop()
        for entry in os.scandir(directory):
            if entry.name.startswith(".") or entry.name in _SKIPPED_DIRECTORIES:
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))
            elif entry.is_file():
                names.append(Path(entry.path).relative_to(root).as_posix())
            # Neither file nor directory (socket, fifo, broken symlink):
            # contributes nothing to any test run, so nothing to the key.
    return names
