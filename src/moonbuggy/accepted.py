"""The accepted-equivalents ledger: a triage decision that outlives the run.

A triage session's real end state is "three survivors, reviewed, verified
equivalent". Without somewhere to put that, the next run reports the same three
forever, every reviewer pays the same cost again, and a CI gate can never be
green. This module is that somewhere: a small TOML file, checked into version
control next to the code it makes claims about, holding one entry per mutant a
human has decided is equivalent -- and the reason they decided it.

Three decisions shape everything below.

**Accepted mutants still run, and are still reported.** Acceptance is an
annotation, never a filter. A mutant dropped from generation would take its
verdict with it, and the day the code changes underneath the acceptance there
would be nothing left to notice. So the run is identical either way; only the
counting and the presentation differ, and `moonbuggy` says how many
acceptances it honoured every time it honours one.

**Drift expires an acceptance.** An entry stores a `fingerprint` of the
mutation it was made for -- the operator, the original line and the mutated
line. If the source under it changes, the fingerprint no longer matches, the
entry is *stale*, and the mutant is reported as unexplained. Silently
honouring a stale acceptance is precisely how a real regression sneaks in
behind a decision somebody made about different code last year.

The fingerprint deliberately covers the mutated **line**, not the whole
module. A module-wide hash is the stricter reading of drift, and it was
rejected: it expires every acceptance in a file on any edit to that file,
including a comment, so a working ledger would be a ledger of nothing within a
week -- and re-accepting fifty entries in bulk is not a review, it is a
ritual. The cost of the narrower hash is stated rather than hidden: a change
*elsewhere* in the module can invalidate an equivalence argument without
expiring the entry. The reason field is what a reviewer re-reads when that
matters, which is why writing one is mandatory.

**Acceptance keys on the id first and on content second.** Mutant ids are
`path:line:operator:index`, so inserting a line above shifts every id below it,
and a ledger keyed on the id alone would silently lose its entries to an
unrelated edit -- worse than no ledger, because the loss is invisible. A ledger
keyed on content alone cannot distinguish two identical lines in one file, and
would honour a decision about one of them for the other. So the match is:

1. the id resolves and the fingerprint agrees -> honoured;
2. otherwise, exactly one *unclaimed* mutant in the same file carries the same
   fingerprint -> honoured, and reported as relocated (the id shifted);
3. otherwise, if the id resolves but the fingerprint disagrees -> **stale**;
4. otherwise -> orphaned: no such mutant in this run, which is the normal state
   of most entries under `--since` and is not a finding.

Ambiguity in step 2 -- two identical candidates, no exact id -- is refused
rather than guessed. Equivalence is a judgement about a line in its context,
and picking one of two candidates at random honours a decision nobody made
about it.

The file is written by hand rather than through a TOML library: the schema is
six flat string keys, `tomllib` reads it back on every supported Python, and
adding a runtime dependency to emit six keys would be the more expensive
mistake. `tomllib` is the authority on what a valid file is, so anything this
module writes is read back by the same parser everyone else uses.
"""

import hashlib
import os
import subprocess
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .mutant import Mutant
from .report import FINDING_STATUSES, Record

# Relative to the project root, and deliberately NOT under `--output-dir`.
# Everything in the output directory is run output that a run may overwrite;
# this file is human input to a run, so pointing `--output-dir` at /tmp must
# not move it. `--accept-file` is how you move it on purpose.
DEFAULT_ACCEPT_FILE = ".moonbuggy/accepted.toml"

# Bumped if the meaning of a stored field changes. An older moonbuggy reading a
# newer ledger refuses rather than misreading it, because a misread entry is an
# acceptance honoured for the wrong reason.
LEDGER_VERSION = 1

_HEADER = """\
# moonbuggy accepted-equivalents ledger.
#
# Written by `moonbuggy accept`, and meant to be committed: it records human
# decisions about this code, not output from a run. Each entry is honoured only
# while its `fingerprint` still matches the mutation it was made for -- edit the
# line and the acceptance goes stale, and the mutant is reported again.
"""


class AcceptError(RuntimeError):
    """Raised when the ledger cannot be read, written, or trusted.

    Always actionable and never a traceback: `cli.main` turns this into exit
    code 2 with the message alone, like every other anticipated failure.
    """


@dataclass(frozen=True)
class Entry:
    """One accepted mutant, as stored in the ledger."""

    id: str
    """The mutant id at the moment of acceptance. A hint, not the identity --
    see the module docstring: the fingerprint outranks it when they disagree."""

    file: str
    """The module the mutant lives in, relative to the project root. Bounds a
    content match to one file, so two identical lines in different modules
    cannot borrow each other's decision."""

    operator: str
    """The operator that produced the mutation."""

    fingerprint: str
    """A digest of `(operator, original, mutated)`. The drift detector."""

    reason: str
    """Why a human decided this mutant is equivalent. Mandatory: an acceptance
    without one is a claim nobody can check and nobody will revisit."""

    accepted_at: str
    """The ISO date the entry was written, so a reviewer can see its age."""


@dataclass(frozen=True)
class Resolution:
    """How each ledger entry lines up with the mutants this run generated."""

    live: dict[str, Entry] = field(default_factory=dict)
    """Honoured acceptances, keyed by the mutant id **in this run** -- which is
    not the entry's stored id when the entry was relocated."""

    relocated: dict[str, str] = field(default_factory=dict)
    """Stored id -> current id, for entries whose line number moved."""

    stale: tuple[Entry, ...] = ()
    """Entries whose mutation has changed under them. Reported, not honoured."""

    ambiguous: tuple[Entry, ...] = ()
    """Entries with more than one equally good candidate. Not honoured."""

    orphaned: tuple[Entry, ...] = ()
    """Entries matching no mutant in this run -- normal under `--since`."""

    def reasons(self) -> dict[str, str]:
        """The honoured reasons, keyed by this run's mutant ids.

        Returns:
            A mapping suitable for `report.record_for`, which stamps the reason
            onto the mutant's record so `results.jsonl` says why a survivor was
            not counted against the run.
        """
        return {mutant_id: entry.reason for mutant_id, entry in self.live.items()}


@dataclass(frozen=True)
class Acceptance:
    """A run's ledger outcome: what was accepted, and what was not.

    Structured rather than formatted, because the same numbers are wanted in
    three places -- the human footer, the exit code, and the `--json` run
    summary -- and a string is only usable by the first of them.
    """

    path: str
    """The ledger's display path, for a message that names the file to edit."""

    accepted: tuple[str, ...]
    """Ids of findings covered by a live acceptance, in report order."""

    unexplained: tuple[str, ...]
    """Ids of findings that are not. This is what `--fail-on-unexplained`
    gates on."""

    stale: tuple[Entry, ...]
    """Acceptances that expired because their line changed."""

    ambiguous: tuple[Entry, ...]
    """Acceptances with no unambiguous candidate."""

    orphaned: tuple[Entry, ...]
    """Acceptances for mutants this run did not generate."""

    relocated: dict[str, str]
    """Acceptances honoured under a shifted id."""

    gating: bool
    """Whether `--fail-on-unexplained` decides this run's exit code."""

    def summary(self) -> dict[str, object]:
        """The counts as JSON-serialisable data.

        Returns:
            A mapping with `accepted`, `unexplained`, `stale`, `ambiguous`,
            `orphaned` and `relocated` counts, the `ledger` path, and whether
            the run was gated on unexplained findings.
        """
        return {
            "accepted": len(self.accepted),
            "unexplained": len(self.unexplained),
            "stale": len(self.stale),
            "ambiguous": len(self.ambiguous),
            "orphaned": len(self.orphaned),
            "relocated": len(self.relocated),
            "ledger": self.path,
            "fail_on_unexplained": self.gating,
        }


def fingerprint(operator: str, original: str, mutated: str) -> str:
    """A digest of one mutation's content, independent of where it sits.

    Args:
        operator: the operator's name.
        original: the source line before mutation, as `generate` stores it.
        mutated: the same line after mutation.

    Returns:
        A short hex digest. Short because this is a drift detector a human
        reads in a diff, not a security boundary -- and because a mutation
        whose 64-bit digest collides with another's, in the same file, is not
        a failure mode worth trading readability for.
    """
    payload = "\0".join([operator, original, mutated]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def entry_for(
    mutant_id: str,
    module: str,
    operator: str,
    original: str,
    mutated: str,
    *,
    reason: str,
    at: str | None = None,
) -> Entry:
    """Build a ledger entry for one mutation.

    Takes the mutation's parts rather than a `Mutant` or a `Record`, because
    both callers have one of those and neither has the other: `moonbuggy
    accept` reads a record out of `results.jsonl`, and the run resolves against
    freshly generated mutants.

    Args:
        mutant_id: the mutant's id at the moment of acceptance.
        module: the file it lives in, relative to the project root.
        operator: the operator that produced it.
        original: the source line before mutation.
        mutated: the same line after mutation.
        reason: why it is equivalent. Mandatory.
        at: the ISO date to stamp, or None for today.

    Returns:
        The :class:`Entry` to store.
    """
    return Entry(
        id=mutant_id,
        file=module,
        operator=operator,
        fingerprint=fingerprint(operator, original, mutated),
        reason=reason,
        accepted_at=at or date.today().isoformat(),
    )


def load(path: str | os.PathLike[str]) -> tuple[Entry, ...]:
    """Read the ledger, in file order.

    Args:
        path: the ledger file. A missing file is an empty ledger, not an error
            -- every project starts without one.

    Returns:
        The entries, in the order they appear in the file.

    Raises:
        AcceptError: if the file is not valid TOML, is missing a required
            field, was written by a newer moonbuggy, or holds two entries for
            one mutant id.
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise AcceptError(f"cannot read the accept file {path}: {error}") from error

    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise AcceptError(
            f"{path} is not a readable accept file: {error}. "
            "It is a checked-in file, so this is usually an unresolved merge "
            "conflict -- open it and keep both sides' entries."
        ) from error

    version = document.get("version", LEDGER_VERSION)
    if not isinstance(version, int) or version > LEDGER_VERSION:
        raise AcceptError(
            f"{path} is version {version}, and this moonbuggy understands "
            f"version {LEDGER_VERSION}. Upgrade moonbuggy rather than editing "
            "the file: an entry read under the wrong schema is an acceptance "
            "honoured for the wrong reason."
        )

    entries = []
    seen: set[str] = set()
    for index, item in enumerate(document.get("accepted", [])):
        entry = _entry_from(item, path, index)
        if entry.id in seen:
            raise AcceptError(
                f"{path} holds two entries for {entry.id}. Two reasons for one "
                "mutant means nobody can say which decision is in force. "
                "A bad merge is the usual cause -- keep one of them."
            )
        seen.add(entry.id)
        entries.append(entry)
    return tuple(entries)


def _entry_from(item: object, path: Path, index: int) -> Entry:
    # An entry missing a field is not "partly usable": without a fingerprint
    # there is no drift check, and without a reason there is no decision to
    # honour. Both would be honoured silently by a lenient reader.
    if not isinstance(item, dict):
        raise AcceptError(f"{path}: entry {index + 1} is not a table.")
    missing = [
        key
        for key in ("id", "file", "operator", "fingerprint", "reason", "accepted_at")
        if not isinstance(item.get(key), str)
    ]
    if missing:
        raise AcceptError(
            f"{path}: entry {index + 1} is missing {', '.join(missing)}. "
            "Every entry needs all six fields; rewrite it with "
            "`moonbuggy accept <id> --reason ...`."
        )
    return Entry(
        id=item["id"],
        file=item["file"],
        operator=item["operator"],
        fingerprint=item["fingerprint"],
        reason=item["reason"],
        accepted_at=item["accepted_at"],
    )


def save(path: str | os.PathLike[str], entries: Iterable[Entry]) -> None:
    """Write the whole ledger, replacing whatever was there.

    Entries are written in id order rather than in the order they were
    accepted, so two people accepting different mutants on two branches produce
    a diff a merge can resolve line by line instead of a reordered file.

    Args:
        path: the ledger file. Its parent directory is created if needed.
        entries: the entries to store.

    Raises:
        AcceptError: if the file cannot be written.
    """
    path = Path(path)
    lines = [_HEADER, f"version = {LEDGER_VERSION}\n"]
    for entry in sorted(entries, key=lambda e: e.id):
        lines.append("\n[[accepted]]\n")
        for key, value in (
            ("id", entry.id),
            ("file", entry.file),
            ("operator", entry.operator),
            ("fingerprint", entry.fingerprint),
            ("reason", entry.reason),
            ("accepted_at", entry.accepted_at),
        ):
            lines.append(f"{key} = {_toml_string(value)}\n")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(lines), encoding="utf-8")
    except OSError as error:
        raise AcceptError(f"cannot write the accept file {path}: {error}") from error


_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _toml_string(value: str) -> str:
    # A TOML basic string. Reasons are free text a human typed, so quotes,
    # backslashes and newlines all reach here; the remaining control characters
    # are escaped as \uXXXX because TOML forbids them raw.
    out = []
    for char in value:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif char < " " or char == "\x7f":
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def resolve(entries: Sequence[Entry], mutants: Sequence[Mutant]) -> Resolution:
    """Match ledger entries against the mutants this run generated.

    The matching rules, and why they are these rules, are in the module
    docstring -- they are the ledger's answer to drift and to id stability, and
    they belong next to the format they justify.

    Args:
        entries: the ledger, as :func:`load` returns it.
        mutants: every mutant this run will execute.

    Returns:
        The :class:`Resolution` for this run.
    """
    by_id = {mutant.id: mutant for mutant in mutants}
    by_content: dict[tuple[str, str], list[Mutant]] = {}
    for mutant in mutants:
        key = (
            mutant.module,
            fingerprint(mutant.operator, mutant.original, mutant.mutated),
        )
        by_content.setdefault(key, []).append(mutant)

    live: dict[str, Entry] = {}
    relocated: dict[str, str] = {}
    claimed: set[str] = set()
    pending: list[Entry] = []

    # Exact matches first, across the whole ledger, so a relocation can never
    # claim a mutant that some other entry names outright.
    for entry in entries:
        named = by_id.get(entry.id)
        if named is not None and _matches(named, entry):
            live[named.id] = entry
            claimed.add(named.id)
        else:
            pending.append(entry)

    stale: list[Entry] = []
    ambiguous: list[Entry] = []
    orphaned: list[Entry] = []
    for entry in pending:
        candidates = [
            mutant
            for mutant in by_content.get((entry.file, entry.fingerprint), [])
            if mutant.id not in claimed
        ]
        if len(candidates) == 1:
            live[candidates[0].id] = entry
            relocated[entry.id] = candidates[0].id
            claimed.add(candidates[0].id)
        elif len(candidates) > 1:
            ambiguous.append(entry)
        elif entry.id in by_id:
            # The id still names a mutant, but not this mutation: the line was
            # edited under the acceptance.
            stale.append(entry)
        else:
            orphaned.append(entry)

    return Resolution(
        live=live,
        relocated=relocated,
        stale=tuple(stale),
        ambiguous=tuple(ambiguous),
        orphaned=tuple(orphaned),
    )


def _matches(mutant: Mutant, entry: Entry) -> bool:
    return entry.fingerprint == fingerprint(
        mutant.operator, mutant.original, mutant.mutated
    )


def tally(
    records: Iterable[Record | Mapping[str, object]],
    resolution: Resolution,
    *,
    path: str,
    gating: bool,
) -> Acceptance:
    """Split this run's findings into accepted and unexplained.

    Only findings can be either: a KILLED mutant with an acceptance on file is
    simply killed, and counting it as accepted would report a decision that no
    longer does any work as if it were live.

    Args:
        records: this run's records, in report order.
        resolution: the ledger matched against this run's mutants.
        path: the ledger's display path, carried into the result so a message
            can name the file to edit.
        gating: whether `--fail-on-unexplained` decides the exit code.

    Returns:
        The run's :class:`Acceptance`.
    """
    accepted: list[str] = []
    unexplained: list[str] = []
    for record in records:
        if record["status"] not in FINDING_STATUSES:
            continue
        mutant_id = str(record["id"])
        if mutant_id in resolution.live:
            accepted.append(mutant_id)
        else:
            unexplained.append(mutant_id)
    return Acceptance(
        path=path,
        accepted=tuple(accepted),
        unexplained=tuple(unexplained),
        stale=resolution.stale,
        ambiguous=resolution.ambiguous,
        orphaned=resolution.orphaned,
        relocated=dict(resolution.relocated),
        gating=gating,
    )


def is_git_ignored(path: str | os.PathLike[str]) -> bool:
    """Whether git would ignore this path.

    Asked once, when the ledger is first written, because `.moonbuggy/` is a
    directory most projects ignore -- moonbuggy's own repository did -- and a
    ledger nobody can commit is a ledger that vanishes on the next clone. A
    warning at the moment of writing is the only time the answer is actionable.

    Args:
        path: the ledger file.

    Returns:
        True if git reports the path as ignored. False for anything else,
        including "not a git repository" and "git is not installed": neither is
        a reason to warn.
    """
    path = Path(path)
    try:
        completed = subprocess.run(
            ["git", "check-ignore", "--quiet", str(path)],
            cwd=path.parent if path.parent.exists() else Path.cwd(),
            capture_output=True,
            check=False,
        )
    except (OSError, ValueError):
        return False
    return completed.returncode == 0
