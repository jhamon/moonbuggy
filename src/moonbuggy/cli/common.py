"""Small pure helpers shared by CLI subcommands."""

import argparse
import io
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import IO

from ..accepted import (
    DEFAULT_ACCEPT_FILE,
)
from ..logging_policy import LoggingPolicy, policy_for


def _display_path(path: Path, project_dir: Path) -> str:
    # `--output-dir` may be an absolute path, in which case
    # `project_dir / args.output_dir` silently discards `project_dir` (an
    # absolute right operand replaces the left one under `/`), so `path` ends
    # up outside `project_dir` and `relative_to` raises. That is an
    # anticipated shape of input, not a crash-worthy one: a
    # user running `moonbuggy --output-dir /tmp/whatever` still gets a usable
    # line, just the absolute path instead of a shortened relative one.
    try:
        return str(path.relative_to(project_dir))
    except ValueError:
        return str(path)


def _measurable_fd(stream: IO[str]) -> int | None:
    # Only a terminal has a size to ask for, and only a real file object has an
    # fd to ask about -- `main`'s docstring supports being called in-process
    # with StringIO in place of either stream. None means "no measurement",
    # which `resolve_width` distinguishes from a measured 80.
    if not stream.isatty():
        return None
    try:
        return stream.fileno()
    except (OSError, ValueError, io.UnsupportedOperation):
        return None


def _clock(seconds: float) -> str:
    # M:SS, as the spec's progress and milestone lines show it.
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}:{remainder:02d}"


def _settled_line(done: int, total: int, counts: Counter[str], elapsed: float) -> str:
    # The greppable, committed form of progress: one per MILESTONE_INTERVAL
    # when there is no live region, and once more on close so the scrollback
    # keeps a record of how the run ended.
    tally = ", ".join(
        f"{counts[status]} {status.lower()}"
        for status in (
            "KILLED",
            "KILLED_BY_ERROR",
            "SURVIVED",
            "NO_COVERAGE",
            "TIMEOUT",
            "SUSPICIOUS",
            "SKIPPED",
        )
        if counts[status]
    )
    return (
        f"moonbuggy: {done}/{total} settled"
        + (f" -- {tally}" if tally else "")
        + f", {_clock(elapsed)}"
    )


def _logging_policy(args: argparse.Namespace) -> LoggingPolicy:
    """The logging policy this invocation asks for.

    Built in one place because three commands need the same answer: `run`,
    `run <id>` and `why` all regenerate mutants, and a mutant that is
    suppressed in one and measured in another would make the three disagree
    about the same source.

    Args:
        args: the parsed command line.

    Returns:
        The :class:`~moonbuggy.logging_policy.LoggingPolicy` for this run.
    """
    return policy_for(
        args.logger_name,
        include_logging_mutants=args.include_logging_mutants,
    )


def _accept_path(args: argparse.Namespace, project_dir: Path) -> Path:
    # Relative to the project root, not to the cwd, so `moonbuggy --project x`
    # and `cd x && moonbuggy` read the same ledger. An absolute --accept-file
    # replaces the root under `/`, which is the behaviour someone passing one
    # is asking for.
    return project_dir / (args.accept_file or DEFAULT_ACCEPT_FILE)


def _target_ids(tokens: Sequence[str]) -> list[str]:
    """The mutant ids to run, with `-` expanded from stdin.

    Args:
        tokens: the positional arguments as given.

    Returns:
        Ids in the order they were named, without duplicates -- running the
        same mutant twice in one invocation would only print it twice.
    """
    ids: list[str] = []
    seen = set()
    for token in tokens:
        found = _ids_from_stdin() if token == "-" else [_clean_id(token)]
        for mutant_id in found:
            if mutant_id and mutant_id not in seen:
                seen.add(mutant_id)
                ids.append(mutant_id)
    return ids


def _ids_from_stdin() -> list[str]:
    return [_clean_id(line) for line in sys.stdin.read().splitlines()]


def _clean_id(token: str) -> str:
    """The mutant id inside one line of input.

    Deliberately forgiving about what a pipeline hands over, because the
    workflow this command exists for is
    `grep -E '^(SURVIVED|NO_COVERAGE)' .moonbuggy/results.txt | moonbuggy run -`
    and the thing on the left of that pipe emits whole result lines. A bare
    id, an `id=...` token, and a full result line all name the same mutant, so
    all three are accepted.

    Args:
        token: one line of input, or one command-line argument.

    Returns:
        The id, or the empty string for a blank line.
    """
    token = token.strip()
    if not token:
        return ""
    if " " in token or "\t" in token:
        # A whole result line. The id is the token that says so; anything else
        # on the line is a field that is about to be re-measured anyway.
        for field in token.split():
            if field.startswith("id="):
                return field[len("id=") :]
        return token
    return token.removeprefix("id=")
