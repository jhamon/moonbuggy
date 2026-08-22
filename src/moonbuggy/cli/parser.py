"""argparse builders: the CLI grammar."""

import argparse

from .. import __version__
from ..accepted import (
    DEFAULT_ACCEPT_FILE,
)
from .constants import DEFAULT_OUTPUT_DIR


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


_SHOW_OUTPUT_DIR_HELP = (
    "where the last run left its artifacts; `show` reads results.jsonl from "
    f"it (default: {DEFAULT_OUTPUT_DIR}, relative to the current directory -- "
    "`show` has no --project)"
)


_ACCEPT_FILE_HELP = (
    "the accepted-equivalents ledger "
    f"(default: {DEFAULT_ACCEPT_FILE}, relative to the project root). "
    "--output-dir does not move it: it is a checked-in record of human "
    "decisions rather than run output, so if you gitignore "
    f"{DEFAULT_OUTPUT_DIR}/ you want to un-ignore this one file"
)
