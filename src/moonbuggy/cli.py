"""Command line interface.

Low floor, high ceiling (6.2): bare `moonbuggy` in a pytest project runs
end to end with no flags and no config file. Everything else is available and
nothing else is required.

Two artifacts are written per run, per 5.2: results.jsonl is canonical, and
results.txt is derived from it rather than authored alongside it, so they cannot
drift apart.
"""

import argparse
import io
import os
import sys
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import IO

from . import __version__, profiling
from .accepted import (
    DEFAULT_ACCEPT_FILE,
    Acceptance,
    AcceptError,
    Entry,
    entry_for,
    is_git_ignored,
)
from .accepted import load as load_accepted
from .accepted import resolve as resolve_accepted
from .accepted import save as save_accepted
from .accepted import tally as tally_accepted
from .baseline import BaselineError
from .cache import ResultCache, run_fingerprint
from .coverage_pass import CoveragePassError, run_baseline_pass
from .diffscope import DiffScope, DiffScopeError, scope_since
from .discover import (
    LayoutError,
    find_source_dir,
    find_source_files,
    looks_like_pytest_project,
)
from .generate import GenerationError, generate_mutants
from .humanreport import render_footer, render_report
from .mutant import Mutant
from .report import (
    FINDING_STATUSES,
    StreamingJSONL,
    find_record,
    plaintext_from_records,
    read_jsonl,
    render_line,
    summarise,
    write_jsonl,
)
from .runner import Result, run_mutants, run_session
from .srcio import SourceError, read_source
from .terminal import (
    LiveRegion,
    is_ci,
    palette_for,
    resolve_colour,
    resolve_format,
    resolve_width,
)

DEFAULT_OUTPUT_DIR = ".moonbuggy"

# How often a run with no live region commits a progress line. Those lines go
# into a log or a CI transcript and stay there, so they are paced for someone
# reading the file afterwards rather than for someone watching a terminal.
MILESTONE_INTERVAL = 10.0

# The end of moonbuggy's import chain, as a timestamp rather than a span: by
# the time anything here can run, the chain has already happened. `profiling`
# is deliberately the first module imported above, so its clock started at the
# top of the chain and the difference is the whole of it. Recorded because
# three rounds of profiles reported a 51-70ms remainder as unattributed, which
# is a tenth of a fast run and was the largest thing nobody had named.
_IMPORTS_DONE = time.perf_counter()


def _harden_streams() -> None:
    """Make stdout/stderr degrade instead of raising on unencodable output.

    A source file may legally be latin-1 or cp1251 (srcio honours PEP 263),
    so a mutated line can hold characters stdout cannot encode. With the
    default errors="strict" that is a UnicodeEncodeError raised from inside
    the report, past main's handler, as a traceback -- which criterion H5
    forbids -- and past run()'s explicit flush, losing the buffered report.
    backslashreplace degrades the character and keeps the run.

    `getattr` rather than a direct call: an in-process caller may have
    replaced the streams with an object that has no `reconfigure`, and
    `main`'s docstring says calling it in-process is supported.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


def main(argv: Sequence[str] | None = None) -> int:
    """Run moonbuggy.

    Args:
        argv: command-line arguments, or None to read `sys.argv`.

    Returns:
        The process exit code: 0 for a clean run, 1 when there are survivors,
        2 when the run could not happen at all, 130 when interrupted.
    """
    _harden_streams()
    profiling.active().add("import chain", _IMPORTS_DONE - profiling.active().started)
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            return _show(args)
        if args.command == "accept":
            return _accept(args)
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
    ) as error:
        # Criteria H5 and M1.4.12: an actionable message, never a traceback.
        # Every failure moonbuggy can anticipate is funnelled through here, so
        # the CLI has exactly one way of reporting that it cannot proceed.
        print(f"moonbuggy: {error}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moonbuggy",
        description="Fast, agent-first mutation testing for Python.",
    )
    parser.add_argument(
        "--version", action="version", version=f"moonbuggy {__version__}"
    )
    parser.set_defaults(command="run")

    _add_run_arguments(parser)

    sub = parser.add_subparsers(dest="command")
    show = sub.add_parser("show", help="print the full record for one mutant id")
    show.add_argument("mutant_id")
    show.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    show.set_defaults(command="show")

    accept = sub.add_parser(
        "accept",
        help="record a mutant as a reviewed equivalent, list the ledger, "
        "or take an entry back out",
    )
    accept.add_argument(
        "mutant_id",
        nargs="?",
        help="the mutant to accept, as printed in `id=...` (omit for --list)",
    )
    accept.add_argument("--project", default=".", help="project root (default: cwd)")
    accept.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    accept.add_argument("--accept-file", default=None, help=_ACCEPT_FILE_HELP)
    accept.add_argument(
        "-r",
        "--reason",
        default=None,
        help="why this mutant is equivalent. Required, and the whole point: "
        "an acceptance without one is a claim nobody can check",
    )
    accept.add_argument(
        "--list", action="store_true", help="print the ledger, one line per entry"
    )
    accept.add_argument(
        "--remove", action="store_true", help="delete this mutant's entry"
    )
    accept.set_defaults(command="accept")
    return parser


# Shared by `moonbuggy` and `moonbuggy accept`, because a path that means one
# file to the command that writes it and another to the command that reads it
# is the one way this feature can silently do nothing.
_ACCEPT_FILE_HELP = (
    "the accepted-equivalents ledger "
    f"(default: {DEFAULT_ACCEPT_FILE}, relative to the project root). "
    "Deliberately not under --output-dir: it is a checked-in record of human "
    "decisions, not run output"
)


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", default=".", help="project root (default: cwd)")
    parser.add_argument(
        "--source", default=None, help="directory to mutate (default: discovered)"
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="seconds before a mutant is called TIMEOUT (default: 30)",
    )
    parser.add_argument(
        "--operators",
        default=None,
        help="comma-separated operator names to use (default: all)",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="only mutate paths containing this fragment (repeatable)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="skip paths containing this fragment (repeatable)",
    )
    parser.add_argument(
        "--since",
        default=None,
        metavar="REF",
        help="only mutate lines changed since this git ref, compared against "
        "the merge base (e.g. --since origin/main). Composes with "
        "--include/--exclude rather than replacing them",
    )
    parser.add_argument("--accept-file", default=None, help=_ACCEPT_FILE_HELP)
    parser.add_argument(
        "--fail-on-unexplained",
        action="store_true",
        help="exit 1 only for findings that are neither killed nor accepted. "
        "Without it the exit code is unchanged -- a run whose every survivor "
        "is accepted still exits 1 -- so adding a ledger never silently turns "
        "a red build green",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=0,
        help="mutants to run concurrently (default: CPU count)",
    )
    parser.add_argument(
        "-n",
        "--workers",
        type=int,
        default=0,
        help="pytest-xdist workers per mutant run (default: 0, serial)",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="ignore and do not update the cache"
    )
    parser.add_argument(
        "--clear-cache", action="store_true", help="delete the cache, then run"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="only print the summary line"
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra argument passed to every pytest run, including "
        "the baseline and each mutant (repeatable). Needed when "
        "your real test command is not bare pytest -- "
        "`--pytest-arg=--doctest-modules`, say",
    )
    parser.add_argument(
        "--flaky-probe",
        type=int,
        default=1,
        metavar="N",
        help="extra unmutated suite runs used to detect flaky tests; "
        "a test whose outcome varies makes every mutant it covers "
        "SUSPICIOUS (default: 1, 0 disables)",
    )
    parser.add_argument(
        "--report",
        choices=["human", "agent"],
        default=None,
        help="output format: 'human' for a readable report with diffs, "
        "'agent' for one grep-friendly line per mutant "
        "(default: human at a terminal, agent when piped; "
        "MOONBUGGY_REPORT overrides)",
    )
    parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default=None,
        help="colour in the human report (default: auto; NO_COLOR is honoured)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="wrap the human report to this many columns (default: detected)",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="do not draw the live progress line",
    )


def _run(args: argparse.Namespace) -> int:
    project_dir = Path(args.project).resolve()
    profiler = profiling.active()
    started = time.perf_counter()

    fmt = resolve_format(args.report, os.environ, sys.stdout.isatty())
    palette = palette_for(resolve_colour(args.color, os.environ, sys.stdout.isatty()))
    width = resolve_width(args.width, os.environ, _measurable_fd(sys.stdout))
    # The live line goes to stderr, so it is measured against stderr. `width`
    # above cannot stand in: under `moonbuggy > report.txt` it was measured
    # from a redirected stdout and falls back to 80 while stderr is 40 columns
    # wide, and a progress line wider than the real terminal wraps -- at which
    # point ERASE clears the last physical row of a two-row line and the
    # corruption is the unrecoverable kind the one-row design exists to
    # prevent.
    stderr_fd = _measurable_fd(sys.stderr)
    # Progress belongs to the human report. The spec's Progress section sits
    # inside the human-report design, and an agent has no use for narration it
    # would then have to filter out -- so agent mode's stderr stays exactly
    # what it was before this branch: the two preamble lines and the summary.
    # Gating on the format rather than on stderr's TTY also keeps
    # `--report agent` quiet at a terminal, which is the conservative
    # direction for a contract we have promised not to move.
    #
    # Which stream is a terminal remains a separate question from that: the
    # report is the payload and goes to stdout, the live line is ephemeral and
    # goes to stderr, so a human redirecting the report still sees the run
    # move.
    narrate = fmt == "human"
    progress = LiveRegion(
        sys.stderr,
        enabled=(
            narrate
            and not args.no_progress
            and sys.stderr.isatty()
            and os.environ.get("TERM", "dumb") != "dumb"
            and not is_ci(os.environ)
        ),
        clock=time.perf_counter,
    )

    with profiler.span("discovery"):
        recognised = looks_like_pytest_project(project_dir)
    if not recognised:
        print(
            f"moonbuggy: {project_dir} does not look like a pytest project "
            "(no pytest.ini, pyproject.toml, conftest.py or test_*.py found). "
            "Run moonbuggy from your project root, or pass --project.",
            file=sys.stderr,
        )
        return 2

    with profiler.span("discovery"):
        # Before generation, so a failure to resolve the ref costs nothing and
        # so the file-level half of the filter can drop whole files before
        # anything is parsed. Raises DiffScopeError, which `main` turns into
        # exit 2 with a message.
        scope = scope_since(args.since, project_dir) if args.since else None
        source_dir = (
            Path(args.source).resolve() if args.source else find_source_dir(project_dir)
        )
        source_files = find_source_files(
            source_dir, project_dir, args.include, args.exclude
        )
        if scope is not None:
            # Composed with --include/--exclude, not a replacement for them:
            # this narrows whatever they left.
            source_files = [name for name in source_files if scope.touches(name)]

    output_dir = project_dir / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if not source_files:
        if scope is not None:
            # A pull request that touched only docs, tests or a config file is
            # a normal, passing state -- not a run that could not happen. It
            # exits 0 and still leaves the artifacts a full run leaves, so a
            # CI step reading them does not have to special-case the empty PR.
            return _nothing_in_scope(scope, output_dir, project_dir)
        print("moonbuggy: no source files to mutate after filtering.", file=sys.stderr)
        return 2
    with profiler.span("cache I/O"):
        cache = _prepare_cache(args, output_dir)

    wanted = set(args.operators.split(",")) if args.operators else None
    with profiler.span("generation"):
        mutants, unreadable = _collect_mutants(
            project_dir, source_files, wanted, progress
        )
        if scope is not None:
            # The line-level half. Generation is untouched -- it produced
            # exactly the mutants it always does, and this keeps the ones
            # standing on a changed line.
            mutants = [m for m in mutants if scope.contains(m.module, m.line)]

    if not mutants and scope is not None and not unreadable:
        return _nothing_in_scope(scope, output_dir, project_dir)

    if not mutants:
        if unreadable:
            # Every file was skipped. Saying "no mutants" here would read as
            # "your code is fully covered" rather than "moonbuggy read nothing".
            noun = "file" if len(unreadable) == 1 else "files"
            print(
                f"moonbuggy: none of the {len(unreadable)} source {noun} could be "
                "read as Python, so there is nothing to mutate. "
                "See the messages above.",
                file=sys.stderr,
            )
        else:
            print("moonbuggy: no mutants generated.", file=sys.stderr)
        return 2

    # Resolved before the run rather than after it, for two reasons: matching
    # needs the mutants (a relocated entry is found by content among them,
    # not among records), and a broken ledger must stop the run at once rather
    # than after several minutes of work whose verdict it would then decide.
    accept_path = _accept_path(args, project_dir)
    resolution = resolve_accepted(load_accepted(accept_path), mutants)
    reasons = resolution.reasons()

    if not args.quiet:
        if scope is not None:
            # Said up front as well as in the footer, and in both report
            # formats: the count on the next line is a scoped count, and a
            # reader who learns that only at the end has already read it as
            # the whole codebase.
            progress.log(f"moonbuggy: {scope.describe()}")
        progress.log(
            f"moonbuggy: {len(mutants)} mutants across {len(source_files)} files"
        )
        progress.log("moonbuggy: running coverage pass...")

    jsonl_path = output_dir / "results.jsonl"
    # Outside the try so the `finally` can name it however far the run got.
    counts_so_far: Counter[str] = Counter()

    # Records are streamed to disk as each mutant is settled, so a run killed
    # mid-flight leaves whole, parseable lines for everything it did finish
    # (criterion M1.4.13) rather than an empty file or a truncated one.
    try:
        with StreamingJSONL(jsonl_path, reasons) as stream:
            last_milestone = 0.0

            def _settled(result: Result) -> None:
                nonlocal last_milestone
                stream.write(result)
                counts_so_far[result.status] += 1
                done = sum(counts_so_far.values())
                elapsed = time.perf_counter() - started
                if progress.enabled:
                    # Re-measured on every repaint rather than through
                    # SIGWINCH, which does not exist on Windows. `--width` is
                    # deliberately not passed: it is a REPORT width, there so
                    # a report is reproducible, and honouring it here would
                    # let `--width 100` wrap the live line on an 80-column
                    # terminal.
                    region_width = resolve_width(None, os.environ, stderr_fd)
                    line = (
                        f"moonbuggy  {done}/{len(mutants)}  "
                        + "  ".join(
                            f"{status.lower()} {counts_so_far[status]}"
                            for status in ("KILLED", "SURVIVED", "TIMEOUT")
                            if counts_so_far[status]
                        )
                        + f"  {_clock(elapsed)}"
                    )
                    progress.tick(line[: region_width - 1])
                    if result.status == "SURVIVED":
                        # Survivors are rare and are the whole point, so they
                        # scroll into the scrollback as they land. Killed
                        # mutants never do. Guarded on the region, because a
                        # disabled region still commits what it is given --
                        # and a bare `SURVIVED  path:line` on the agent path
                        # is a line that opens with a contract keyword and
                        # carries none of its key=value tokens.
                        progress.log(
                            f"SURVIVED  {result.mutant.module}:{result.mutant.line}"
                        )
                elif (
                    narrate
                    and not args.quiet
                    # The last result's milestone would be word for word the
                    # durable line `close` is about to commit, so the run
                    # would end by saying the same thing twice.
                    and done < len(mutants)
                    and elapsed - last_milestone >= MILESTONE_INTERVAL
                ):
                    # No live region to watch, so progress arrives as committed
                    # lines instead. Paced, because they are permanent.
                    last_milestone = elapsed
                    progress.log(
                        _settled_line(done, len(mutants), counts_so_far, elapsed)
                    )

            if args.workers:
                # xdist needs real subprocesses, so the warm single-pass
                # session does not apply; fall back to the separate baseline
                # pass and cold forks.
                linemap, flaky = run_baseline_pass(
                    project_dir,
                    source_dir,
                    args.flaky_probe,
                    extra_args=args.pytest_arg,
                )
                results = run_mutants(
                    project_dir,
                    mutants,
                    linemap,
                    timeout=args.timeout,
                    xdist_workers=args.workers,
                    cache=cache,
                    jobs=args.jobs or None,
                    flaky=flaky,
                    on_result=_settled,
                )
            else:
                _, results = run_session(
                    project_dir,
                    mutants,
                    source_dir,
                    timeout=args.timeout,
                    cache=cache,
                    jobs=args.jobs or None,
                    probes=args.flaky_probe,
                    on_result=_settled,
                    extra_args=args.pytest_arg,
                )
    finally:
        # `run()` exits via `os._exit`, which skips `atexit`, so this
        # `finally` is the only teardown that runs -- the live line must be
        # erased before the report prints, whether or not an exception
        # unwinds through here.
        #
        # One durable `\n`-terminated line goes with it, so the scrollback
        # keeps a record of how the run ended once the live line is gone.
        # Suppressed under --quiet, whose contract is the summary and nothing
        # else.
        progress.close(
            None
            if not narrate or args.quiet
            else _settled_line(
                sum(counts_so_far.values()),
                len(mutants),
                counts_so_far,
                time.perf_counter() - started,
            )
        )

    with profiler.span("reporting"):
        # Rewritten in canonical mutant order. The streamed file is valid at
        # every instant, but it arrives in completion order, and the reported
        # order has to be stable for unchanged source (criterion C3).
        write_jsonl(results, jsonl_path, reasons)

        # Derived from the JSONL that was just written, not from the in-memory
        # results, so the two artifacts cannot disagree (criterion E3).
        records = read_jsonl(jsonl_path)
        text_path = output_dir / "results.txt"
        text_path.write_text(plaintext_from_records(records) + "\n", encoding="utf-8")

        acceptance = tally_accepted(
            records,
            resolution,
            path=_display_path(accept_path, project_dir),
            gating=args.fail_on_unexplained,
        )

        if fmt == "human":
            if args.quiet:
                # --quiet in human mode is the footer, not silence. The agent
                # path still prints its stderr summary under --quiet, so
                # without this quiet-human would be the only mode that reports
                # nothing at all.
                print(
                    render_footer(
                        summarise(records),
                        time.perf_counter() - started,
                        _display_path(jsonl_path, project_dir),
                        scope=scope,
                        acceptance=acceptance,
                    )
                )
            else:
                print(
                    render_report(
                        records,
                        palette=palette,
                        files=len(source_files),
                        elapsed=time.perf_counter() - started,
                        timeout=args.timeout,
                        artifact=_display_path(jsonl_path, project_dir),
                        width=width,
                        scope=scope,
                        acceptance=acceptance,
                    )
                )
        elif not args.quiet:
            for record in records:
                print(render_line(record))

    with profiler.span("cache I/O"):
        if cache is not None:
            cache.save()

    profiler.note("mutants", len(mutants))
    profiler.note("source_files", len(source_files))
    profiler.write()

    counts = summarise(records)
    # The report's own footer already summarises a human run, so this line
    # would be redundant there. In agent mode it stays exactly as it always
    # was -- Task 11's golden test pins it byte for byte -- so the ledger's
    # numbers go on a second line rather than into that one, and only when
    # there is a ledger to report.
    if fmt == "agent":
        print(
            "moonbuggy: "
            + "  ".join(f"{status}={count}" for status, count in sorted(counts.items()))
            + f"  cached={sum(1 for r in results if r.from_cache)}"
            + f"  -> {_display_path(jsonl_path, project_dir)}",
            file=sys.stderr,
        )
        if _has_ledger(acceptance):
            print(_ledger_line(acceptance), file=sys.stderr)
    for warning in _ledger_warnings(acceptance):
        print(warning, file=sys.stderr)

    if args.fail_on_unexplained:
        # The whole point of the flag: a survivor a human has reviewed and
        # explained is not a reason to fail a build, and a stale acceptance is
        # not an explanation -- `tally` has already put those back among the
        # unexplained.
        return 1 if acceptance.unexplained else 0
    # NO_COVERAGE counts exactly as SURVIVED does. It is a finding -- nothing
    # exercises the line -- so a CI gate that failed on survivors before the
    # status existed must not start passing because its findings were renamed.
    # Acceptances do not enter this branch at all: adding a ledger must never
    # change an existing gate's answer without being asked to.
    return 1 if counts["SURVIVED"] or counts["NO_COVERAGE"] else 0


def _nothing_in_scope(
    scope: DiffScope,
    output_dir: Path,
    project_dir: Path,
) -> int:
    """Finish a diff-scoped run that found no changed source lines.

    Exit 0, not 2. A pull request touching only docs or tests genuinely has
    nothing for moonbuggy to mutate, and failing the gate for it would teach
    everyone to stop running the gate. The empty artifacts are still written,
    so a CI step that reads `results.jsonl` finds an empty file rather than the
    previous run's verdicts -- stale results being the one outcome worse than
    none.

    Args:
        scope: the run's diff scope, named in the message.
        output_dir: where the artifacts go.
        project_dir: the project root, for shortening the artifact path.

    Returns:
        0.
    """
    jsonl_path = output_dir / "results.jsonl"
    jsonl_path.write_text("", encoding="utf-8")
    (output_dir / "results.txt").write_text("", encoding="utf-8")
    print(
        f"moonbuggy: no changed source lines since {scope.ref} "
        f"(merge base {scope.merge_base[:7]}), so there is nothing to mutate. "
        f"Empty results in {_display_path(jsonl_path, project_dir)}",
        file=sys.stderr,
    )
    # Nothing on stdout on purpose. stdout is the report, and in agent format
    # every line of it begins with a status keyword; a prose line explaining
    # the empty run would be the one line a parser cannot read.
    return 0


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


def _display_path(path: Path, project_dir: Path) -> str:
    # `--output-dir` may be an absolute path, in which case
    # `project_dir / args.output_dir` silently discards `project_dir` (an
    # absolute right operand replaces the left one under `/`), so `path` ends
    # up outside `project_dir` and `relative_to` raises. That is an
    # anticipated shape of input, not a crash-worthy one (criterion H5): a
    # user running `moonbuggy --output-dir /tmp/whatever` still gets a usable
    # line, just the absolute path instead of a shortened relative one.
    try:
        return str(path.relative_to(project_dir))
    except ValueError:
        return str(path)


def _collect_mutants(
    project_dir: Path,
    source_files: list[str],
    wanted: set[str] | None,
    progress: LiveRegion,
) -> tuple[list[Mutant], list[str]]:
    """Generate mutants for every readable source file.

    One unparseable or undecodable file must not end the run: the other files
    are still perfectly good input, and a project with one broken module is a
    normal state during editing (criteria M1.4.1 and M1.4.7). The skip is
    announced per file rather than summarised, so it is impossible to mistake a
    skipped file for a file with no mutants.

    Args:
        project_dir: the project root.
        source_files: paths relative to the project root.
        wanted: a set of operator names to keep, or None for all.
        progress: the live progress region, so these messages go through the
            same single writer as everything else printed to stderr while it
            is open.

    Returns:
        ``(mutants, unreadable)`` -- the mutants found, and the relative paths that
            were skipped.
    """
    mutants: list[Mutant] = []
    unreadable: list[str] = []
    for relative in source_files:
        skipped: list[int] = []

        def _note_skip(line: int, why: str, skipped: list[int] = skipped) -> None:
            # `skipped=skipped` is the usual late-binding fix for a closure
            # inside a loop -- captures THIS iteration's list rather than
            # whatever `skipped` is bound to when `generate_mutants` calls
            # back, even though nothing here is async or deferred past the
            # loop body. A plain `def` in place of the original lambda, since
            # a lambda's parameters cannot carry annotations.
            skipped.append(line)

        try:
            source = read_source(project_dir / relative)
            found = generate_mutants(
                source,
                module=relative,
                on_skip=_note_skip,
            )
        except (SourceError, GenerationError) as error:
            progress.log(f"moonbuggy: skipping {relative}: {error}")
            unreadable.append(relative)
            continue
        if skipped:
            progress.log(
                f"moonbuggy: {relative}: {len(skipped)} site(s) too deeply nested "
                f"to mutate, first at line {min(skipped)}. Those lines are not "
                "covered by this run."
            )
        mutants.extend(m for m in found if wanted is None or m.operator in wanted)
    return mutants, unreadable


def _prepare_cache(args: argparse.Namespace, output_dir: Path) -> ResultCache | None:
    """The results cache for this run, or None under `--no-cache`.

    The fingerprint is what stops a run being served the previous run's
    verdicts after its command line changed -- `--pytest-arg` reaches every
    pytest run here, baseline included, so it decides which tests exist and
    whether they pass. `sys.executable` is the interpreter both run paths end
    up using: `run_mutants` falls back to it, and the warm session is this
    process.

    Args:
        args: the parsed command line.
        output_dir: where the cache file lives.

    Returns:
        A :class:`~moonbuggy.cache.ResultCache`, or None if caching is off.
    """
    cache = ResultCache(
        output_dir / "cache.json",
        fingerprint=run_fingerprint(
            args.pytest_arg, timeout=args.timeout, python=sys.executable
        ),
    )
    if args.clear_cache:
        cache.clear()
    return None if args.no_cache else cache


def _accept_path(args: argparse.Namespace, project_dir: Path) -> Path:
    # Relative to the project root, not to the cwd, so `moonbuggy --project x`
    # and `cd x && moonbuggy` read the same ledger. An absolute --accept-file
    # replaces the root under `/`, which is the behaviour someone passing one
    # is asking for.
    return project_dir / (args.accept_file or DEFAULT_ACCEPT_FILE)


def _has_ledger(acceptance: Acceptance) -> bool:
    # "Is there anything to say about the ledger?" -- false for the
    # overwhelmingly common case of a project that has never accepted
    # anything, which must not gain a line of output for a file it does not
    # have.
    return bool(
        acceptance.accepted
        or acceptance.stale
        or acceptance.ambiguous
        or acceptance.orphaned
        or acceptance.relocated
    )


def _ledger_line(acceptance: Acceptance) -> str:
    # The agent format's second summary line: key=value, like every other line
    # a parser reads, and printed only when a ledger exists.
    summary = acceptance.summary()
    return "moonbuggy: " + "  ".join(
        [
            f"accepted={summary['accepted']}",
            f"unexplained={summary['unexplained']}",
            f"stale={summary['stale']}",
            f"-> {acceptance.path}",
        ]
    )


def _ledger_warnings(acceptance: Acceptance) -> list[str]:
    """Everything about the ledger that needs saying out loud.

    Stale and ambiguous acceptances are printed in both report formats and
    under `--quiet`, because both mean a mutant is being reported that somebody
    believes they already dealt with. That is exactly the surprise a run must
    not keep to itself.

    Args:
        acceptance: the run's ledger outcome.

    Returns:
        Zero or more lines for stderr.
    """
    lines = []
    for entry in acceptance.stale:
        lines.append(
            f"moonbuggy: the acceptance for {entry.id} is stale -- that line "
            "has changed since it was accepted, so it is reported as "
            "unexplained. Re-review it and `moonbuggy accept` it again if it "
            "is still equivalent."
        )
    for entry in acceptance.ambiguous:
        lines.append(
            f"moonbuggy: the acceptance for {entry.id} matches more than one "
            f"mutant in {entry.file}, so it was not applied to any of them. "
            "Accept the mutant you mean by its current id."
        )
    return lines


def _accept(args: argparse.Namespace) -> int:
    """Add to, list, or remove from the accepted-equivalents ledger.

    Args:
        args: the parsed `moonbuggy accept` command line.

    Returns:
        The process exit code: 0 on success, 2 for anything the user has to
        fix -- an id no run produced, an entry that is not there.
    """
    project_dir = Path(args.project).resolve()
    path = _accept_path(args, project_dir)
    entries = list(load_accepted(path))

    if args.list:
        return _accept_list(entries, _display_path(path, project_dir))
    if not args.mutant_id:
        print(
            "moonbuggy: accept needs a mutant id (or --list). "
            "Ids are the `id=...` token on each result line.",
            file=sys.stderr,
        )
        return 2
    if args.remove:
        return _accept_remove(entries, args.mutant_id, path, project_dir)
    if not args.reason:
        print(
            "moonbuggy: accept needs --reason. An acceptance without one is a "
            "claim nobody can check and nobody will revisit.",
            file=sys.stderr,
        )
        return 2
    return _accept_add(entries, args, path, project_dir)


def _accept_list(entries: list[Entry], path: str) -> int:
    # One line per entry, `id` first and key=value after it, so the ledger
    # greps the same way results.txt does.
    if not entries:
        print(f"moonbuggy: no accepted mutants in {path}", file=sys.stderr)
        return 0
    for entry in entries:
        print(
            f"{entry.id}  operator={entry.operator}  "
            f"accepted_at={entry.accepted_at}  "
            f"reason={entry.reason}"
        )
    return 0


def _accept_remove(
    entries: list[Entry], mutant_id: str, path: Path, project_dir: Path
) -> int:
    kept = [entry for entry in entries if entry.id != mutant_id]
    if len(kept) == len(entries):
        print(
            f"moonbuggy: no entry for {mutant_id} in "
            f"{_display_path(path, project_dir)}",
            file=sys.stderr,
        )
        return 2
    save_accepted(path, kept)
    print(
        f"moonbuggy: removed {mutant_id} from {_display_path(path, project_dir)}. "
        "It is reported as a finding again from the next run.",
        file=sys.stderr,
    )
    return 0


def _accept_add(
    entries: list[Entry], args: argparse.Namespace, path: Path, project_dir: Path
) -> int:
    # The mutation's text comes from the last run's records rather than from
    # re-generating mutants: it is the run the human just read, and it is what
    # the fingerprint has to be taken from for the acceptance to mean "this
    # mutation, as reviewed".
    results = project_dir / args.output_dir / "results.jsonl"
    if not results.exists():
        print(
            f"moonbuggy: no results at {_display_path(results, project_dir)}. "
            "Run moonbuggy first -- an acceptance records a decision about a "
            "mutant a run produced.",
            file=sys.stderr,
        )
        return 2
    record = find_record(read_jsonl(results), args.mutant_id)
    if record is None:
        print(
            f"moonbuggy: no mutant with id {args.mutant_id} in "
            f"{_display_path(results, project_dir)}",
            file=sys.stderr,
        )
        return 2

    entry = entry_for(
        record["id"],
        record["file"],
        record["operator"],
        record["original"],
        record["mutated"],
        reason=args.reason,
    )
    existed = path.exists()
    kept = [e for e in entries if e.id != entry.id]
    save_accepted(path, [*kept, entry])

    verb = "updated" if len(kept) != len(entries) else "accepted"
    print(
        f"moonbuggy: {verb} {entry.id} in {_display_path(path, project_dir)}. "
        "It still runs and is still reported; it is counted separately.",
        file=sys.stderr,
    )
    if record["status"] not in FINDING_STATUSES:
        # Accepting a killed mutant is not an error -- the ledger is about the
        # mutation, and a test may stop killing it tomorrow -- but silence here
        # would let someone believe they had explained away a finding they had
        # not.
        print(
            f"moonbuggy: note -- {entry.id} was {record['status']} in that run, "
            "so the acceptance does nothing until it survives.",
            file=sys.stderr,
        )
    if not existed and is_git_ignored(path):
        print(
            f"moonbuggy: warning -- git ignores {_display_path(path, project_dir)}, "
            "so this decision will not be committed and the next clone will "
            "not have it. The ledger is meant to be in version control: in "
            ".gitignore, replace `.moonbuggy/` with `.moonbuggy/*` followed by "
            "`!.moonbuggy/accepted.toml`.",
            file=sys.stderr,
        )
    return 0


def _show(args: argparse.Namespace) -> int:
    path = Path(args.output_dir) / "results.jsonl"
    if not path.exists():
        path = Path(".") / args.output_dir / "results.jsonl"
    if not path.exists():
        print(f"moonbuggy: no results at {path}. Run moonbuggy first.", file=sys.stderr)
        return 2

    record = find_record(read_jsonl(path), args.mutant_id)
    if record is None:
        print(f"moonbuggy: no mutant with id {args.mutant_id}", file=sys.stderr)
        return 2

    # The diff lives here rather than in the plaintext view, which stays one
    # line per mutant (criterion E5/E7).
    print(f"id           {record['id']}")
    print(f"status       {record['status']}")
    print(f"location     {record['file']}:{record['line']}")
    print(f"operator     {record['operator']}")
    print(f"nearest_test {record['nearest_test'] or '-'}")
    print(f"tests_run    {record['tests_run']}")
    print("diff")
    for line in record["diff"].splitlines():
        print(f"  {line}")
    return 0


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


if __name__ == "__main__":
    run()
