"""Reporting: canonical JSONL, plus a plaintext view derived from it.

Section 5 assumes the reader is an agent grepping output rather than a human
reading a dashboard, which makes the format the feature:

- A fixed leading status keyword, so `grep SURVIVED` works with zero knowledge
  of the schema. The keyword is padded to a fixed-width column for the eye's
  benefit; a keyword longer than the column overflows it rather than widening
  it, so a line's tokens keep their positions whatever the status is.
- key=value tokens rather than prose, so naive whitespace splitting parses it.
- Exactly one line per mutant, so grep and awk stay usable. The diff is
  deliberately NOT inlined -- `moonbuggy show <id>` retrieves it. This settles
  the first open question in 5.3 in favour of the doc's stated lean.

The plaintext is *derived from* the JSONL rather than authored alongside it, so
the two cannot drift apart. That is criterion E3, and it is why render_line
takes a record dict rather than a Result.
"""

import json
import os
from collections.abc import Iterable, Mapping
from types import TracebackType
from typing import IO, Literal, TypedDict

from .runner import Result

# The whole status vocabulary. Every plaintext line begins with one of these,
# so adding a keyword is a breaking change for anyone grepping: NO_COVERAGE
# arrived in 0.1.3 and took the uncovered lines that used to be SURVIVED with
# it. `summarise` seeds its counts from here, so a new keyword also appears in
# the run's final summary line with a count of zero.
STATUS_KEYWORDS = {
    "KILLED",
    "SURVIVED",
    "NO_COVERAGE",
    "TIMEOUT",
    "SUSPICIOUS",
    "SKIPPED",
}

# The statuses that are findings about the tests rather than facts about the
# run: something was changed, and nothing noticed. These are the statuses that
# count toward exit 1, and the ones the accepted-equivalents ledger can speak
# for. TIMEOUT and SUSPICIOUS are deliberately absent -- neither is a claim
# that the mutation went unnoticed, so neither is something to accept.
FINDING_STATUSES = frozenset({"SURVIVED", "NO_COVERAGE"})

# Printed when a field has no value. A literal placeholder rather than an empty
# string keeps the token count per line constant, so whitespace-splitting
# parsers do not have to special-case missing fields.
ABSENT = "-"


class Record(TypedDict):
    """The canonical JSONL record for one mutant -- `record_for`'s return shape."""

    id: str
    status: str
    file: str
    line: int
    operator: str
    category: str
    nearest_test: str | None
    tests_run: int
    duration: float
    module_level: bool
    suppressed: bool
    original: str
    mutated: str
    diff: str
    accepted: bool
    accept_reason: str | None


def record_for(result: Result, reason: str | None = None) -> Record:
    """The canonical record for one mutant. This is the JSONL line's content.

    Args:
        result: the mutant's verdict.
        reason: the accepted-equivalents ledger's reason for this mutant, if a
            live acceptance covers it. Stamped onto the record rather than kept
            beside it, because a reader filtering `results.jsonl` for real
            findings needs the acceptance in the same object as the status --
            `jq 'select(.status=="SURVIVED" and .accepted|not)'` is the
            question, and it cannot be asked across two files.

    Returns:
        The record.
    """
    mutant = result.mutant
    return {
        "id": mutant.id,
        "status": result.status,
        "file": mutant.module,
        "line": mutant.line,
        "operator": mutant.operator,
        # Category is the operator name rather than a second taxonomy. Section
        # 5.3 defers designing a separate reason taxonomy until real
        # survived-mutant data exists to design it against, and inventing one
        # here would be exactly the speculative choice that defers.
        "category": mutant.operator,
        "nearest_test": result.nearest_test,
        "tests_run": result.tests_run,
        "duration": round(result.duration, 4),
        "module_level": mutant.module_level,
        "suppressed": mutant.suppressed,
        # The operands, not just the rendered diff. The human reporter computes
        # a changed span from these; deriving them by splitting `diff` would be
        # a reporter parsing its own output format.
        "original": mutant.original,
        "mutated": mutant.mutated,
        "diff": f"- {mutant.original}\n+ {mutant.mutated}",
        # An accepted mutant is annotated, never hidden: it ran, it has a
        # verdict, and this says a human has already explained it.
        "accepted": reason is not None,
        "accept_reason": reason,
    }


def write_jsonl(
    results: Iterable[Result],
    path: str | os.PathLike[str],
    reasons: Mapping[str, str] | None = None,
) -> None:
    """Stream records to disk, one complete line at a time.

    Flushed per record so a run killed partway leaves only whole, parseable
    lines behind (criterion E2). A half-written final record would break every
    downstream reader, which matters more here than the cost of the flush.

    Args:
        results: the verdicts to write, in the order to write them.
        path: the file to write.
        reasons: accepted-equivalents reasons by mutant id, as
            `accepted.Resolution.reasons` returns.
    """
    reasons = reasons or {}
    with open(path, "w", encoding="utf-8") as handle:
        for result in results:
            record = record_for(result, reasons.get(result.mutant.id))
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()


class StreamingJSONL:
    """Write records as they are produced, keeping the file valid throughout.

    Criterion M1.4.13: a run killed mid-flight has to leave something a later
    reader can parse, not a truncated final line. Every record is written and
    flushed whole, so the file is a valid JSONL document at every instant
    between writes -- it is only ever *incomplete*, which readers can see for
    themselves by counting lines.

    Records arrive in completion order rather than mutant order. That is fine
    for a partial file, and the caller rewrites the whole thing in canonical
    order once the run finishes.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        reasons: Mapping[str, str] | None = None,
    ) -> None:
        self.path = path
        self.reasons = reasons or {}
        self._handle: IO[str] | None = None
        self.written = 0

    def __enter__(self) -> "StreamingJSONL":
        self._handle = open(self.path, "w", encoding="utf-8")
        return self

    def write(self, result: Result) -> None:
        """Append one result. Safe to call from a runner callback."""
        # Only ever None before __enter__ or after __exit__; calling write()
        # outside that window is a caller bug that already crashed here (with
        # AttributeError) before this annotation.
        assert self._handle is not None, "write() called outside the context manager"
        record = record_for(result, self.reasons.get(result.mutant.id))
        self._handle.write(json.dumps(record, sort_keys=True) + "\n")
        self._handle.flush()
        self.written += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        assert self._handle is not None
        self._handle.close()
        self._handle = None
        return False


def read_jsonl(path: str | os.PathLike[str]) -> list[Record]:
    """Read every record back from a JSONL file, in file order."""
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def render_line(record: Record) -> str:
    """One plaintext line for one record. Never contains a newline."""
    return " ".join(
        [
            f"{record['status']:<9}",
            f"{record['file']}:{record['line']}",
            record["category"],
            f"line={record['line']}",
            f"nearest_test={record['nearest_test'] or ABSENT}",
            f"tests_run={record['tests_run']}",
            f"id={record['id']}",
        ]
    )


def plaintext_from_records(records: Iterable[Record]) -> str:
    """The whole plaintext view: one line per record, no trailing newline."""
    return "\n".join(render_line(record) for record in records)


def summarise(records: Iterable[Record]) -> dict[str, int]:
    """Counts per status, for the run's final line."""
    counts = {keyword: 0 for keyword in sorted(STATUS_KEYWORDS)}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    return counts


def find_record(records: Iterable[Record], mutant_id: str) -> Record | None:
    """The record with this mutant id, or None if there is not one."""
    for record in records:
        if record["id"] == mutant_id:
            return record
    return None
