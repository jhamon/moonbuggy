"""The full-run subcommand and its orchestration helpers."""

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

from .. import profiling
from ..accepted import (
    Acceptance,
)
from ..accepted import load as load_accepted
from ..accepted import resolve as resolve_accepted
from ..accepted import tally as tally_accepted
from ..cache import ResultCache, run_fingerprint
from ..coverage_pass import run_baseline_pass
from ..diffscope import DiffScope, scope_since, scope_summary
from ..discover import (
    find_source_dir,
    find_source_files,
    looks_like_pytest_project,
)
from ..generate import GenerationError, generate_mutants
from ..humanreport import count_logging_skipped, render_footer, render_report
from ..logging_policy import LoggingPolicy
from ..mutant import Mutant
from ..operators import (
    resolve_operators,
)
from ..report import (
    StreamingJSONL,
    plaintext_from_records,
    read_jsonl,
    render_line,
    run_summary,
    summarise,
    write_jsonl,
    write_summary,
)
from ..runner import Result, run_mutants, run_session
from ..srcio import SourceError, read_source
from ..terminal import (
    LiveRegion,
    is_ci,
    palette_for,
    resolve_colour,
    resolve_format,
    resolve_width,
)
from .accept import _has_ledger, _ledger_line, _ledger_warnings
from .common import (
    _accept_path,
    _clock,
    _display_path,
    _logging_policy,
    _measurable_fd,
    _settled_line,
)
from .constants import MILESTONE_INTERVAL


def _run(args: argparse.Namespace) -> int:
    project_dir = Path(args.project).resolve()
    profiler = profiling.active()
    started = time.perf_counter()

    # Resolved first, before any file is read: a typo in `--operators` is a
    # command line that cannot mean anything, and finding that out after a
    # coverage pass is a minute wasted for no reason. Raises SelectionError,
    # which `main` turns into exit 2 with a message.
    _wanted_operators(args)

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
            return _nothing_in_scope(args, scope, output_dir, project_dir)
        print("moonbuggy: no source files to mutate after filtering.", file=sys.stderr)
        return 2
    with profiler.span("cache I/O"):
        cache = _prepare_cache(args, output_dir)

    wanted = _wanted_operators(args)
    logging_policy = _logging_policy(args)
    with profiler.span("generation"):
        mutants, unreadable = _collect_mutants(
            project_dir, source_files, wanted, progress, logging_policy
        )
        if scope is not None:
            # The line-level half. Generation is untouched -- it produced
            # exactly the mutants it always does, and this keeps the ones
            # standing on a changed line.
            mutants = [m for m in mutants if scope.contains(m.module, m.line)]

    if not mutants and scope is not None and not unreadable:
        return _nothing_in_scope(args, scope, output_dir, project_dir)

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

    jsonl_path, results = _execute_mutants(
        project_dir=project_dir,
        source_dir=source_dir,
        output_dir=output_dir,
        mutants=mutants,
        cache=cache,
        reasons=reasons,
        progress=progress,
        narrate=narrate,
        stderr_fd=stderr_fd,
        args=args,
        started=started,
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

        counts = summarise(records)
        elapsed = time.perf_counter() - started
        # Decided before anything is printed, because the summary reports it.
        # A consumer reading summary.json after the fact then gets the gate's
        # answer without re-deriving it from the counts -- which is exactly the
        # derivation `--fail-on-unexplained` changes the rules of.
        code = _exit_code(counts, acceptance, args.fail_on_unexplained)
        summary = run_summary(
            records,
            elapsed=elapsed,
            cached=sum(1 for r in results if r.from_cache),
            config=_effective_config(args),
            scope=scope_summary(scope),
            acceptance=acceptance.summary(),
            exit_code=code,
        )
        # Written on every run, not only under --json: a results directory
        # somebody finds later should say what produced it. It is a separate
        # file rather than a line in results.jsonl because a run has exactly
        # one summary and that file has exactly one kind of line -- a mutant
        # record -- which is worth more than saving a file.
        write_summary(summary, output_dir / "summary.json")

        if args.json:
            # stdout is exactly one JSON object. The per-mutant view is not
            # lost -- it is in results.txt and results.jsonl, as always -- and
            # printing it here as well would leave stdout something no parser
            # could read whole.
            print(json.dumps(summary, sort_keys=True))
        elif fmt == "human":
            if args.quiet:
                # --quiet in human mode is the footer, not silence. The agent
                # path still prints its stderr summary under --quiet, so
                # without this quiet-human would be the only mode that reports
                # nothing at all.
                print(
                    render_footer(
                        counts,
                        elapsed,
                        _display_path(jsonl_path, project_dir),
                        scope=scope,
                        acceptance=acceptance,
                        logging_skipped=count_logging_skipped(records),
                    )
                )
            else:
                print(
                    render_report(
                        records,
                        palette=palette,
                        files=len(source_files),
                        elapsed=elapsed,
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

    # The report's own footer already summarises a human run, so this line
    # would be redundant there. In agent mode it stays exactly as it always
    # was -- Task 11's golden test pins it byte for byte -- so the ledger's
    # numbers go on a second line rather than into that one, and only when
    # there is a ledger to report.
    if fmt == "agent" and not args.json:
        print(
            "moonbuggy: "
            + "  ".join(f"{status}={count}" for status, count in sorted(counts.items()))
            + f"  cached={summary['cached']}"
            + f"  -> {_display_path(jsonl_path, project_dir)}",
            file=sys.stderr,
        )
        if _has_ledger(acceptance):
            print(_ledger_line(acceptance), file=sys.stderr)
    for warning in _ledger_warnings(acceptance):
        print(warning, file=sys.stderr)

    return code


def _execute_mutants(
    *,
    project_dir: Path,
    source_dir: Path,
    output_dir: Path,
    mutants: list[Mutant],
    cache: ResultCache | None,
    reasons: dict[str, str],
    progress: LiveRegion,
    narrate: bool,
    stderr_fd: int | None,
    args: argparse.Namespace,
    started: float,
) -> tuple[Path, list[Result]]:
    """Run every mutant, streaming verdicts to results.jsonl as they settle.

    Each settled verdict is streamed to disk exactly once here,
    and every later artifact --- results.txt, summary.json, the
    in-order rewrite --- is derived from that single file, so the artifacts of
    one run cannot disagree. Split out of ``_run`` because
    this is the phase with the most complex surface --- progress streaming,
    the warm-session vs xdist forks --- and none of it reads or writes state
    a caller of this function cannot simply pass in.

    Args:
        project_dir: project root.
        source_dir: directory coverage is measured against.
        output_dir: where the run's artifacts live.
        mutants: every mutant to run, in report order.
        cache: the run's result cache, or None.
        reasons: acceptance reasons, used to annotate streamed records.
        progress: the live-region progress renderer for stderr.
        narrate: whether human progress narration is wanted at all.
        stderr_fd: the real stderr fd the live line is measured against.
        args: the parsed ``moonbuggy run`` command line.
        started: the monotonic clock at the start of the run, for elapsed
            reporting.

    Returns:
        ``(jsonl_path, results)``. ``jsonl_path`` is returned rather than
        re-derived so the one source naming the streamed file is the one
        built here.
    """
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

    return jsonl_path, results


def _exit_code(counts: dict[str, int], acceptance: Acceptance, gating: bool) -> int:
    """The process exit code for a completed run.

    Args:
        counts: the run's counts per status.
        acceptance: the run's ledger outcome.
        gating: whether `--fail-on-unexplained` was passed.

    Returns:
        1 if the run has something to fail for, 0 otherwise.
    """
    if gating:
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


def _wanted_operators(args: argparse.Namespace) -> set[str] | None:
    """The operator names this run will keep, or None for the default tier.

    None rather than the expanded default set, so that "the user did not
    choose" stays distinguishable from "the user typed out today's default
    tier by hand" -- `_effective_config` records the selector verbatim, and
    those two runs should not read identically a year from now.

    Args:
        args: the parsed command line.

    Returns:
        The resolved names, or None when `--operators` was not given, which
        `generate_mutants` reads as the `default` tier.

    Raises:
        SelectionError: if the selection names something unknown or resolves
            to no operators.
    """
    if not args.operators:
        return None
    return set(resolve_operators(args.operators))


def _effective_config(args: argparse.Namespace) -> dict[str, object]:
    """The run's configuration, as the run actually resolved it.

    This is what makes a results directory self-describing: a file somebody
    finds a week later says which operators produced it, which paths were in
    and out, and what pytest was told -- the same inputs the cache key covers,
    so two results files that disagree can be told apart by their inputs
    rather than by guesswork.

    `--since` is deliberately absent: how a run reached a mutant is scope, not
    configuration, and it is reported under `scope` by `scope_summary`.

    Args:
        args: the parsed command line.

    Returns:
        A JSON-serialisable mapping of the effective configuration.
    """
    wanted = _wanted_operators(args)
    return {
        # The *resolved* set, sorted, because that is what the run actually
        # did: `--operators deep` and `--operators +boundary` say nothing to a
        # consumer about which operators produced these results, and a version
        # from six months hence would resolve them differently.
        #
        # None rather than the expanded list when no selection was made,
        # because "all of them" and "all of the ones that existed in that
        # version" are different claims and only the first is one this run
        # made.
        "operators": sorted(wanted) if wanted is not None else None,
        # And the shorthand as typed, because "why did this run use these
        # seven?" is answered by the selector and not by its expansion.
        "operators_selector": args.operators,
        "include": list(args.include),
        "exclude": list(args.exclude),
        "pytest_args": list(args.pytest_arg),
        "timeout": args.timeout,
        "jobs": args.jobs,
        "workers": args.workers,
        "flaky_probe": args.flaky_probe,
        "cache": not args.no_cache,
        "include_logging_mutants": args.include_logging_mutants,
        # The names this run *added*, not the effective set. Same reasoning as
        # `operators` above: "the built-in names plus audit" and "the built-in
        # names of that version plus audit" are different claims, and only the
        # first is one this run made.
        "logger_names": list(args.logger_name),
    }


def _nothing_in_scope(
    args: argparse.Namespace,
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
        args: the parsed command line, for the effective configuration the
            summary reports and for `--json`.
        scope: the run's diff scope, named in the message.
        output_dir: where the artifacts go.
        project_dir: the project root, for shortening the artifact path.

    Returns:
        0.
    """
    jsonl_path = output_dir / "results.jsonl"
    jsonl_path.write_text("", encoding="utf-8")
    (output_dir / "results.txt").write_text("", encoding="utf-8")
    # A summary too, for the same reason as the empty results: a consumer that
    # reads it must find this run's zeroes rather than the previous run's
    # numbers. The ledger is reported empty rather than loaded -- nothing ran,
    # so nothing could be accepted or unexplained, and a run that mutates
    # nothing must not start failing over a file it never needed to read.
    summary = run_summary(
        [],
        elapsed=0.0,
        cached=0,
        config=_effective_config(args),
        scope=scope_summary(scope),
        acceptance=Acceptance(
            path=_display_path(_accept_path(args, project_dir), project_dir),
            accepted=(),
            unexplained=(),
            stale=(),
            ambiguous=(),
            orphaned=(),
            relocated={},
            gating=args.fail_on_unexplained,
        ).summary(),
        exit_code=0,
    )
    write_summary(summary, output_dir / "summary.json")
    print(
        f"moonbuggy: no changed source lines since {scope.ref} "
        f"(merge base {scope.merge_base[:7]}), so there is nothing to mutate. "
        f"Empty results in {_display_path(jsonl_path, project_dir)}",
        file=sys.stderr,
    )
    if args.json:
        # The one thing that does belong on this path's stdout: a consumer
        # that asked for an object every run must not get an empty stream for
        # the PR that happened to touch no source.
        print(json.dumps(summary, sort_keys=True))
    # Otherwise nothing on stdout, on purpose. stdout is the report, and in
    # agent format every line of it begins with a status keyword; a prose line
    # explaining the empty run would be the one line a parser cannot read.
    return 0


def _collect_mutants(
    project_dir: Path,
    source_files: list[str],
    wanted: set[str] | None,
    progress: LiveRegion,
    logging_policy: LoggingPolicy,
) -> tuple[list[Mutant], list[str]]:
    """Generate mutants for every readable source file.

    One unparseable or undecodable file must not end the run: the other files
    are still perfectly good input, and a project with one broken module is a
    normal state during editing. The skip is
    announced per file rather than summarised, so it is impossible to mistake a
    skipped file for a file with no mutants.

    Args:
        project_dir: the project root.
        source_files: paths relative to the project root.
        wanted: a set of operator names to keep, or None for all.
        progress: the live progress region, so these messages go through the
            same single writer as everything else printed to stderr while it
            is open.
        logging_policy: which calls count as logging calls, and whether their
            mutants are suppressed.

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
                logging_policy=logging_policy,
                operators=wanted,
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
        mutants.extend(found)
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
