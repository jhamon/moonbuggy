"""Diff scoping: mutate only the lines a branch changed.

A full run is a session you schedule; a diff-scoped run is cheap enough to be a
habit, which is the whole point of `--since`. On a typical pull request the
changed lines carry a handful of mutants and seconds of runtime, so mutation
testing can gate every PR rather than being an audit somebody remembers to do.

This is a **filter on generation**, not a second mutation engine. Generation
produces exactly the mutants it always did; the scope is intersected with them
afterwards, and with `--include`/`--exclude` rather than in place of them. That
is what keeps a diff-scoped verdict identical to the verdict the same mutant
would get in a full run -- which is also why `--since` is deliberately absent
from the cache fingerprint (see :func:`moonbuggy.cache.run_fingerprint`): how
you reached a mutant cannot change its answer, so both kinds of run fill and
read the same cache.

Two decisions worth stating, because both differ from the obvious reading:

- The diff is taken between the **merge base** and the **working tree**, not
  `<ref>...HEAD`. The merge base half is the familiar three-dot semantics --
  scope is this branch's work, not whatever `main` has drifted to since. The
  working-tree half is a correctness requirement: moonbuggy mutates the files
  on disk, so a scope computed against HEAD would carry line numbers for a
  file that is not the one being read, and uncommitted work -- the state you
  are in when you most want a fast run -- would be scoped out.
- Untracked files are entirely in scope. `git diff` cannot see them, and a
  module you just wrote is the least-tested code in the tree; silently
  skipping it would be the worst possible way to be fast.
"""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# `@@ -old,count +new,count @@`, where either count may be omitted (meaning 1).
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# What a `git fetch --depth 1` checkout has to do to make `--since origin/main`
# work at all. Named once, because every error here is really the same
# question -- "does this clone contain enough history?" -- and the answer must
# be actionable in each of them.
_FETCH_DEPTH_HINT = (
    "In CI, actions/checkout fetches a single commit by default; "
    "set `fetch-depth: 0` so the base branch and its history are present."
)


class DiffScopeError(RuntimeError):
    """Raised when a `--since` scope cannot be computed.

    Always actionable and never a traceback: `cli.main` turns this into exit
    code 2 with the message alone, the same as every other anticipated failure.
    """


@dataclass(frozen=True)
class DiffScope:
    """The changed lines of a working tree, relative to a git ref.

    A plain frozen record with no I/O of its own, so a caller can build one in
    a test without a repository, and so the structured summary a `--json` run
    reports is the same object the run was filtered by rather than a second
    derivation of it.
    """

    ref: str
    """The ref as the user spelled it, e.g. `origin/main`."""

    merge_base: str
    """The full sha the diff was taken from."""

    ranges: dict[str, tuple[tuple[int, int], ...]]
    """Changed line ranges per file, as inclusive ``(first, last)`` pairs.

    Keyed by path relative to the project directory, with `/` separators.
    Files whose only change was a deletion do not appear: there is nothing
    left on those lines to mutate."""

    @property
    def files(self) -> tuple[str, ...]:
        """The changed files that have at least one mutable line, sorted."""
        return tuple(sorted(self.ranges))

    @property
    def changed_lines(self) -> int:
        """How many lines are in scope, across every file."""
        return sum(
            last - first + 1 for spans in self.ranges.values() for first, last in spans
        )

    def touches(self, module: str) -> bool:
        """Whether any line of this file is in scope.

        Used to drop whole files before generation runs on them, which is the
        cheap half of the filter.

        Args:
            module: a path relative to the project directory.

        Returns:
            True if the file has changed lines.
        """
        return _normalise(module) in self.ranges

    def contains(self, module: str, line: int) -> bool:
        """Whether one source line is in scope.

        Args:
            module: a path relative to the project directory.
            line: a 1-based line number.

        Returns:
            True if that line falls in a changed range.
        """
        return any(
            first <= line <= last
            for first, last in self.ranges.get(_normalise(module), ())
        )

    def describe(self) -> str:
        """One line saying what was scoped away, for a report footer.

        Returns:
            A sentence naming the ref, the merge base and the size of the
            scope -- so a 100% kill rate on three mutants can never be read as
            a clean full run.
        """
        files = len(self.ranges)
        lines = self.changed_lines
        return (
            f"Diff-scoped: only lines changed since {self.ref} "
            f"(merge base {self.merge_base[:7]}) were mutated "
            f"-- {files} {'file' if files == 1 else 'files'}, "
            f"{lines} {'line' if lines == 1 else 'lines'}."
        )


def scope_summary(scope: "DiffScope | None") -> dict[str, object]:
    """The scope of a run as structured data, whether or not it was scoped.

    Every run has an answer to "was this the whole codebase?", so this takes
    None and reports it rather than leaving the caller to build the negative
    case itself. `--json` consumers read these keys; the human footer reads
    :meth:`DiffScope.describe`. Both come from the same object.

    Args:
        scope: the run's diff scope, or None for a full run.

    Returns:
        A JSON-serialisable mapping with `diff_scoped`, `since`, `merge_base`,
        `files` and `changed_lines`. The last four are None/0 for a full run.
    """
    if scope is None:
        return {
            "diff_scoped": False,
            "since": None,
            "merge_base": None,
            "files": 0,
            "changed_lines": 0,
        }
    return {
        "diff_scoped": True,
        "since": scope.ref,
        "merge_base": scope.merge_base,
        "files": len(scope.ranges),
        "changed_lines": scope.changed_lines,
    }


def scope_since(ref: str, project_dir: str | os.PathLike[str]) -> DiffScope:
    """The lines `project_dir` has changed since `ref`.

    Args:
        ref: any git revision -- a branch, a tag, a sha.
        project_dir: the project root, which may be a subdirectory of the
            repository. Only paths under it are reported, and they are
            reported relative to it, because that is how a mutant names its
            module.

    Returns:
        The :class:`DiffScope` for this working tree.

    Raises:
        DiffScopeError: if this is not a git repository, `ref` does not
            resolve, or the two have no merge base.
    """
    project_dir = Path(project_dir).resolve()

    ok, top = _git(project_dir, "rev-parse", "--show-toplevel")
    if not ok:
        raise DiffScopeError(
            f"--since {ref} needs git history, but {project_dir} is not inside "
            "a git repository (or git is not installed). Drop --since to run "
            "over the whole project."
        )
    toplevel = Path(top.strip()).resolve()

    if not _git(project_dir, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")[
        0
    ]:
        raise DiffScopeError(
            f"--since {ref}: no such git ref in this repository. "
            f"Check the name -- a remote branch is usually `origin/{ref}`. "
            + _FETCH_DEPTH_HINT
        )

    ok, base = _git(project_dir, "merge-base", ref, "HEAD")
    if not ok or not base.strip():
        raise DiffScopeError(
            f"--since {ref}: no merge base between {ref} and HEAD, so there is "
            "no common history to diff against. A shallow clone is the usual "
            "cause: `git fetch --unshallow` locally. " + _FETCH_DEPTH_HINT
        )
    merge_base = base.strip()

    ok, diff = _git(
        project_dir,
        "-c",
        "core.quotePath=false",
        "diff",
        "--unified=0",
        "--no-color",
        "--find-renames",
        merge_base,
        "--",
    )
    if not ok:
        raise DiffScopeError(
            f"--since {ref}: `git diff` against {merge_base[:7]} failed. {diff.strip()}"
        )

    ranges: dict[str, tuple[tuple[int, int], ...]] = {}
    for path, spans in _parse_hunks(diff).items():
        relative = _relative_to_project(toplevel / path, project_dir)
        if relative is not None:
            ranges[relative] = spans

    for path in _untracked(project_dir):
        relative = _relative_to_project(toplevel / path, project_dir)
        if relative is None:
            continue
        count = _line_count(toplevel / path)
        if count:
            ranges[relative] = ((1, count),)

    return DiffScope(ref=ref, merge_base=merge_base, ranges=ranges)


def _git(cwd: Path, *args: str) -> tuple[bool, str]:
    # Every git call funnels through here so that "git is not installed" and
    # "git said no" arrive as the same kind of answer -- a flag and a string --
    # rather than one being an exception the callers would each have to catch.
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return False, ""
    if completed.returncode != 0:
        return False, completed.stderr
    return True, completed.stdout


def _parse_hunks(diff: str) -> dict[str, tuple[tuple[int, int], ...]]:
    """Changed line ranges per post-image path, from a unified=0 diff.

    Walks the diff with a cursor rather than scanning for prefixes, because a
    *content* line can begin with `@@` or `+++` -- a diff of a diff, a test
    fixture holding patch text -- and a prefix scan would read it as a header.
    Each hunk header states exactly how many lines follow it, and with zero
    context that count is exact.

    Args:
        diff: the output of `git diff --unified=0`.

    Returns:
        A mapping from path (relative to the repository root, `/` separated)
        to inclusive ``(first, last)`` line ranges in the post-image. Files
        whose hunks only removed lines are absent.
    """
    found: dict[str, list[tuple[int, int]]] = {}
    lines = diff.splitlines()
    index = 0
    path: str | None = None
    while index < len(lines):
        line = lines[index]
        if line.startswith("diff --git "):
            path = None
            index += 1
            continue
        if line.startswith("+++ "):
            target = line[4:]
            # `/dev/null` is a deleted file: nothing on disk to mutate.
            path = None if target == "/dev/null" else _strip_prefix(target)
            index += 1
            continue
        if path is None:
            index += 1
            continue
        match = _HUNK.match(line)
        if match is None:
            index += 1
            continue
        removed = 1 if match.group(2) is None else int(match.group(2))
        start = int(match.group(3))
        added = 1 if match.group(4) is None else int(match.group(4))
        # A hunk that adds nothing is a pure deletion. There is no line left
        # where those lines were, so it puts nothing in scope.
        if added:
            found.setdefault(path, []).append((start, start + added - 1))
        index += 1
        # Skip the hunk's body by count. "\ No newline at end of file" is a
        # note about the previous line rather than a line of its own, so it
        # does not come out of the budget.
        body = removed + added
        while index < len(lines) and body > 0:
            if not lines[index].startswith("\\"):
                body -= 1
            index += 1
    return {path: tuple(spans) for path, spans in found.items()}


def _strip_prefix(target: str) -> str:
    # `+++ b/pkg/mod.py`. The prefix is `b/` by default; a user's
    # `diff.noprefix` or `diff.dstPrefix` config would change it, so the
    # explicit `--src-prefix` is not assumed and the common case is handled
    # without insisting on it.
    return target[2:] if target.startswith("b/") else target


def _relative_to_project(path: Path, project_dir: Path) -> str | None:
    # Paths come out of git relative to the repository root, and mutants are
    # named relative to the project -- which may be a package inside a
    # monorepo. Anything outside the project is simply not this run's business.
    try:
        return path.resolve().relative_to(project_dir).as_posix()
    except (ValueError, OSError):
        return None


def _untracked(cwd: Path) -> list[str]:
    ok, out = _git(cwd, "ls-files", "--others", "--exclude-standard", "-z")
    if not ok:
        return []
    return [entry for entry in out.split("\0") if entry]


def _line_count(path: Path) -> int:
    # Counted from bytes, not decoded text: a source file may be latin-1 or
    # cp1251 (srcio honours PEP 263) and this only needs to know how far the
    # file goes. A file not ending in a newline still has a final line.
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _normalise(module: str) -> str:
    # Mutants carry OS-native separators; git speaks posix. One direction of
    # conversion, done at the lookup rather than at every call site.
    return module.replace(os.sep, "/") if os.sep != "/" else module
