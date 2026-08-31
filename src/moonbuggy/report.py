"""Reporting: canonical JSONL, plus a plaintext view derived from it.

The output is designed for a reader -- human or agent -- grepping the report
rather than viewing a dashboard, which makes the format the feature:

- A fixed leading status keyword, so `grep SURVIVED` works with zero knowledge
  of the schema. The keyword is padded to a fixed-width column for the eye's
  benefit; the padding is cosmetic, and a keyword longer than the column
  overflows it rather than widening it -- `NO_COVERAGE` and `KILLED_BY_ERROR`
  both do. What a line guarantees is therefore the *order* of its fields under
  whitespace splitting (field 0 is the status, field 1 is `file:line`, and so
  on), never a column offset. Split, do not slice.
- key=value tokens rather than prose, so naive whitespace splitting parses it.
- Exactly one line per mutant, so grep and awk stay usable. The diff is
  deliberately NOT inlined -- `moonbuggy show <id>` retrieves it.

The plaintext is *derived from* the JSONL rather than authored alongside it, so
the two cannot drift apart by construction -- which is why render_line
takes a record dict rather than a Result.
"""

import json
import os
from collections.abc import Iterable, Mapping
from types import TracebackType
from typing import IO, Literal, TypedDict

from . import __version__
from .runner import Result

# The version stamped on every JSONL record, and the one `read_jsonl` upgrades
# older records to. A line-oriented file has no header to put this in, so each
# line carries it: a reader that has one line has everything it needs to know
# what that line means, which is the property JSONL exists for.
#
# 1: the original record, before the accepted-equivalents ledger.
# 2: `accepted` and `accept_reason`, and this field.
# 3: `logging_call`, which also widened what `suppressed` can mean.
# 4: `killreason`, which carries the stable reason enumeration for every
#    verdict (assertion_failed, test_errored, execution_crash, flaky_probe,
#    or null).
RECORD_SCHEMA = 4

# The version of the run summary -- `summary.json` and `--json`. Separate from
# RECORD_SCHEMA because they are separate documents that will move for separate
# reasons; a consumer of one must not be told the other changed.
#
# 1: the first machine-readable run summary.
SUMMARY_SCHEMA = 1

# What a record written before RECORD_SCHEMA existed does not say. Filled in on
# read so `Record` is honestly total=True: an old file gets today's shape, and
# every consumer -- moonbuggy's own human report included -- can index the keys
# instead of guessing at them with `.get()`.
_SCHEMA_1_DEFAULTS: dict[str, object] = {"accepted": False, "accept_reason": None}

# The same, for the key schema 3 added. False is the honest fill: a run by a
# version with no logging policy generated these mutants without recognising
# any of them, so "not a logging mutant" is what that file actually claims.
_SCHEMA_2_DEFAULTS: dict[str, object] = {"logging_call": False}

# The same, for the key schema 4 added. None is the honest fill: a record
# written by a version with no killreason enumeration has no reason to report,
# and a reader that sees None knows the field was absent rather than empty.
_SCHEMA_3_DEFAULTS: dict[str, object] = {"killreason": None}

# The whole status vocabulary. Every plaintext line begins with one of these,
# so adding a keyword is a breaking change for anyone grepping: NO_COVERAGE
# arrived after 0.1.2 and took the uncovered lines that used to be SURVIVED
# with it, and KILLED_BY_ERROR arrived next and took the crash-kills that used
# to be KILLED. `summarise` seeds its counts from here, so a new keyword also
# appears in the run's final summary line with a count of zero.
STATUS_KEYWORDS = {
    "KILLED",
    "KILLED_BY_ERROR",
    "SURVIVED",
    "NO_COVERAGE",
    "TIMEOUT",
    "SUSPICIOUS",
    "SKIPPED",
}

# The statuses that mean the mutation was noticed. Both count toward the kill
# rate: a crash-kill is still a kill, and dropping it from the numerator would
# report a *lower* score for a suite that did notice. What KILLED_BY_ERROR
# says is what the kill proves -- that the tests execute the line, not that
# they check it -- which is a distinction to read, not to score.
KILL_STATUSES = frozenset({"KILLED", "KILLED_BY_ERROR"})

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
    """The canonical JSONL record for one mutant -- `record_for`'s return shape.

    Every key is required, including the ones added after the format shipped.
    That is what `schema` buys: :func:`read_jsonl` upgrades an older line to
    this shape as it reads it, so the promise holds for a file written by any
    version rather than only for a record made in this process.
    """

    schema: int
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
    logging_call: bool
    original: str
    mutated: str
    diff: str
    accepted: bool
    accept_reason: str | None
    killreason: str | None
    """Why this mutant was killed, or None. One of the
    :mod:`moonbuggy.killreason` enumeration, stable across runs -- a consumer
    comparing two records compares this token directly. None for any status
    where no reason applies (survivors, timeouts, uncovered lines, skipped
    mutants). Added with record schema 4."""


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
        # Written by every record rather than inferred from the keys present:
        # "this line lacks `accepted`" and "this line was written by a version
        # that had no ledger" are the same observation only by luck.
        "schema": RECORD_SCHEMA,
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
        # True whenever this mutant was settled without running -- the skip
        # marker, or a suppressed logging mutant. `logging_call` says which,
        # and stays true under `--include-logging-mutants` when the mutant
        # really did run, so triage can filter on it either way.
        "suppressed": mutant.suppressed,
        "logging_call": mutant.logging_call,
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
        # The stable reason id, from the same enumeration the plaintext
        # line carries. None for any status where no reason applies.
        "killreason": result.killreason,
    }


def write_jsonl(
    results: Iterable[Result],
    path: str | os.PathLike[str],
    reasons: Mapping[str, str] | None = None,
) -> None:
    """Stream records to disk, one complete line at a time.

    Flushed per record so a run killed partway leaves only whole, parseable
    lines behind. A half-written final record would break every
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

    A run killed mid-flight has to leave something a later reader can parse,
    not a truncated final line. Every record is written and
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
    """Read every record back from a JSONL file, in file order.

    Records written by an older moonbuggy are upgraded to the current shape as
    they are read, so a caller never has to ask which version wrote the file.

    Args:
        path: the file to read.

    Returns:
        The records, each one a complete :class:`Record`.
    """
    with open(path, encoding="utf-8") as handle:
        return [_upgraded(json.loads(line)) for line in handle if line.strip()]


def _upgraded(record: dict[str, object]) -> Record:
    """One record in today's shape, whatever version wrote it.

    Args:
        record: a record as it was read off disk.

    Returns:
        The same record with anything its schema predates filled in. Values it
        does carry are never overwritten -- an upgrade adds, it does not
        reinterpret.
    """
    version = record.get("schema")
    if version == RECORD_SCHEMA:
        return record  # type: ignore[return-value]  # TypedDict is dict at runtime; cast would be a no-op
    # No `schema` key at all is schema 1 by definition: the field arrived with
    # schema 2, so its absence is the version rather than a missing value.
    #
    # Defaults are layered oldest first and the record goes on top, so a
    # schema-2 line gains only what schema 3 added and a schema-1 line gains
    # both. Adding a fourth version means adding one more mapping here, not
    # branching on the version number.
    upgraded = {
        **_SCHEMA_1_DEFAULTS,
        **_SCHEMA_2_DEFAULTS,
        **_SCHEMA_3_DEFAULTS,
        **record,
    }
    upgraded.setdefault("schema", 1)
    return upgraded  # type: ignore[return-value]  # same reason as the early-return above


def render_line(record: Record) -> str:
    """One plaintext line for one record. Never contains a newline.

    Whitespace-separated, in a fixed field order: status, ``file:line``,
    category, ``line=``, ``nearest_test=``, ``tests_run=``, ``killreason=``,
    ``id=``. The status is padded to nine columns for the eye only --
    ``NO_COVERAGE`` and ``KILLED_BY_ERROR`` are longer and push the rest of
    the line right, so parse by splitting rather than by column.

    Args:
        record: the record to render.

    Returns:
        The line.
    """
    return " ".join(
        [
            f"{record['status']:<9}",
            f"{record['file']}:{record['line']}",
            record["category"],
            f"line={record['line']}",
            f"nearest_test={record['nearest_test'] or ABSENT}",
            f"tests_run={record['tests_run']}",
            f"killreason={record['killreason'] or ABSENT}",
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


def run_summary(
    records: Iterable[Record],
    *,
    elapsed: float,
    cached: int,
    config: Mapping[str, object],
    scope: Mapping[str, object],
    acceptance: Mapping[str, object],
    exit_code: int,
) -> dict[str, object]:
    """The whole run as one JSON-serialisable object.

    A run has exactly one of these, which is why it is an object and not a line
    in `results.jsonl`: that file is one record per mutant, and a reader
    counting its lines is counting mutants. Keeping the summary out of it means
    no consumer has to learn a discriminator to avoid mistaking the two.

    The `config`, `scope` and `acceptance` mappings are carried through
    verbatim from the components that own them --
    :func:`moonbuggy.diffscope.scope_summary` and
    :meth:`moonbuggy.accepted.Acceptance.summary` -- so the summary reports
    what those made of the run rather than a second opinion about it.

    Args:
        records: the run's records, as `results.jsonl` holds them.
        elapsed: wall time for the whole run, in seconds.
        cached: how many verdicts were served from the results cache.
        config: the run's effective configuration.
        scope: the run's diff scope, from `scope_summary`.
        acceptance: the ledger outcome, from `Acceptance.summary`.
        exit_code: the code the process is about to exit with, so a consumer
            reading the file afterwards need not re-derive the gate's answer.

    Returns:
        The summary, with twelve keys: `schema` (this document's version),
        `record_schema` (the version of the records beside it), `moonbuggy`
        (the version string), `total`, `cached`, `measured`, `elapsed`,
        `exit_code`, `counts` (one lower-cased key per status keyword),
        `acceptance`, `scope` and `config` (the three mappings carried through
        verbatim).
    """
    counts = summarise(records)
    total = sum(counts.values())
    return {
        "schema": SUMMARY_SCHEMA,
        # The results file's version, not this document's. A consumer holding
        # a summary can tell what shape the records beside it are in without
        # opening them.
        "record_schema": RECORD_SCHEMA,
        "moonbuggy": __version__,
        "total": total,
        "cached": cached,
        # Stated rather than left as a subtraction, because "how much of this
        # run was actually measured" is the question people ask of a cached
        # run and getting it wrong by one is easy.
        "measured": total - cached,
        "elapsed": round(elapsed, 3),
        "exit_code": exit_code,
        # Lower-cased keys: the plaintext keywords are shouted because they
        # begin a line a human's eye scans, and neither reason applies inside
        # an object a parser reads.
        "counts": {status.lower(): count for status, count in counts.items()},
        "acceptance": dict(acceptance),
        "scope": dict(scope),
        "config": dict(config),
    }


def write_summary(summary: Mapping[str, object], path: str | os.PathLike[str]) -> None:
    """Write one run summary to disk, as a single JSON object.

    Args:
        summary: the summary, as :func:`run_summary` returns it.
        path: the file to write.
    """
    # Indented and newline-terminated: unlike results.jsonl this is one object
    # per file, so there is no line-per-record property to preserve and a
    # human opening it should be able to read it.
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")


def find_record(records: Iterable[Record], mutant_id: str) -> Record | None:
    """The record with this mutant id, or None if there is not one."""
    for record in records:
        if record["id"] == mutant_id:
            return record
    return None
