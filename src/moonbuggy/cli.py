"""Command line interface.

Low floor, high ceiling (6.2): bare `moonbuggy` in a pytest project runs
end to end with no flags and no config file. Everything else is available and
nothing else is required.

Two artifacts are written per run, per 5.2: results.jsonl is canonical, and
results.txt is derived from it rather than authored alongside it, so they cannot
drift apart. A third, summary.json, describes the run rather than its mutants:
one object, versioned, carrying the counts and the configuration that produced
them. `--json` prints that same object to stdout.
"""

import argparse
import io
import json
import os
import sys
import textwrap
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
from .diffscope import DiffScope, DiffScopeError, scope_since, scope_summary
from .discover import (
    LayoutError,
    find_source_dir,
    find_source_files,
    looks_like_pytest_project,
)
from .generate import GenerationError, generate_mutants
from .humanreport import count_logging_skipped, render_footer, render_report
from .logging_policy import LoggingPolicy, policy_for
from .mutant import Mutant
from .operators import (
    ALL_TIER,
    COSTS,
    TIERS,
    SelectionError,
    describe_operators,
    resolve_operators,
    tier_members,
)
from .report import (
    FINDING_STATUSES,
    StreamingJSONL,
    find_record,
    plaintext_from_records,
    read_jsonl,
    record_for,
    render_line,
    run_summary,
    summarise,
    write_jsonl,
    write_summary,
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
from .verify import (
    Explanation,
    Verification,
    VerifyError,
    explain,
    resolve_targets,
    verify,
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


# `-h` is the one surface an agent reads before acting (#13), so anything it
# has to reason about before it can act belongs here rather than only in docs/.
#
# The first two sections are the output contract: the seven words a result line
# can begin with, and what the process exit code means. Every flag was already
# documented, but those two -- the only things an agent actually acts on --
# were discoverable only by running the tool and reading what came back.
# `KILLED` and `KILLED_BY_ERROR` did not appear in `-h` at all. The last two
# sections are the machinery behind a result line that a result line cannot
# show you: why a mutant was not re-measured, and why it ran the tests it ran.
_EPILOG = """\
Statuses:
  Every result line begins with one of these seven words, and the list is
  closed -- `summary.json` counts all seven on every run.

    KILLED           a selected test failed. The change was noticed.
    KILLED_BY_ERROR  a selected test errored rather than failed. Still a kill,
                     but it only proves the tests execute the line, not that
                     they check it.
    SURVIVED         every selected test passed with the change in place.
    NO_COVERAGE      no test reaches the line, so nothing could have killed it.
    TIMEOUT          the mutant ran past --timeout, usually a loop that no
                     longer ends.
    SUSPICIOUS       a test covering the line is flaky, so the verdict cannot
                     be trusted either way. See --flaky-probe.
    SKIPPED          never run: the line carries a `# moonbuggy: skip` marker,
                     or the mutant sits inside a logging call. See
                     --include-logging-mutants.

  SURVIVED and NO_COVERAGE are the *findings* -- the two that say something
  about your tests rather than about the run. They are what the exit code
  gates on, and the only two `moonbuggy accept` will speak for.

Exit codes:
  0    no findings.
  1    at least one SURVIVED or NO_COVERAGE. A result, not an error. TIMEOUT,
       SUSPICIOUS, SKIPPED and KILLED_BY_ERROR never cause it on their own.
  2    the run could not happen -- no package found, unparseable source, an
       unreadable ledger. Nothing was measured, so there is nothing to read.
  130  interrupted. Whatever finished is already in results.jsonl and valid.

  --fail-on-unexplained narrows 1 to the findings the ledger does not explain.
  Without it, a run whose every finding is accepted still exits 1.

Caching:
  A stored verdict is reused only when nothing it depends on has changed: the
  mutant itself, the full source of the module it mutates, the contents of
  every test file selected for it, and this run's --pytest-arg values,
  --timeout and interpreter. Editing a test file therefore invalidates every
  mutant that file was selected for. --jobs and -n/--workers are deliberately
  not part of the key -- they change how the work is scheduled, not the
  verdict. --no-cache bypasses the cache for one run; --clear-cache deletes it
  first.

Test selection:
  One instrumented pass over the unmutated suite builds a line -> test map,
  and each mutant then runs only the tests that execute its line. That is what
  `tests_run=` on a result line counts, and why it differs per mutant.
  tests_run=0 means no test reaches that line at all, which is reported as
  NO_COVERAGE rather than SURVIVED: nothing could have killed it. It does not
  work the other way round -- a SKIPPED mutant also shows tests_run=0, because
  it was suppressed before selection ever ran. The map is rebuilt every run
  and is never written to disk. `moonbuggy why <id>` prints the selection for
  one mutant without running anything.
"""

# Every command that reads or writes a results directory names it the same
# way, for the same reason `--accept-file` does: a path that means one thing
# to the writer and another to the reader fails silently.
#
# Two strings rather than one, because the writer and the readers do not mean
# the same thing by it. A run creates the artifacts; `show`, `run <id>`, `why`
# and `accept` only go looking for results.jsonl, and telling them the flag is
# about summary.json sends an agent to the wrong flag for the wrong reason.
_OUTPUT_DIR_HELP = (
    "where this run's artifacts go -- results.jsonl, results.txt, "
    "summary.json and cache.json "
    f"(default: {DEFAULT_OUTPUT_DIR}, relative to the project root)"
)

_READ_OUTPUT_DIR_HELP = (
    "where the last run left its artifacts; this command reads "
    f"results.jsonl from it (default: {DEFAULT_OUTPUT_DIR}, relative to the "
    "project root)"
)

# `show` is the one subcommand with no --project, so it resolves --output-dir
# against the working directory. The shared string's "relative to the project
# root" is true of every other command and false here.
_SHOW_OUTPUT_DIR_HELP = (
    "where the last run left its artifacts; `show` reads results.jsonl from "
    f"it (default: {DEFAULT_OUTPUT_DIR}, relative to the current directory -- "
    "`show` has no --project)"
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moonbuggy",
        description="Fast, agent-first mutation testing for Python.",
        epilog=_EPILOG,
        # The epilog is prose in paragraphs. The default formatter reflows it
        # into one block, which is what makes most epilogs unreadable; it is
        # hand-wrapped here instead.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"moonbuggy {__version__}"
    )
    parser.set_defaults(command="run")

    _add_run_arguments(parser)

    sub = parser.add_subparsers(dest="command")
    show = sub.add_parser("show", help="print the full record for one mutant id")
    show.add_argument("mutant_id", help="the mutant to print, as printed in `id=...`")
    show.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, help=_SHOW_OUTPUT_DIR_HELP
    )
    show.set_defaults(command="show")

    _add_run_one_parser(sub)
    _add_why_parser(sub)
    _add_operators_parser(sub)

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
    accept.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, help=_READ_OUTPUT_DIR_HELP
    )
    accept.add_argument("--accept-file", default=None, help=_ACCEPT_FILE_HELP)
    accept.add_argument(
        "-r",
        "--reason",
        default=None,
        help="why this mutant is equivalent. Required when accepting one "
        "(not for --list or --remove), and the whole point: an acceptance "
        "without one is a claim nobody can check",
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
    "--output-dir does not move it: it is a checked-in record of human "
    "decisions rather than run output, so if you gitignore "
    f"{DEFAULT_OUTPUT_DIR}/ you want to un-ignore this one file"
)


def _add_run_one_parser(
    sub: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Define `moonbuggy run <id>`: the fix-verify loop's one command.

    The internal command name is `run-one` rather than `run`, because bare
    `moonbuggy` already means "run everything" and its parsed command is
    already `run`. The subcommand a user types is `run` either way.

    Args:
        sub: the subparser action to register on.
    """
    one = sub.add_parser(
        "run",
        help="re-run one mutant by id and print the fresh verdict",
        description="Re-run one mutant, or several, using the same coverage "
        "pass, selection and runner a full run uses. The verdict is always "
        "measured rather than served from the cache -- re-measuring is the "
        "point -- and results.jsonl is left exactly as the last full run "
        "wrote it.",
    )
    one.add_argument(
        "mutant_id",
        nargs="+",
        metavar="ID",
        help="mutant ids, as printed in `id=...`. `-` reads them from stdin, "
        "one per line, so `grep -E '^(SURVIVED|NO_COVERAGE)' "
        ".moonbuggy/results.txt | moonbuggy run -` re-runs the whole finding "
        "set. Matching SURVIVED alone silently drops every NO_COVERAGE "
        "finding, which this command handles and gates on just the same",
    )
    one.add_argument("--project", default=".", help="project root (default: cwd)")
    one.add_argument(
        "--source", default=None, help="directory to mutate (default: discovered)"
    )
    one.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, help=_READ_OUTPUT_DIR_HELP
    )
    one.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="seconds before a mutant is called TIMEOUT (default: 30)",
    )
    one.add_argument(
        "-n",
        "--workers",
        type=int,
        default=0,
        help="pytest-xdist workers per mutant run (default: 0, serial)",
    )
    one.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra argument passed to every pytest run, including the "
        "coverage pass (repeatable). Pass the same ones your full run uses, "
        "or you are measuring a different suite",
    )
    one.add_argument(
        "--flaky-probe",
        type=int,
        default=1,
        metavar="N",
        help="extra unmutated suite runs used to detect flaky tests "
        "(default: 1, 0 disables)",
    )
    _add_logging_arguments(one)
    one.add_argument("--accept-file", default=None, help=_ACCEPT_FILE_HELP)
    one.add_argument(
        "--no-cache",
        action="store_true",
        help="do not record the fresh verdict in the cache. `moonbuggy run` "
        "never *reads* the cache for its targets, with or without this",
    )
    one.add_argument(
        "--report",
        choices=["human", "agent"],
        default=None,
        help="output format: 'human' for a readable block per mutant, 'agent' "
        "for the same one-line-per-mutant format results.txt uses "
        "(default: human at a terminal, agent when piped)",
    )
    one.set_defaults(command="run-one")


def _add_why_parser(
    sub: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Define `moonbuggy why <id>`: the selection and cache decisions, unrun.

    Args:
        sub: the subparser action to register on.
    """
    why = sub.add_parser(
        "why",
        help="explain which tests are selected for one mutant, and whether "
        "its verdict would come from the cache",
        description="Explain how a mutant is handled without running it: "
        "which tests coverage-guided selection picks and why, how many of "
        "them there are (the `tests_run=` on its result line), and whether "
        "the results cache already holds a verdict for those exact inputs. "
        "Answers 'is my new test being ignored, or am I being served a stale "
        "verdict?' -- which look identical from a result line. Use "
        "`moonbuggy run <id>` to re-measure instead.",
    )
    why.add_argument(
        "mutant_id",
        nargs="+",
        metavar="ID",
        help="mutant ids, as printed in `id=...`. `-` reads them from stdin, "
        "one per line, exactly as `moonbuggy run` does",
    )
    why.add_argument("--project", default=".", help="project root (default: cwd)")
    why.add_argument(
        "--source", default=None, help="directory to mutate (default: discovered)"
    )
    why.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, help=_READ_OUTPUT_DIR_HELP
    )
    why.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="the --timeout the run being explained uses. Not waited on here "
        "-- nothing is run -- but it is part of the cache key, so an "
        "explanation with the wrong one describes the wrong entry "
        "(default: 30)",
    )
    why.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra argument passed to the coverage pass (repeatable). Pass "
        "the same ones your full run uses, or selection and the cache key "
        "both describe a different suite",
    )
    why.add_argument(
        "--flaky-probe",
        type=int,
        default=0,
        metavar="N",
        help="extra unmutated suite runs used to detect flaky tests "
        "(default: 0). Off by default because `why` measures nothing and a "
        "probe is a measurement; raise it to have a flaky selection reported",
    )
    _add_logging_arguments(why)
    why.add_argument("--accept-file", default=None, help=_ACCEPT_FILE_HELP)
    why.add_argument(
        "--no-cache",
        action="store_true",
        help="skip the cache lookup and report selection only. `moonbuggy "
        "why` never writes to the cache, with or without this",
    )
    why.add_argument(
        "--json",
        action="store_true",
        help="emit one JSON object per mutant, one per line, in the same "
        "JSONL *format* results.jsonl uses. The fields are `why`'s own -- "
        "selection and cache keys -- not a result record, so there is no "
        "`status` and no `diff` to filter on",
    )
    why.set_defaults(command="why")


def _add_operators_parser(
    sub: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Define `moonbuggy operators`: what `--operators` will accept.

    `--operators` has taken a subset of names since the beginning, but nothing
    said the names existed. If the advertised onboarding path is showing an
    agent `moonbuggy -h` (#13), an operator set it cannot enumerate is one it
    will reverse-engineer by experiment or not use at all.

    Args:
        sub: the subparser action to register on.
    """
    listing = sub.add_parser(
        "operators",
        help="list the mutation operators, their tiers and their cost",
        description="Every operator this version can run. The `tier` column "
        "is what `--operators default` and `--operators deep` select; `cost` "
        "is a rough indication of what an operator adds to a run in wall "
        "clock and in survivors to read, not a measurement -- the real cost "
        "depends on the code being mutated.",
    )
    listing.add_argument(
        "--json",
        action="store_true",
        help="print the listing to stdout as a single JSON object and nothing "
        "else -- `operators` and `tiers`, so an agent can enumerate rather "
        "than parse a table",
    )
    listing.set_defaults(command="operators")


def _operators(args: argparse.Namespace) -> int:
    """Print the operator listing.

    Args:
        args: the parsed `moonbuggy operators` command line.

    Returns:
        0. Nothing here can fail: it reports what is registered.
    """
    infos = describe_operators()
    tiers = {tier: list(tier_members(tier)) for tier in TIERS}
    tiers[ALL_TIER] = list(tier_members(ALL_TIER))

    if args.json:
        # A single object, like summary.json and for the same reason: there is
        # exactly one listing per invocation. JSONL is the shape for per-mutant
        # data, of which there is a stream.
        print(
            json.dumps(
                {
                    "operators": [
                        {
                            "name": info.name,
                            "tier": info.tier,
                            "description": info.description,
                            "cost": info.cost,
                        }
                        for info in infos
                    ],
                    "tiers": tiers,
                },
                sort_keys=True,
            )
        )
        return 0

    name_width = max((len(info.name) for info in infos), default=4)
    tier_width = max(len(tier) for tier in TIERS)
    # Widths come from the vocabularies rather than from the values in hand, so
    # a column cannot silently narrow when no operator happens to claim the
    # longest word. The cost column was hardcoded at 4 while `low` and `high`
    # were the only costs anyone declared, and the first `medium` operator
    # pushed MUTATES two columns right on its own row.
    cost_width = max(len("COST"), *(len(cost) for cost in COSTS))
    header = f"{'NAME':<{name_width}}  {'TIER':<{tier_width}}  "
    print(f"{header}{'COST':<{cost_width}}  MUTATES")
    for info in infos:
        print(
            f"{info.name:<{name_width}}  {info.tier:<{tier_width}}  "
            f"{info.cost:<{cost_width}}  {info.description}"
        )
    print()
    for tier in (*TIERS, ALL_TIER):
        members = tiers[tier]
        count = len(members)
        noun = "operator" if count == 1 else "operators"
        # An empty tier is named rather than hidden. A reader who cannot see
        # that a tier is empty would read `--operators <tier>` failing as a bug
        # rather than as the truth. `deep` was that tier when tiers landed.
        print(f"  {tier}: {count} {noun}" + (" (none yet)" if not members else ""))
    print()
    # The worked example has to name an operator that is *not* in the default
    # tier, or it demonstrates a no-op and teaches that `+` means something it
    # does not. `+boundary` was the example, and boundary is listed as
    # `default` three lines above it.
    print(
        "Select with --operators: a comma-separated list of names is an exact "
        "set,\na tier name stands for its members, and a `+` prefix adds to "
        "the rest of\nthe selection -- to `default` when nothing else is "
        "named, so\n`--operators +statement_deletion` is the default set plus "
        "that one."
    )
    return 0


def _add_logging_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the logging-policy flags to one subcommand's parser.

    Shared rather than repeated because the policy has to be identical across
    `run`, `run <id>` and `why`: it decides whether a mutant is suppressed, and
    a mutant suppressed by one command and measured by another would make them
    contradict each other about unchanged source.

    Args:
        parser: the subcommand parser to add them to.
    """
    parser.add_argument(
        "--include-logging-mutants",
        action="store_true",
        help="run mutants that sit inside a logging call's arguments instead "
        "of reporting them SKIPPED. They are unkillable unless your tests "
        "assert on log output -- which is exactly when you want this",
    )
    parser.add_argument(
        "--logger-name",
        action="append",
        default=[],
        metavar="NAME",
        help="also treat this receiver name as a logger (repeatable). For a "
        "project that wraps the stdlib logger: `--logger-name audit` makes "
        "`audit.info(...)` and `self.audit.info(...)` logging calls. Added to "
        "the built-in names, never replacing them",
    )


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", default=".", help="project root (default: cwd)")
    parser.add_argument(
        "--source", default=None, help="directory to mutate (default: discovered)"
    )
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, help=_OUTPUT_DIR_HELP
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="seconds before a mutant is called TIMEOUT (default: 30)",
    )
    parser.add_argument(
        "--operators",
        default=None,
        metavar="SELECTION",
        help="which operators to run (default: the `default` tier). A comma-separated "
        "list of names is an exact set -- `comparison_swap,boundary` is those "
        "two and nothing else. A tier name stands for its members: `default` "
        "is the cheap, high-signal operators, `deep` is the expensive or "
        "noisy ones, `all` is everything. A `+` prefix adds to the rest of "
        "the selection rather than replacing it -- to the `default` tier when "
        "nothing else is named, so `+statement_deletion` is the ordinary run "
        "plus that one, while `deep,+boundary` is the deep tier plus boundary "
        "and not the default tier at all. `moonbuggy operators` lists every "
        "name, its tier and its cost",
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
        "the merge base (e.g. --since origin/main). The other end of the diff "
        "is the working tree, not HEAD, so uncommitted edits are in scope, "
        "and an untracked file is mutated in full rather than line by line. "
        "Composes with --include/--exclude rather than replacing them",
    )
    _add_logging_arguments(parser)
    parser.add_argument("--accept-file", default=None, help=_ACCEPT_FILE_HELP)
    parser.add_argument(
        "--fail-on-unexplained",
        action="store_true",
        help="exit 1 only for findings -- SURVIVED and NO_COVERAGE -- that "
        "the ledger does not account for. Without it the exit code is "
        "unchanged: a run whose every finding is accepted still exits 1, so "
        "adding a ledger never silently turns a red build green",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=0,
        help="mutants to run concurrently (default: CPU count, or one fewer "
        "with -n/--workers, which needs a core for the parent process)",
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
        "--quiet",
        action="store_true",
        help="print nothing but the summary line, which goes to stderr like "
        "the progress it replaces. stdout is left empty -- use --json for a "
        "payload to capture",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the run summary to stdout as a single JSON object and "
        "nothing else -- counts, totals, wall time and the run's effective "
        "configuration, so nothing has to be parsed out of the human line. "
        "The same object is always written to <output-dir>/summary.json, "
        "with or without this flag",
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


def _run_one(args: argparse.Namespace) -> int:
    """Re-run the named mutants and report fresh verdicts.

    Args:
        args: the parsed `moonbuggy run` command line.

    Returns:
        The process exit code, mirroring a full run's: 0 when every target was
        killed, 1 when any of them is a finding -- SURVIVED or NO_COVERAGE,
        both of which mean the mutation went unnoticed. NO_COVERAGE exits 1
        here for the same reason it does there: a CI gate that failed on
        survivors must not start passing because a finding was renamed.

    Raises:
        VerifyError: if there are no ids to run, or the project is not one
            moonbuggy can run in. Both are turned into an exit 2 with a
            message by `main`.
    """
    project_dir = Path(args.project).resolve()
    ids = _target_ids(args.mutant_id)
    if not ids and "-" in args.mutant_id:
        # Almost always one specific pipeline: `cut -d' ' -f2` over
        # results.txt, which lands on the empty string because the status
        # column is space-padded to a fixed width and cut does not fold
        # repeated delimiters. Naming the fix is worth more here than
        # repeating the usage line.
        raise VerifyError(
            "no mutant ids on stdin. If you cut a column out of results.txt, "
            "note that its status column is space-padded, so `cut -d' ' -f2` "
            "is empty -- pipe the whole lines instead: "
            "`grep -E '^(SURVIVED|NO_COVERAGE)' .moonbuggy/results.txt | "
            "moonbuggy run -`"
        )
    if not ids:
        raise VerifyError(
            "no mutant ids to run. Pass one or more ids, or `-` with ids on "
            "stdin, one per line."
        )
    if not looks_like_pytest_project(project_dir):
        raise VerifyError(
            f"{project_dir} does not look like a pytest project "
            "(no pytest.ini, pyproject.toml, conftest.py or test_*.py found). "
            "Run moonbuggy from your project root, or pass --project."
        )

    source_dir = (
        Path(args.source).resolve() if args.source else find_source_dir(project_dir)
    )
    mutants = resolve_targets(project_dir, ids, _logging_policy(args))

    # Resolved the same way a full run resolves it, so a mutant a human has
    # already reviewed says so here too. It is an annotation and never a
    # verdict: the exit code below ignores it, because `--fail-on-unexplained`
    # is what changes an exit code and this command does not have it.
    accept_path = _accept_path(args, project_dir)
    reasons = resolve_accepted(load_accepted(accept_path), mutants).reasons()

    cache = (
        None
        if args.no_cache
        else ResultCache(
            project_dir / args.output_dir / "cache.json",
            # The same fingerprint a full run builds, from the same flags. A
            # second derivation here would be a second answer to "is this
            # entry still valid", and the two would drift.
            fingerprint=run_fingerprint(
                args.pytest_arg, timeout=args.timeout, python=sys.executable
            ),
        )
    )

    verifications = verify(
        project_dir,
        mutants,
        source_dir,
        timeout=args.timeout,
        workers=args.workers,
        probes=args.flaky_probe,
        extra_args=args.pytest_arg,
        cache=cache,
        reasons=reasons,
    )
    if cache is not None:
        cache.save()

    fmt = resolve_format(args.report, os.environ, sys.stdout.isatty())
    for index, verification in enumerate(verifications):
        if fmt == "agent":
            # Byte for byte the line results.txt carries, so the output of one
            # `moonbuggy run` can be grepped and piped straight into the next.
            print(render_line(record_for(verification.result, verification.reason)))
        else:
            if index:
                print()
            _print_verification(verification)

    counts = Counter(v.status for v in verifications)
    # So the summary lands after the report rather than in the middle of it
    # when both streams are the same terminal.
    sys.stdout.flush()
    print(
        "moonbuggy: "
        + "  ".join(f"{status}={count}" for status, count in sorted(counts.items()))
        + f"  re-measured={len(verifications)}"
        + f" ({_display_path(project_dir / args.output_dir, project_dir)}"
        + "/results.jsonl is unchanged)",
        file=sys.stderr,
    )
    return 1 if any(v.status in FINDING_STATUSES for v in verifications) else 0


def _print_verification(verification: Verification) -> None:
    """Print one mutant's fresh verdict, in `moonbuggy show`'s shape.

    Deliberately the same aligned key/value block, plus the two things only a
    real run can say: which tests were selected, and which of them failed.

    Args:
        verification: the mutant's re-measured outcome.
    """
    mutant = verification.mutant
    print(f"id           {mutant.id}")
    print(f"status       {verification.status}")
    print(f"location     {mutant.module}:{mutant.line}")
    print(f"operator     {mutant.operator}")
    print(f"tests_run    {verification.result.tests_run}")
    _print_test_list("selected", verification.selected)
    _print_test_list("failed", verification.failed)
    if verification.reason is not None:
        print(f"accepted     {verification.reason}")
    print("diff")
    print(f"  - {mutant.original}")
    print(f"  + {mutant.mutated}")


def _print_test_list(label: str, node_ids: Sequence[str]) -> None:
    # One test per line under a single label, indented to the value column.
    # Node ids are long and there can be dozens; joining them would produce a
    # line nobody can read and no terminal can wrap usefully.
    if not node_ids:
        print(f"{label:<12} -")
        return
    print(f"{label:<12} {node_ids[0]}")
    for node_id in node_ids[1:]:
        print(f"{'':<12} {node_id}")


def _why(args: argparse.Namespace) -> int:
    """Explain the selection and cache decisions for the named mutants.

    Args:
        args: the parsed `moonbuggy why` command line.

    Returns:
        0. An explanation has no verdict to gate on, so unlike `run` this never
        exits 1 for a finding -- a `why` in a CI script must not fail the build
        for successfully explaining something.

    Raises:
        VerifyError: if there are no ids to explain, or the project is not one
            moonbuggy can run in. Both become an exit 2 with a message.
    """
    project_dir = Path(args.project).resolve()
    ids = _target_ids(args.mutant_id)
    if not ids:
        raise VerifyError(
            "no mutant ids to explain. Pass one or more ids, or `-` with ids "
            "on stdin, one per line."
        )
    if not looks_like_pytest_project(project_dir):
        raise VerifyError(
            f"{project_dir} does not look like a pytest project "
            "(no pytest.ini, pyproject.toml, conftest.py or test_*.py found). "
            "Run moonbuggy from your project root, or pass --project."
        )

    source_dir = (
        Path(args.source).resolve() if args.source else find_source_dir(project_dir)
    )
    mutants = resolve_targets(project_dir, ids, _logging_policy(args))
    reasons = resolve_accepted(
        load_accepted(_accept_path(args, project_dir)), mutants
    ).reasons()

    output_dir = project_dir / args.output_dir
    # Derived exactly as `run` and a full run derive it, from the same flags.
    # An explanation of a key computed a second way would be an explanation of
    # a key nothing else uses.
    inputs: dict[str, object] = {
        "pytest_args": list(args.pytest_arg),
        "timeout": args.timeout,
        "python": sys.executable,
    }
    cache = (
        None
        if args.no_cache
        else ResultCache(
            output_dir / "cache.json",
            fingerprint=run_fingerprint(
                args.pytest_arg, timeout=args.timeout, python=sys.executable
            ),
        )
    )

    results_path = output_dir / "results.jsonl"
    records = (
        {record["id"]: record for record in read_jsonl(results_path)}
        if results_path.exists()
        else {}
    )

    explanations = explain(
        project_dir,
        mutants,
        source_dir,
        probes=args.flaky_probe,
        extra_args=args.pytest_arg,
        cache=cache,
        fingerprint_inputs=inputs,
        reasons=reasons,
        records=records,
    )

    for index, explanation in enumerate(explanations):
        if args.json:
            # JSONL rather than one array, so a single id yields one object
            # `jq` reads directly and many ids stream the way results.jsonl
            # does. One shape for both is worth more than an array's tidiness.
            print(json.dumps(explanation.summary(), sort_keys=True))
        else:
            if index:
                print()
            _print_explanation(
                explanation, _display_path(results_path, project_dir), bool(records)
            )
    return 0


def _print_explanation(
    explanation: Explanation, results_path: str, has_records: bool
) -> None:
    """Print one mutant's selection and cache decisions, in `show`'s shape.

    Args:
        explanation: the mutant's explanation.
        results_path: where the last run's records were looked for, for the
            `last_run` line to name.
        has_records: whether that file existed at all. Distinguishes "no run
            has happened here" from "the last run did not report this mutant",
            which are different things to go and fix.
    """
    mutant = explanation.mutant
    print(f"id           {mutant.id}")
    print(f"location     {mutant.module}:{mutant.line}")
    print(f"operator     {mutant.operator}")
    print("diff")
    print(f"  - {mutant.original}")
    print(f"  + {mutant.mutated}")
    print(f"selection    {_selection_line(explanation)}")
    print(f"tests_run    {len(explanation.selected)}")
    _print_test_list("selected", explanation.selected)
    if explanation.flaky:
        _print_test_list("flaky", explanation.flaky)
    print(f"cache        {_cache_line(explanation)}")
    if explanation.cache_key is not None:
        print(f"cache_key    {explanation.cache_key}")
        _print_test_list("cache_covers", explanation.cache_covers)
    print(f"run_inputs   {_run_inputs_line(explanation.fingerprint_inputs or {})}")
    print(f"last_run     {_last_run_line(explanation, results_path, has_records)}")
    if explanation.reason is not None:
        print(f"accepted     {explanation.reason}")
    for note in _notes(explanation):
        _print_note(note)


def _selection_line(explanation: Explanation) -> str:
    """One sentence naming the selected set and where it came from."""
    mutant = explanation.mutant
    where = f"{mutant.module}:{mutant.line}"
    count = len(explanation.selected)
    if explanation.selection == "suppressed":
        if mutant.logging_call:
            # Named specifically, because this suppression is the tool's own
            # policy rather than something the author wrote into the file, and
            # a reader looking for a `# moonbuggy: skip` that is not there
            # would conclude the report was wrong.
            return (
                "the mutation is inside a logging call's arguments, so no "
                "test is selected and a run reports SKIPPED without measuring "
                "anything -- pass --include-logging-mutants to run it"
            )
        return (
            "the line carries a suppression marker, so no test is selected "
            "and a run reports SKIPPED without measuring anything"
        )
    if explanation.selection == "module_level":
        return (
            f"{where} runs at import time, so the coverage pass attributes it "
            f"to no single test and the whole suite is selected ({count})"
        )
    if count == 0:
        return f"the coverage pass saw no test execute {where}"
    return (
        f"the coverage pass saw {count} "
        f"{'test' if count == 1 else 'tests'} execute {where}"
    )


def _cache_line(explanation: Explanation) -> str:
    """Whether a run would be served this mutant's verdict from the cache."""
    if explanation.next_run == "skipped":
        return "not consulted -- a suppressed mutant is settled before the lookup"
    if explanation.next_run == "suspicious":
        return (
            "not consulted -- a flaky test in the selection settles this as "
            "SUSPICIOUS before the lookup"
        )
    if explanation.cache_key is None:
        return "not consulted (--no-cache)"
    if explanation.cached is None:
        return (
            "miss -- nothing is stored under this key, so the next run "
            "measures this mutant for real"
        )
    return (
        f"hit -- the next run replays {explanation.cached['status']} "
        f"(tests_run={explanation.cached['tests_run']}) without measuring it"
    )


def _run_inputs_line(inputs: dict[str, object]) -> str:
    """The run inputs folded into the cache key, as one readable line."""
    raw = inputs.get("pytest_args")
    pytest_args = raw if isinstance(raw, list) else []
    rendered = " ".join(str(a) for a in pytest_args) if pytest_args else "(none)"
    return (
        f"pytest args: {rendered}   timeout: {inputs.get('timeout')}   "
        f"python: {inputs.get('python')}"
    )


def _last_run_line(
    explanation: Explanation, results_path: str, has_records: bool
) -> str:
    """What the last full run recorded for this mutant, if anything."""
    if not has_records:
        return f"- (no {results_path}; no run has happened here yet)"
    if explanation.last_run is None:
        return (
            f"- (not in {results_path}; the last run did not report this "
            "mutant, so its line may have moved since)"
        )
    return (
        f"{explanation.last_run['status']}  "
        f"tests_run={explanation.last_run['tests_run']}  ({results_path})"
    )


def _notes(explanation: Explanation) -> list[str]:
    """The one or two things the field lines above do not say outright."""
    notes = []
    if explanation.selection == "coverage" and not explanation.selected:
        notes.append(
            "no test reaches this line, so a run reports NO_COVERAGE rather than "
            "SURVIVED -- nothing could have caught the mutation. Write a test that "
            "executes the line, or delete the code."
        )
    if explanation.next_run == "cache":
        notes.append(
            "the key covers the selected set above and the contents of those files, "
            "so a test that is new, edited, or newly reaches this line changes the "
            "key and turns this hit into a miss. A stale verdict cannot outlive any "
            "of those."
        )
    return notes


def _print_note(text: str) -> None:
    """Print one note, wrapped, under a single `note` label."""
    lines = textwrap.wrap(text, width=76) or [""]
    print(f"{'note':<12} {lines[0]}")
    for line in lines[1:]:
        print(f"{'':<12} {line}")


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
