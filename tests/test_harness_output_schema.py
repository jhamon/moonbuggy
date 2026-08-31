"""The D2 numbers pipe is a contract, pinned by validation, not by care.

Performance numbers quoted in docs, dashboards, or PRs are supposed to come
from a conforming harness-output document. This test is the freeze on that
contract: it pins the schema version, the required key set, and a golden
document, and it refuses the shape to drift under a reader the way the agent
format test refuses the plaintext line to drift. A field that must be *added*
raises the schema version; a field that stops validating fails here.

The validator itself is exercised against its own gauntlet of bad documents
rather than trusted on one good example, so a validator that has gone too
permissive (accepting anything) or too strict (rejecting conforming rows) is
caught by the negative cases below.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import harness_output  # noqa: E402
from harness_output import build, validate  # noqa: E402

# The version the schema pins; bump ONLY in lockstep with the schema file's
# "schema" const, which is what a reader keys off.
SCHEMA_VERSION = 1

# The canonical set of fields every row must carry. A field promoted into the
# required set is a version bump, so the list is frozen here for review rather
# than trusted to match the schema's "required" array silently.
REQUIRED_FIELDS = (
    "schema",
    "suite",
    "hypothesis",
    "purpose",
    "harness",
    "moonbuggy",
    "commit",
    "python",
    "host",
    "timestamp",
    "wall_clock",
    "runs",
    "mutants",
    "mutants_per_sec",
    "memory_delta",
)


def _golden(**overrides):
    """Build a reference row, overridable per-test.

    Args:
        **overrides: values to substitute into the golden row.

    Returns:
        A conforming harness-output document.
    """
    base = {
        "suite": "slow-tests",
        "purpose": "bench",
        "harness": "bench_mutation.py",
        "wall_clock": 0.940,
        "hypothesis": "H21",
        "moonbuggy": "0.2.0",
        "mutants": 84,
    }
    base.update(overrides)
    return build(**base)


def test_the_schema_version_is_frozen():
    schema = harness_output.load_schema()
    assert schema["properties"]["schema"]["const"] == SCHEMA_VERSION


def test_the_required_field_set_is_frozen():
    schema = harness_output.load_schema()
    assert tuple(schema["required"]) == REQUIRED_FIELDS


def test_the_golden_row_validates():
    assert validate(_golden()) == []


def test_build_produces_the_expected_shape():
    row = _golden()
    assert row["schema"] == SCHEMA_VERSION
    assert row["suite"] == "slow-tests"
    assert row["hypothesis"] == "H21"
    assert row["mutants"] == 84
    assert row["mutants_per_sec"] == pytest.approx(84 / 0.940)


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda r: r.pop("wall_clock"), "missing required field: wall_clock"),
        (lambda r: r.pop("hypothesis"), "missing required field: hypothesis"),
        (lambda r: r.pop("memory_delta"), "missing required field: memory_delta"),
        (lambda r: r.update({"schema": 2}), "schema: expected constant 1"),
        (lambda r: r.update({"wall_clock": 0.0}), "wall_clock: must be > 0"),
        (lambda r: r.update({"wall_clock": "fast"}), "wall_clock: expected number"),
        (lambda r: r.update({"mutants": -1}), "mutants: must be >= 0"),
        (lambda r: r.update({"suite": ""}), "suite: shorter than minLength 1"),
        (lambda r: r.update({"purpose": "benchmark"}), "purpose: not one of"),
        (lambda r: r.update({"commit": "abc"}), "commit: shorter than minLength 7"),
        (lambda r: r.update({"surprise": 1}), "unknown fields"),
        (lambda r: r.update({"timestamp": "yesterday"}), "not an ISO-8601 date-time"),
    ],
)
def test_the_validator_rejects_corrupted_rows(mutation, expected):
    row = _golden()
    mutation(row)
    errors = validate(row)
    assert errors, "a corrupted row validated clean -- the validator is too permissive"
    assert any(expected in error for error in errors)


def test_unknown_field_reports_every_unknown_key():
    row = _golden()
    row["zzz"] = 1
    row["aaa"] = 2
    errors = validate(row)
    unknown = [e for e in errors if e.startswith("unknown fields")]
    assert len(unknown) == 1
    assert "aaa" in unknown[0] and "zzz" in unknown[0]


def test_optional_fields_validate_and_replace_nothing():
    row = _golden(
        runs=7,
        memory_delta=-1024,
        memory_baseline_bytes=2048,
        median=0.90,
        min_=0.85,
        interval=[0.86, 0.95],
    )
    assert validate(row) == []
    assert row["runs"] == 7
    assert row["interval"] == [0.86, 0.95]


def test_memory_delta_is_derivable_and_zero_is_valid():
    # A single measurement with no comparator records 0; that is a statement of
    # "no comparator", not a measurement of zero delta.
    row = _golden(memory_delta=0.0)
    assert validate(row) == []


def test_write_and_read_roundtrip(tmp_path):
    out = tmp_path / "harness-output.jsonl"
    for i in range(2):
        harness_output.write_jsonl(_golden(mutants=84 + i), out)
    rows = harness_output.read_jsonl(out)
    assert len(rows) == 2
    assert validate(rows[0]) == []
    assert rows[1]["mutants"] == 85


def test_every_field_in_the_golden_is_documented_in_the_schema():
    schema = harness_output.load_schema()
    golden = _golden()
    for key in golden:
        assert key in schema["properties"], f"{key} not in the schema"
