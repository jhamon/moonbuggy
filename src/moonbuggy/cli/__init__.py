"""CLI entry points and subcommand dispatch.

These names are re-exported on purpose: `moonbuggy.cli` is the public surface
the tests and the `[project.scripts] moonbuggy = "moonbuggy.cli:run"` entry
point import from, even though the implementation now lives in the submodules
below.
"""

import os
import sys
import time
from collections.abc import Sequence

__all__ = [
    "main",
    "run",
    "_harden_streams",
    "_accept",
    "_build_parser",
    "_prepare_cache",
    "_clean_id",
    "_clock",
    "_display_path",
    "_measurable_fd",
    "_settled_line",
    "_target_ids",
    "_operators",
    "_run",
    "_run_one",
    "_show",
    "_why",
]

from .. import profiling
from ..accepted import AcceptError
from ..baseline import BaselineError
from ..coverage_pass import CoveragePassError
from ..diffscope import DiffScopeError
from ..discover import LayoutError
from ..operators import SelectionError
from ..srcio import SourceError
from ..verify import VerifyError
from .accept import _accept
from .common import (
    _clean_id,
    _clock,
    _display_path,
    _measurable_fd,
    _settled_line,
    _target_ids,
)
from .exec import _prepare_cache, _run
from .explain import _run_one, _show, _why
from .operators import _operators
from .parser import _build_parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run moonbuggy.

    Args:
        argv: command-line arguments, or None to read `sys.argv`.

    Returns:
        The process exit code: 0 for a run with no findings, 1 when there are
        findings (SURVIVED or NO_COVERAGE), 2 when the run could not happen at
        all, 130 when interrupted.
    """
    _harden_streams()
    profiling.active().add("import chain", _IMPORTS_DONE - profiling.active().started)
    parser = _build_parser()
    args = parser.parse_args(argv)
    # Before the handler below, which reports an interrupt by naming the
    # partial results in `args.output_dir`. `operators` reads the registry and
    # writes nothing, so it has no results directory to name and nothing worth
    # interrupting.
    if args.command == "operators":
        return _operators(args)
    try:
        if args.command == "show":
            return _show(args)
        if args.command == "accept":
            return _accept(args)
        if args.command == "run-one":
            return _run_one(args)
        if args.command == "why":
            return _why(args)
        return _run(args)
    except KeyboardInterrupt:
        # An anticipated ending, not a crash. 130 is the shell convention for
        # SIGINT. The results file is valid at every instant (criterion
        # M1.4.13), so whatever finished is already usable.
        print(
            "\nmoonbuggy: interrupted. Partial results in "
            f"{args.output_dir}/results.jsonl",
            file=sys.stderr,
        )
        return 130
    except (
        LayoutError,
        CoveragePassError,
        BaselineError,
        SourceError,
        DiffScopeError,
        AcceptError,
        VerifyError,
        SelectionError,
    ) as error:
        # Criteria H5 and M1.4.12: an actionable message, never a traceback.
        # Every failure moonbuggy can anticipate is funnelled through here, so
        # the CLI has exactly one way of reporting that it cannot proceed.
        print(f"moonbuggy: {error}", file=sys.stderr)
        return 2


def run() -> None:
    """Entry point for the `moonbuggy` command: `main`, then exit at once.

    `main` returns an exit code and this turns it into a process exit, which
    would normally be `sys.exit`. It is `os._exit` instead, because CPython's
    finalisation of a process holding pytest, coverage and moonbuggy is
    **13ms** — measured as 85ms to exit normally against 72ms to `os._exit`,
    on a process importing exactly what this one imports. That is a real part
    of what a user waits for, and none of it does any work: every object being
    torn down is about to stop existing anyway.

    What `os._exit` skips is `atexit`, module teardown, and **buffer
    flushing**, and the last of those is the whole risk. moonbuggy's two output
    files are written and closed inside `_run` before it returns, so the only
    thing left unflushed is the standard streams, which are flushed here
    explicitly. A missed flush would lose the report, which is worse than a
    slow exit -- so this function stays this short, and anything that ever
    needs to happen at exit must happen before the flush rather than in an
    `atexit` hook that will not run.

    `main` itself deliberately still returns rather than exiting, so the tests
    and any in-process caller get a value instead of a dead interpreter.
    """
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def _harden_streams() -> None:
    """Make stdout/stderr degrade instead of raising on unencodable output.

    A source file may legally be latin-1 or cp1251 (srcio honours PEP 263),
    so a mutated line can hold characters stdout cannot encode. With the
    default errors="strict" that is a UnicodeEncodeError raised from inside
    the report -- past `main`'s error handler and past `run()`'s explicit flush
    -- as a bare traceback, losing the buffered report.
    backslashreplace degrades the character and keeps the run.

    `getattr` rather than a direct call: an in-process caller may have
    replaced the streams with an object that has no `reconfigure`, and
    `main`'s docstring says calling it in-process is supported.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


_IMPORTS_DONE = time.perf_counter()


if __name__ == "__main__":
    run()
