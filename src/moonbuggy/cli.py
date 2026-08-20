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
from .baseline import BaselineError
from .cache import ResultCache, run_fingerprint
from .coverage_pass import CoveragePassError, run_baseline_pass
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
    except (LayoutError, CoveragePassError, BaselineError, SourceError) as error:
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
    return parser


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
        source_dir = (
            Path(args.source).resolve() if args.source else find_source_dir(project_dir)
        )
        source_files = find_source_files(
            source_dir, project_dir, args.include, args.exclude
        )
    if not source_files:
        print("moonbuggy: no source files to mutate after filtering.", file=sys.stderr)
        return 2

    output_dir = project_dir / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    with profiler.span("cache I/O"):
        cache = _prepare_cache(args, output_dir)

    wanted = set(args.operators.split(",")) if args.operators else None
    with profiler.span("generation"):
        mutants, unreadable = _collect_mutants(
            project_dir, source_files, wanted, progress
        )

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

    if not args.quiet:
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
        with StreamingJSONL(jsonl_path) as stream:
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
        write_jsonl(results, jsonl_path)

        # Derived from the JSONL that was just written, not from the in-memory
        # results, so the two artifacts cannot disagree (criterion E3).
        records = read_jsonl(jsonl_path)
        text_path = output_dir / "results.txt"
        text_path.write_text(plaintext_from_records(records) + "\n", encoding="utf-8")

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
    # was -- Task 11's golden test pins it byte for byte.
    if fmt == "agent":
        print(
            "moonbuggy: "
            + "  ".join(f"{status}={count}" for status, count in sorted(counts.items()))
            + f"  cached={sum(1 for r in results if r.from_cache)}"
            + f"  -> {_display_path(jsonl_path, project_dir)}",
            file=sys.stderr,
        )
    # NO_COVERAGE counts exactly as SURVIVED does. It is a finding -- nothing
    # exercises the line -- so a CI gate that failed on survivors before the
    # status existed must not start passing because its findings were renamed.
    return 1 if counts["SURVIVED"] or counts["NO_COVERAGE"] else 0


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
