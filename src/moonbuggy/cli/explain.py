"""run-one / why / show subcommands and their render helpers."""

import argparse
import json
import os
import sys
import textwrap
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from ..accepted import load as load_accepted
from ..accepted import resolve as resolve_accepted
from ..cache import ResultCache, run_fingerprint
from ..discover import (
    find_source_dir,
    looks_like_pytest_project,
)
from ..report import (
    FINDING_STATUSES,
    find_record,
    read_jsonl,
    record_for,
    render_line,
)
from ..terminal import (
    resolve_format,
)
from ..verify import (
    Explanation,
    Verification,
    VerifyError,
    explain,
    resolve_targets,
    verify,
)
from .common import _accept_path, _display_path, _logging_policy, _target_ids


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
