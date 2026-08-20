"""Persistent results cache, so repeat runs skip mutants nothing has changed for.

Two independent sources pointed at this (6.4): Hypothesis's replay database and
Ruff's incremental re-linting. Both converge on the same idea, which is why the
design promoted it into the MVP rather than deferring it.

The risk is not a cache miss, it is a stale hit. Serving a SURVIVED after the
user added the test that kills it hides the gap they just closed and reports it
as still outstanding -- worse than no cache, because it is confidently wrong.
The key therefore covers the inputs a mutation run can be expected to notice:

- the mutant's identity and mutated text,
- the full source of the module being mutated,
- the contents of every test file selected for it, and
- a fingerprint of the run itself -- see `run_fingerprint`, which covers the
  parts of the command line that decide what pytest does.

Hashing the whole module rather than just the mutated function is deliberately
coarser than criterion F2 requires. A mutant's behaviour can depend on anything
else in its module -- a helper it calls, an import, a module-level constant --
and per-function hashing would miss those. Coarse and correct beats precise and
occasionally stale; if this ever shows up in profiles, it is a safe thing to
tighten with evidence.

What the key cannot see
-----------------------

Those four bullets are the whole of `key_for`: the only file bytes that reach
the digest are the mutated module's and, for each selected node id, the test
file the node id names. Everything else a verdict depends on is outside the
key, and editing it yields a stale hit -- the exact failure this module opens by
warning about. Known gaps, in rough order of how often they bite:

- `conftest.py`. Editing a fixture changes what the selected tests actually do
  while their own file bytes are unchanged, so the key does not move. This is
  the most likely stale-hit vector in practice.
- Any *other* module the mutated module imports. The per-function argument
  above is about depth within one module; it says nothing about a helper in a
  sibling module, whose edit is invisible here.
- pytest configuration (`pytest.ini`, `[tool.pytest.ini_options]`) and
  installed dependency versions. `run_fingerprint` covers the command line, not
  the config files or the environment behind it.
- Which tests *inside* an unchanged file were selected. The key hashes the test
  file, not the node id list, so a change in selection within one file does not
  move it.

Widening the key would be a behaviour change with a real cost in cache reuse
and would need a `CACHE_VERSION` bump; it has not been made. Until it is,
`--no-cache` is the answer when one of the above has changed.
"""

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
CACHE_VERSION = 4


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
            contents of every selected test file, and this run's fingerprint.
        """
        project_dir = Path(project_dir)
        digest = hashlib.sha256()
        digest.update(self._fingerprint.encode())
        digest.update(mutant.id.encode())
        digest.update(mutant.mutated.encode())
        digest.update(_read_bytes(project_dir / mutant.module))
        # Sorted so selection order cannot change the key.
        for test_id in sorted(selected_tests):
            test_file = test_id.split("::")[0]
            digest.update(test_file.encode())
            digest.update(_read_bytes(project_dir / test_file))
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
        save leaves the previous cache intact rather than a half-written file
        (criterion M1.4.13). `os.replace` is atomic within a filesystem, and the
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
