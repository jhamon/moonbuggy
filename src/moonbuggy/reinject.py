"""Verdict transitions for the re-injection loop (`moonbuggy run --against`).

A fresh verdict on a re-measured mutant only means something next to the
verdict it replaces. The cost model (docs/development/reinjection-cost-model.md)
prices that loop: batched `moonbuggy run -` at ~5s/id is ~24x cheaper than
re-running ids one at a time, so the documented consumer loop is "export, fix
tests, replay the whole finding set in one batched invocation". What the loop
was missing is a machine-readable answer to "which ids *changed*, and did any
of them change for a reason worth acting on?".

The transition vocabulary is deliberately NOT a new free-form string: it is a
closed pairing of the prior export status with the fresh verdict, and the only
token that says "your new test checked the mutant" is
`survived->assertion_failed` (or `no_coverage->assertion_failed`). A fresh
KILLED_BY_ERROR is a crash, not a catch — docs/closing-the-loop.md's
"KILLED_BY_ERROR is not a kill" rule — so it gets its own token rather than
collapsing into KILLED.
"""

from __future__ import annotations

from dataclasses import dataclass

# The re-injection transition document's own version, independent of the
# survivor-export pin (v1.1) and of RECORD_SCHEMA/TRACE_SCHEMA: this document
# is a pairing of one frozen record with one fresh trace, and it will move for
# its own reasons (same reasoning as TRACE_SCHEMA beside RECORD_SCHEMA).
REINJECT_SCHEMA = 1

# The closed transition vocabulary. Prior status comes from a survivor-export
# record, whose frozen status enum is exactly {SURVIVED, NO_COVERAGE}; the
# fresh side is a verdict keyword. Tokens are spelled `prior->fresh` in
# lowercase, with the fresh side folded: KILLED and KILLED_BY_ERROR stay
# distinct because only assertion_failed proves a test checked the mutant.
TRANSITION_TOKENS = frozenset(
    {
        "survived->assertion_failed",
        "survived->killed_by_error",
        "survived->survived",
        "survived->no_coverage",
        "survived->timeout",
        "survived->suspicious",
        "survived->skipped",
        "no_coverage->assertion_failed",
        "no_coverage->killed_by_error",
        "no_coverage->no_coverage",
        "no_coverage->timeout",
        "no_coverage->suspicious",
        "no_coverage->skipped",
        "no_coverage->survived",
    }
)

# The fresh-side killreason token that proves a test checked the mutant, per
# docs/closing-the-loop.md. Everything else a kill-shaped verdict can carry is
# not a catch.
ASSERTION_FAILED = "assertion_failed"


def transition_token(
    prior_status: str, fresh_status: str, killreason: str | None
) -> str:
    """The transition token for one re-measured finding.

    Args:
        prior_status: the export record's status (SURVIVED or NO_COVERAGE).
        fresh_status: the fresh verdict's keyword.
        killreason: the fresh verdict's killreason, which decides whether a
            kill-shaped verdict is a real catch (assertion_failed) or a crash
            (test_errored / execution_crash).

    Returns:
        A token from :data:`TRANSITION_TOKENS`. `KILLED` folds to
        `assertion_failed` only when the killreason says so; a KILLED verdict
        that somehow carries no assertion killreason folds to `killed_by_error`
        on the "not proven" side, which is the conservative direction.
    """
    fresh = fresh_status.lower()
    if fresh_status == "KILLED":
        # The only killreason that proves a test checked the mutant; anything
        # else folds to the crash side, which is the conservative direction.
        if killreason == ASSERTION_FAILED:
            fresh = "assertion_failed"
        else:
            fresh = "killed_by_error"
    token = f"{prior_status.lower()}->{fresh}"
    if token not in TRANSITION_TOKENS:
        raise ValueError(f"unknown transition token: {token}")
    return token


# The two tokens a consumer gates on when the question is "did my new tests
# check anything that used to slip through".
CAUGHT_TOKENS = frozenset(
    {
        "survived->assertion_failed",
        "no_coverage->assertion_failed",
    }
)


@dataclass(frozen=True)
class PriorFinding:
    """One record of the export a re-injection run is measured against."""

    id: str
    status: str
    survival_reason: str | None
    tests_run: int


def load_prior(findings_path: str) -> dict[str, PriorFinding]:
    """Read a survivor-export file into prior findings by mutant id.

    Args:
        findings_path: path to a `survivors.jsonl` written by
            `moonbuggy export` (survivor-export v1 records, one per line).

    Returns:
        Prior findings keyed by id. Duplicate ids keep the first — an export
        never contains one, and refusing to guess which line wins is cheaper
        than silently pairing a verdict with the wrong prior.
    """
    import json
    from pathlib import Path

    priors: dict[str, PriorFinding] = {}
    for line in Path(findings_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        finding = PriorFinding(
            id=record["id"],
            status=record["status"],
            survival_reason=record.get("survival_reason"),
            tests_run=record.get("tests_run", 0),
        )
        priors.setdefault(finding.id, finding)
    return priors
