"""The accept subcommand and ledger helpers."""

import argparse
import sys
from pathlib import Path

from ..accepted import (
    Acceptance,
    Entry,
    entry_for,
    is_git_ignored,
)
from ..accepted import load as load_accepted
from ..accepted import save as save_accepted
from ..report import (
    FINDING_STATUSES,
    find_record,
    read_jsonl,
)
from .common import _accept_path, _display_path


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
