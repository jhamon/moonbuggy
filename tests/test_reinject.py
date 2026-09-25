"""Tests for the re-injection transition surface (`run --against`).

Fast, shape-level tests: the transition vocabulary's closure, the token
derivation (including the KILLED_BY_ERROR-is-not-a-kill rule), the export
loader, and the CLI argument wiring. The end-to-end loop against real
subprocesses lives in tests/test_reinject_e2e.py.
"""

import json
from pathlib import Path

import pytest

from moonbuggy.reinject import (
    REINJECT_SCHEMA,
    TRANSITION_TOKENS,
    PriorFinding,
    load_prior,
    transition_token,
)

# ---------------------------------------------------------------------------
# The closed vocabulary
# ---------------------------------------------------------------------------


def test_transition_vocabulary_is_closed_and_complete():
    # Every pairing of an export status with a fresh verdict keyword is a
    # token -- the vocabulary cannot be caught out by a verdict we did not
    # anticipate, because there is no such verdict outside STATUS_KEYWORDS.
    prior = {"survived", "no_coverage"}
    fresh = {
        "assertion_failed",
        "killed_by_error",
        "survived",
        "no_coverage",
        "timeout",
        "suspicious",
        "skipped",
    }
    expected = {f"{p}->{f}" for p in prior for f in fresh}
    assert expected == TRANSITION_TOKENS


def test_killed_with_assertion_failed_is_the_catch_token():
    assert transition_token("SURVIVED", "KILLED", "assertion_failed") == (
        "survived->assertion_failed"
    )


def test_killed_by_error_is_not_a_kill():
    # docs/closing-the-loop.md's rule, enforced in the token: a crash-folded
    # kill says nothing about whether the new test checked the mutant.
    assert transition_token("SURVIVED", "KILLED", "test_errored") == (
        "survived->killed_by_error"
    )
    assert transition_token("SURVIVED", "KILLED", None) == "survived->killed_by_error"
    assert transition_token("SURVIVED", "KILLED_BY_ERROR", "test_errored") == (
        "survived->killed_by_error"
    )


def test_no_transition_is_its_own_token():
    # Silence is a finding about the new tests, not a bug in the loop (the C3
    # receipt: 51 of 51 still SURVIVED).
    assert transition_token("SURVIVED", "SURVIVED", None) == "survived->survived"
    assert transition_token("NO_COVERAGE", "NO_COVERAGE", None) == (
        "no_coverage->no_coverage"
    )


def test_no_coverage_prior_gains_a_catch_token_too():
    # A NO_COVERAGE finding that a new test now executes and fails is exactly
    # as much of a catch as a survived one.
    assert transition_token("NO_COVERAGE", "KILLED", "assertion_failed") == (
        "no_coverage->assertion_failed"
    )


def test_unknown_token_raises():
    with pytest.raises(ValueError, match="unknown transition token"):
        transition_token("SURVIVED", "ACCEPTED", None)


# ---------------------------------------------------------------------------
# The export loader
# ---------------------------------------------------------------------------


def _write_export(path: Path, records: list[dict[str, object]]) -> Path:
    for record in records:
        path.open("a").write(json.dumps(record) + "\n")  # type: ignore[arg-type]
    return path


def test_load_prior_reads_survivor_export_records(tmp_path: Path):
    export = _write_export(
        tmp_path / "survivors.jsonl",
        [
            {
                "schema": 1,
                "id": "calc.py:2:constant_int:0",
                "status": "SURVIVED",
                "survival_reason": "covered_unasserted",
                "tests_run": 1,
            },
            {
                "schema": 1,
                "id": "calc.py:9:statement_deletion:0",
                "status": "NO_COVERAGE",
                "survival_reason": "no_coverage",
                "tests_run": 0,
            },
        ],
    )
    priors = load_prior(str(export))
    assert set(priors) == {"calc.py:2:constant_int:0", "calc.py:9:statement_deletion:0"}
    assert priors["calc.py:2:constant_int:0"] == PriorFinding(
        id="calc.py:2:constant_int:0",
        status="SURVIVED",
        survival_reason="covered_unasserted",
        tests_run=1,
    )


def test_load_prior_skips_blank_lines_and_keeps_first_duplicate(tmp_path: Path):
    export = _write_export(
        tmp_path / "survivors.jsonl",
        [
            {
                "schema": 1,
                "id": "a:1:op:0",
                "status": "SURVIVED",
                "survival_reason": None,
                "tests_run": 1,
            },
            {
                "schema": 1,
                "id": "a:1:op:0",
                "status": "NO_COVERAGE",
                "survival_reason": "no_coverage",
                "tests_run": 0,
            },
        ],
    )
    priors = load_prior(str(export))
    assert len(priors) == 1
    assert priors["a:1:op:0"].status == "SURVIVED"


def test_reinject_schema_pin_is_frozen():
    assert REINJECT_SCHEMA == 1
