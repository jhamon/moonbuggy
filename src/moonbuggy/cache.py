"""Persistent results cache, so repeat runs skip mutants nothing has changed for.

Two widely-seen precedents pointed at this: Hypothesis's replay database and
Ruff's incremental re-linting. Both converge on the same idea, which is why the
design promoted it into the MVP rather than deferring it.

The risk is not a cache miss, it is a stale hit. Serving a SURVIVED after the
user added the test that kills it hides the gap they just closed and reports it
as still outstanding -- worse than no cache, because it is confidently wrong.
The key therefore covers the inputs a mutation run can be expected to notice:

- the mutant's identity and mutated text,
- the full source of the module being mutated,
- the contents of every test file selected for it,
- the conftest.py chain those test files pull in (a fixture edit is exactly
  what changes what a test *does* while its own bytes are unchanged),
- the mutated module's first-order imports, resolved to files inside the
  project, and
- a fingerprint of the run itself -- see `run_fingerprint`, which covers the
  parts of the command line that decide what pytest does.

Hashing the whole module rather than just the mutated function is deliberately
coarse. A mutant's behaviour can depend on anything
else in its module -- a helper it calls, an import, a module-level constant --
and per-function hashing would miss those. Coarse and correct beats precise and
occasionally stale; if this ever shows up in profiles, it is a safe thing to
tighten with evidence.

What the key cannot see
-----------------------

Those bullets are not the whole of `key_for`'s inputs, but they are not the
whole of reality either. Everything still outside the key yields a stale hit
when edited, and the boundary is drawn where the reuse it would cost outweighs
the correctness it buys. Known gaps, kept small on purpose:

- **Transitive imports.** Only the mutated module's *first-order* imports are
  resolved, and only to files inside the project. A helper two levels deep, an
  installed dependency's source, or a ``from .. import x`` ancestor-relative
  import is invisible unless it is the mutated module's own direct import.
- **pytest configuration and dependency versions.** `run_fingerprint` covers
  the command line, not `pytest.ini`, `[tool.pytest.ini_options]`, or the
  installed environment behind them. A dependency bump invalidating everything
  is arguably *right* rather than a cost, but it has not been measured, so it
  is not wired in yet.
- **Which tests *inside* an unchanged file were selected.** The key hashes the
  test file, not the node id list, so a change in selection within one file
  does not move it.

A key that covers everything is not the goal -- it would cost the reuse the
cache exists to buy. When one of the above has changed, `--no-cache` is the
answer, as it always has been.
"""

import ast
import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict

from .mutant import Mutant

# Bumped whenever the key derivation or record shape changes, or whenever the
# meaning of a stored value does. An old cache is then ignored rather than
# misread -- entries keyed by a different algorithm are not wrong-looking, they
# are silently wrong.
#
# 3: NO_COVERAGE. Mutants no test reaches were stored as "SURVIVED", and a v2
#    entry replayed under v3 would put them back in the survivor list under the
#    name the report no longer uses for them.
# 4: KILLED_BY_ERROR. Same argument, one status along. A v3 entry holds
#    "KILLED" for every kill, including the ones this version calls
#    KILLED_BY_ERROR -- and unlike NO_COVERAGE this is not confined to a new
#    operator, because any operator can produce a mutation that makes a test
#    raise. A warm cache would therefore report a *different* crash-kill count
#    than a cold one on the same code, which is the one thing a cache must
#    never do.
# 5: doctest mismatches reclassified. v4 stored KILLED_BY_ERROR for a doctest
#    that caught a mutation, because `doctest.DocTestFailure` is neither an
#    AssertionError nor a pytest failure; it is now the ordinary KILLED it
#    always should have been. v4 never shipped -- 0.2.0 is the first release to
#    carry any of these -- so this bump costs one cold run to nobody outside
#    this repository, and buys not replaying a verdict this version disagrees
#    with.
# 6: conftest.py chain and first-order imports joined the key. A v5 entry was
#    computed without either, so it would serve a stale verdict the moment a
#    fixture or an imported helper changed. Both are new inputs to the digest,
#    so every old entry must be ignored rather than misread.
CACHE_VERSION = 6


def run_fingerprint(
    pytest_args: Iterable[str] = (),
    *,
    timeout: float | None = None,
    python: str | None = None,
) -> str:
    """A digest of the run inputs that can change a mutant's verdict.

    Mixed into every key by :class:`ResultCache`, so a run whose command line
    differs cannot be served the previous run's answers. Only inputs that can
    genuinely change an outcome belong here -- each one folded in halves cache
    reuse for anyone who varies it, and a cache that never hits is its own
    failure mode.

    What is in, and why:

    - `pytest_args` (`--pytest-arg`): they reach the baseline and every mutant
      run, and they decide both which tests exist (`--doctest-modules`, `-p`)
      and whether they pass (`-W error`, `-m`). Hashed in order, because
      pytest's argument order is meaningful -- the last `-p` wins and `-W`
      filters match last-first -- unlike test selection, which is sorted.
    - `timeout` (`--timeout`): TIMEOUT is a verdict about the clock. The same
      mutant is TIMEOUT at five seconds and KILLED at sixty, so the clock is
      an input to the answer and not merely to how long it takes to get it.
    - `python`: a different interpreter is a different language version and a
      different set of installed packages. A test that is skipped on one and
      fails on another produces a different verdict from identical source.

    What is deliberately out:

    - `-n/--workers`: xdist changes how the selected tests are distributed
      across processes, not which of them run or what they assert. A suite
      whose outcome depends on worker count is order-dependent, which is the
      flaky-probe's problem (such tests make their mutants SUSPICIOUS) rather
      than something to encode in the key. Folding it in would cost every
      cache entry to anyone who flips between serial and parallel runs, and
      buy no correctness.
    - `--jobs`: parallelism across mutants. Each mutant still runs alone, in
      its own process, against its own selection.
    - `--since`: how you reached a mutant cannot change its answer. A scoped
      run generates a subset of the mutants a full run does, and each one is
      the same mutation run against the same selection either way, so the two
      deliberately share a cache. See :mod:`moonbuggy.diffscope`.

    Args:
        pytest_args: `--pytest-arg` values, in the order they were given.
        timeout: seconds before a mutant is called TIMEOUT, or None.
        python: the resolved interpreter for mutant runs, or None.

    Returns:
        A hex digest, stable for identical inputs and different for any
        change to them.
    """
    digest = hashlib.sha256()
    for argument in pytest_args:
        # Length-prefixed, so ["-ab"] and ["-a", "b"] cannot hash alike.
        digest.update(f"{len(argument)}:".encode())
        digest.update(argument.encode())
    digest.update(b"\0timeout\0")
    digest.update(b"" if timeout is None else repr(float(timeout)).encode())
    digest.update(b"\0python\0")
    digest.update(b"" if python is None else python.encode())
    return digest.hexdigest()


class CacheRecord(TypedDict):
    """One mutant's stored outcome, as read back by `runner.run_one`."""

    status: str
    tests_run: int
    nearest_test: str | None


class ResultCache:
    """Mutant outcomes from previous runs, keyed on everything they depend on."""

    def __init__(self, path: str | os.PathLike[str], fingerprint: str = "") -> None:
        """Open the cache at `path`.

        Args:
            path: the cache file. It need not exist yet.
            fingerprint: a :func:`run_fingerprint` digest for this run, mixed
                into every key. The default is empty, which keys purely on the
                code -- correct only for a caller that never varies the run
                inputs.
        """
        self.path = Path(path)
        self._fingerprint = fingerprint
        self._entries = self._load()

    def _load(self) -> dict[str, CacheRecord]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable: cost a cold run, never a wrong result.
            return {}
        if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
            return {}
        entries = payload.get("entries")
        return entries if isinstance(entries, dict) else {}

    def key_for(
        self,
        mutant: Mutant,
        project_dir: str | os.PathLike[str],
        selected_tests: Iterable[str],
    ) -> str:
        """The cache key for one mutant.

        Args:
            mutant: the mutant whose outcome would be stored.
            project_dir: the project root, used to resolve relative paths.
            selected_tests: the pytest node ids selection chose for it.

        Returns:
            A hex digest covering the mutant, its module's full source, the
            contents of every selected test file, the conftest.py chain each
            test file pulls in, the mutant's first-order imports, and this
            run's fingerprint.
        """
        project_dir = Path(project_dir)
        digest = hashlib.sha256()
        digest.update(self._fingerprint.encode())
        digest.update(mutant.id.encode())
        digest.update(mutant.mutated.encode())
        digest.update(_read_bytes(project_dir / mutant.module))
        conftests: set[str] = set()
        # Sorted so selection order cannot change the key.
        for test_id in sorted(selected_tests):
            test_file = test_id.split("::")[0]
            digest.update(test_file.encode())
            digest.update(_read_bytes(project_dir / test_file))
            conftests.update(_conftest_paths(test_file))
        # conftest.py from rootdir down to each test file, and the mutated
        # module's first-order imports. Both are hashed as path + bytes so a
        # missing file differs from an empty one, and a file that moves to a
        # different directory changes the key.
        for conftest in sorted(conftests):
            digest.update(("conftest\0" + conftest).encode())
            digest.update(_read_bytes(project_dir / conftest))
        for imported in _imported_project_files(project_dir, mutant.module):
            digest.update(("import\0" + imported).encode())
            digest.update(_read_bytes(project_dir / imported))
        return digest.hexdigest()

    def get(self, key: str) -> CacheRecord | None:
        """The stored record for `key`, or None if there is not one."""
        return self._entries.get(key)

    def put(self, key: str, record: CacheRecord) -> None:
        """Store `record` under `key`. Not written to disk until `save`."""
        self._entries[key] = record

    def save(self) -> None:
        """Persist the cache, atomically.

        Written to a sibling temp file and renamed, so a run killed during the
        save leaves the previous cache intact rather than a half-written file.
        `os.replace` is atomic within a filesystem, and the
        temp file is deliberately a sibling so it is on the same one.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(
                {"version": CACHE_VERSION, "entries": self._entries}, sort_keys=True
            )
        )
        os.replace(temporary, self.path)

    def clear(self) -> None:
        """Forget everything, on disk and in memory."""
        self._entries = {}
        self.path.unlink(missing_ok=True)

    def __len__(self) -> int:
        return len(self._entries)


def _read_bytes(path: str | os.PathLike[str]) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError:
        # A missing file is a real state, not an error: hash it as such so the
        # key changes if it later appears.
        return b"\0missing\0"


def _conftest_paths(test_file: str) -> list[str]:
    """Project-relative ``conftest.py`` paths pytest loads for `test_file`.

    pytest collects conftest.py from the rootdir down through every directory
    on the path to a test file, so a ``tests/test_x.py`` pulls in the root
    ``conftest.py`` and ``tests/conftest.py`` alike. The list runs root-to-leaf
    and always includes the root itself, so a test file at the project root
    still sees ``conftest.py``.
    """
    directories: list[Path] = []
    current = Path(test_file).parent
    while True:
        directories.append(current)
        if current == Path("."):
            break
        current = current.parent
    return [str(directory / "conftest.py") for directory in directories]


# The mutated module is the same for every mutant that mutates it, so its first
# order imports are resolved once and reused. Keyed by (path, mtime, size) like
# :data:`moonbuggy.srcio._SOURCE_CACHE`: an edit during a run is a miss rather
# than a stale hit. ``key_for`` then hashes each resolved file's bytes, which
# the OS page cache and the loop above make cheap to read again.
_IMPORT_RESOLUTION_CACHE: dict[tuple[str, int, int], tuple[str, ...]] = {}


def _imported_project_files(
    project_dir: str | os.PathLike[str], module: str
) -> tuple[str, ...]:
    """Project-relative files the mutated module's first-order imports resolve to.

    Resolution is static (AST), never by importing the code -- importing
    arbitrary project code to compute a cache key is its own hazard. A name
    that resolves to nothing inside the project (stdlib, site-packages, or a
    file that does not exist) contributes nothing to the key: those edits are
    deliberately out of scope and documented as a known gap.

    Args:
        project_dir: the project root, used to resolve relative paths.
        module: the mutated module's path, relative to `project_dir`.

    Returns:
        The resolved files, sorted, as project-relative paths. Empty when the
        module cannot be parsed or imports nothing inside the project.
    """
    module_path = Path(project_dir) / module
    try:
        stat = os.stat(module_path)
        stat_key = (str(module_path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return ()
    cached = _IMPORT_RESOLUTION_CACHE.get(stat_key)
    if cached is not None:
        return cached

    try:
        tree = ast.parse(module_path.read_bytes())
    except (OSError, SyntaxError, ValueError):
        # Unreadable or unparseable: the module's own bytes are already hashed,
        # so nothing more can be learned here. Return empty rather than guess.
        return ()

    source_parent = Path(module).parent
    targets: list[tuple[Path, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend((Path("."), alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                targets.append((Path("."), node.module))
                # ``from pkg import helper`` names the submodule in `names`,
                # not `module`. Resolving only `module` would drop
                # ``pkg/helper.py`` -- the module the code actually calls --
                # while ``pkg/__init__.py`` stays the same, serving a stale
                # verdict when the helper changes (#37).
                targets.extend(
                    (Path("."), node.module + "." + alias.name) for alias in node.names
                )
            elif node.level == 1:
                # A single-dot relative import resolves inside the mutated
                # module's own package. ``from . import x`` leaves `module`
                # empty and names the submodule in `names`.
                if node.module is not None:
                    targets.append((source_parent, node.module))
                else:
                    targets.extend((source_parent, alias.name) for alias in node.names)
            # ``from .. import x`` (level > 1) is deliberately left out: it
            # resolves against an ancestor package, which is rare inside a
            # project and cheap for the user to cover with --no-cache.

    found: set[str] = set()
    for base, dotted in targets:
        relative = Path(*dotted.split("."))
        for candidate in (
            base / relative.with_suffix(".py"),
            base / relative / "__init__.py",
        ):
            if (Path(project_dir) / candidate).is_file():
                found.add(str(candidate))

    resolved = tuple(sorted(found))
    _IMPORT_RESOLUTION_CACHE[stat_key] = resolved
    return resolved
