"""In-memory mutation application via a meta path import hook.

Section 4.2 flags this as the highest-uncertainty piece in the design, with
three named risks. What the spike found for each:

1. COMPOSING WITH PYTEST'S ASSERT-REWRITE HOOK. Not the collision it looked
   like. pytest's hook rewrites test modules, conftest files, and registered
   plugins; this hook rewrites the module under test. Those sets are disjoint,
   so both can sit on sys.meta_path and each handles imports the other ignores.
   The finder here narrows to a single file path and returns None for every
   other module, which keeps it that way -- a broader finder that returned a
   spec for test modules would displace assert rewriting and silently degrade
   every failure message in the run.

2. LINECACHE. Handled here rather than left to the reporting layer, because by
   the time a traceback is formatted the original source is what gets quoted.
   The entry is stored with mtime None, which makes linecache.checkcache treat
   it as not-from-a-file and leave it alone; a real mtime would be compared
   against the unmutated file on disk and evicted.

3. XDIST. Not solvable here -- it is a process-lifecycle problem, so it lives
   in plugin.py where the worker's own pytest_configure can call install().
"""

import importlib.util
import linecache
import os
import sys
import types
from collections.abc import Buffer, Sequence
from importlib.abc import Loader
from importlib.machinery import ModuleSpec, PathFinder, SourceFileLoader
from pathlib import Path

from .srcio import detect_encoding, encode_source, read_source, replace_line


def mutated_source(path: str | os.PathLike[str], line: int, mutated_text: str) -> str:
    """The file's source with one line replaced, keeping its indentation.

    Args:
        path: the module file being mutated.
        line: 1-based line number to replace.
        mutated_text: the replacement line, stripped of indentation.

    Returns:
        the full mutated source as ``str``.

    Raises:
        SourceError: if the file cannot be decoded (see :mod:`moonbuggy.srcio`).
    """
    return replace_line(read_source(path), line, mutated_text)


class _MutatingLoader(SourceFileLoader):
    """A normal source loader that serves mutated bytes instead of the file.

    Bytecode caching is disabled, and that is not an optimisation detail. A
    SourceFileLoader writes compiled bytecode to __pycache__ as a side effect of
    importing, and stamps it with the *real* file's mtime and size -- so the
    mutated .pyc looks valid for the unmutated source. Every later import picks
    it up: moonbuggy's next run, the naive oracle's, and the user's own plain
    `pytest`. Their suite starts failing with mutations they never asked for,
    the source files are byte-identical, and nothing points at us.

    D3's obvious reading -- hash the .py files -- does not catch this, because
    the .py files really are untouched. See docs/development/spike-a-findings.md.
    """

    def __init__(
        self,
        fullname: str,
        path: str,
        source: str,
        encoding: str | None = None,
    ) -> None:
        super().__init__(fullname, path)
        self._source = source
        self._source_path = str(Path(path).resolve())
        # The bytes handed back are decoded by the import machinery using the
        # file's own PEP 263 rules, so they have to be encoded the way the file
        # declares. Encoding a latin-1 module as UTF-8 produces a module that
        # imports without error and contains different characters (M1.4.7).
        self._encoding = encoding or "utf-8"

    def get_data(self, path: str) -> bytes:
        if str(Path(path).resolve()) == self._source_path:
            return encode_source(self._source, self._encoding)
        # Anything else is a bytecode path. Refusing it forces compilation from
        # the mutated source rather than any cache lying around on disk.
        raise OSError(f"moonbuggy: not reading cached bytecode for {path}")

    def set_data(self, path: str, data: Buffer, **kwargs: object) -> None:
        """Never write bytecode for a mutated module. Deliberately a no-op."""
        return

    def get_source(self, fullname: str) -> str:
        return self._source


class _MutationFinder:
    """Serves one specific file mutated; invisible for everything else."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        source: str,
        encoding: str | None = None,
    ) -> None:
        self.path = str(Path(path).resolve())
        self.source = source
        self.encoding = encoding

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: types.ModuleType | None = None,
    ) -> ModuleSpec | None:
        # PathFinder is consulted directly rather than by re-entering
        # sys.meta_path, so this cannot recurse into itself.
        spec = PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.origin is None:
            return None
        if str(Path(spec.origin).resolve()) != self.path:
            return None
        loader: Loader = _MutatingLoader(
            fullname, spec.origin, self.source, self.encoding
        )
        return importlib.util.spec_from_file_location(
            fullname,
            spec.origin,
            loader=loader,
        )


def install(path: str | os.PathLike[str], line: int, mutated_text: str) -> str:
    """Apply a mutation to `path` for every subsequent import in this process.

    Safe to call before the module is imported, which is the normal case:
    pytest_configure runs before test collection imports anything under test.

    Args:
        path: the module file to mutate.
        line: 1-based line number to replace.
        mutated_text: the replacement line.

    Returns:
        the mutated source as ``str``.

    Raises:
        SourceError: if the file cannot be decoded.
    """
    source = mutated_source(path, line, mutated_text)
    resolved = str(Path(path).resolve())
    encoding = detect_encoding(resolved)

    _populate_linecache(resolved, source)
    _evict_already_imported(resolved)

    sys.meta_path.insert(0, _MutationFinder(resolved, source, encoding))
    return source


def uninstall_all() -> None:
    """Remove every installed mutation from this process.

    Only meaningful in-process; the normal lifecycle is one mutant per process,
    which is what makes the xdist story work. Tests need it to avoid leaking a
    finder into later cases.
    """
    for finder in [f for f in sys.meta_path if isinstance(f, _MutationFinder)]:
        sys.meta_path.remove(finder)
        linecache.cache.pop(finder.path, None)
        _evict_already_imported(finder.path)


def _populate_linecache(path: str, source: str) -> None:
    lines = source.splitlines(keepends=True)
    # mtime None: linecache.checkcache skips entries it did not read from disk.
    linecache.cache[path] = (len(source), None, lines, path)


def _evict_already_imported(path: str) -> None:
    """Drop any module already imported from this file.

    A module imported before install() would otherwise keep its unmutated code
    objects and the mutation would silently do nothing -- the exact false
    SURVIVED this whole mechanism exists to avoid.
    """
    for name, module in list(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if origin and str(Path(origin).resolve()) == path:
            del sys.modules[name]
