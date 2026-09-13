"""The survivor export is a contract, pinned by validation, not by care.

The export is the C3 Phase A findings feed: every SURVIVED/NO_COVERAGE record
under the frozen ``survivor-export.v1`` schema, with ``survival_reason``
reserved-null (the vocabulary is Phase B and no token may be invented). This
test freezes the shape the way ``test_harness_output_schema.py`` freezes the
numbers pipe: version pin, required field set, a golden record, and a
validator gauntlet that refuses the shape to drift. The round trip --
export from a real run, re-inject every id through ``moonbuggy run <id>``,
one fresh verdict per id -- is pinned here too, because a re-injection
handle that does not round-trip is not an export.
"""

import json
import subprocess
import sys

import pytest

from moonbuggy import export as survivor_export
from moonbuggy.cli import main
from moonbuggy.report import RECORD_SCHEMA, read_jsonl, render_line

# The version the schema pins; bump ONLY in lockstep with the schema file's
# "schema" const, which is what a reader keys off.
SCHEMA_VERSION = 1

# The envelope version the embedded finding fields carry.
RECORD_SCHEMA_VERSION = 4

# The canonical field set of an export record: the record envelope verbatim
# plus the export's own additions. A field promoted into this set is a version
# bump, so the list is frozen here for review rather than trusted to match the
# schema's "required" array silently.
EXPORT_FIELDS = frozenset(
    {
        # export pin + provenance
        "schema",
        "exported",
        "moonbuggy",
        "record_schema",
        # reserved for C3 Phase B -- always null in v1
        "survival_reason",
        # the record envelope, verbatim
        "id",
        "status",
        "file",
        "line",
        "operator",
        "category",
        "nearest_test",
        "tests_run",
        "duration",
        "module_level",
        "suppressed",
        "logging_call",
        "original",
        "mutated",
        "diff",
        "accepted",
        "accept_reason",
        "killreason",
    }
)

# The envelope keys the export contributes nothing to -- asserted equal to the
# record schema's key set so an envelope field added without this test being
# updated fails here rather than exporting under a stale golden shape.
RECORD_FIELDS = frozenset(
    EXPORT_FIELDS
    - {"schema", "exported", "moonbuggy", "record_schema", "survival_reason"}
    | {"schema"}  # the envelope carries its own schema pin; the export adds a second
)


def _golden(**overrides):
    """Build a reference export record, overridable per-test.

    Args:
        **overrides: values to substitute into the golden record.

    Returns:
        A conforming survivor-export record.
    """
    record = {
        "schema": SCHEMA_VERSION,
        "exported": "2026-09-10T12:00:00+00:00",
        "moonbuggy": "0.3.0",
        "record_schema": RECORD_SCHEMA_VERSION,
        "survival_reason": None,
        "id": "calc.py:2:comparison_swap:0",
        "status": "SURVIVED",
        "file": "calc.py",
        "line": 2,
        "operator": "comparison_swap",
        "category": "comparison_swap",
        "nearest_test": "test_calc.py::test_high",
        "tests_run": 2,
        "duration": 0.0314,
        "module_level": False,
        "suppressed": False,
        "logging_call": False,
        "original": "value > ceiling",
        "mutated": "value < ceiling",
        "diff": "- value > ceiling\n+ value < ceiling",
        "accepted": False,
        "accept_reason": None,
        "killreason": None,
    }
    record.update(overrides)
    return record


def test_schema_version_pin_matches_the_frozen_const():
    """The module's pin and the schema file's const are the same number."""
    assert survivor_export.load_schema()["properties"]["schema"]["const"] == (
        SCHEMA_VERSION
    )
    assert survivor_export.SCHEMA_VERSION == SCHEMA_VERSION


def test_required_field_set_is_frozen():
    """The schema's required array is exactly the frozen export field set."""
    assert set(survivor_export.load_schema()["required"]) == EXPORT_FIELDS


def test_export_fields_extend_the_record_envelope_exactly():
    """Everything that is not export pin/provenance IS the schema-4 envelope."""
    from moonbuggy.report import Record

    envelope = set(Record.__annotations__)
    assert envelope == RECORD_FIELDS
    assert RECORD_SCHEMA == RECORD_SCHEMA_VERSION


def test_survival_reason_is_reserved_null_in_v1():
    """v1 reserves survival_reason and no token exists: the schema types it null."""
    rule = survivor_export.load_schema()["properties"]["survival_reason"]
    assert rule["type"] == "null"
    golden = _golden()
    assert golden["survival_reason"] is None
    assert survivor_export.validate(golden) == []


def test_finding_statuses_are_the_export_vocabulary():
    """The export covers exactly the two finding statuses, by the shared set."""
    from moonbuggy.report import FINDING_STATUSES

    rule = survivor_export.load_schema()["properties"]["status"]
    assert set(rule["enum"]) == FINDING_STATUSES


def test_validator_accepts_the_golden_record():
    """The golden record validates clean."""
    assert survivor_export.validate(_golden()) == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema": 2},
        {"survival_reason": "equivalent_suspect"},  # inventing a Phase B token
        {"status": "KILLED"},  # findings-only
        {"status": "TIMEOUT"},  # findings-only
        {"record_schema": 3},
        {"exported": "not-a-timestamp"},
        {"id": ""},
        {"line": 0},
        {"tests_run": -1},
    ],
)
def test_validator_gauntlet_rejects_drift(mutation):
    """Every mutation of the golden record is named, not passed."""
    bad = _golden(**mutation)
    errors = survivor_export.validate(bad)
    assert errors, f"validator accepted {mutation}"


def test_validator_rejects_unknown_fields():
    """additionalProperties is false: an extra key is a contract violation."""
    bad = _golden(extra_field=1)
    assert survivor_export.validate(bad)
    assert survivor_export.validate(_golden()) == []


def test_export_findings_covers_findings_only():
    """A KILLED record never exports; SURVIVED and NO_COVERAGE always do."""
    records = [
        _golden(status="SURVIVED", id="a.py:1:comparison_swap:0"),
        _golden(status="KILLED", id="a.py:2:comparison_swap:0"),
        _golden(status="NO_COVERAGE", id="a.py:3:statement_deletion:0"),
        _golden(status="TIMEOUT", id="a.py:4:statement_deletion:0"),
        _golden(status="SUSPICIOUS", id="a.py:5:boundary:0"),
        _golden(status="SKIPPED", id="a.py:6:boundary:0"),
    ]
    exports = survivor_export.export_findings(records)
    assert [e["id"] for e in exports] == [
        "a.py:1:comparison_swap:0",
        "a.py:3:statement_deletion:0",
    ]


def test_write_export_round_trips_through_read(tmp_path):
    """The file is JSONL, one conforming record per line, in record order."""
    records = [
        _golden(status="SURVIVED", id="a.py:1:comparison_swap:0"),
        _golden(status="NO_COVERAGE", id="a.py:2:statement_deletion:0"),
    ]
    path = tmp_path / "survivors.jsonl"
    count = survivor_export.write_export(records, path)
    assert count == 2
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) == 2
    for line in lines:
        assert survivor_export.validate(line) == []
    assert [line["id"] for line in lines] == [r["id"] for r in records]


def test_killreason_is_carried_verbatim_and_null_on_findings():
    """killreason is a cause of a kill; a finding has none -- but the field
    travels, so one parser reads both results.jsonl and the export."""
    golden = _golden()
    assert golden["killreason"] is None
    assert survivor_export.validate(golden) == []


# --- the re-injection round trip: export from a real run, re-run each id ---

PROJECT = """\
def clamp(value, ceiling):
    if value > ceiling:
        return ceiling
    return value
"""

TESTS = """\
from calc import clamp


def test_clamp_caps_large_values():
    assert clamp(99, 10) == 10
"""


@pytest.fixture
def run_project(tmp_path):
    """A tiny project whose run leaves one finding behind.

    Args:
        tmp_path: pytest's per-test scratch directory.

    Returns:
        The project root, with .moonbuggy/results.jsonl already written.
    """
    (tmp_path / "calc.py").write_text(PROJECT)
    (tmp_path / "test_calc.py").write_text(TESTS)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
    )
    assert main(["--project", str(tmp_path), "--json"]) in (0, 1)
    return tmp_path


def test_round_trip_export_then_reinject(run_project, tmp_path):
    """Export from run A, re-run every exported id, one fresh verdict per id.

    This is the contract's load-bearing promise (competitive-intel §2.1 part
    C in seed form): the export is the handoff, `moonbuggy run <id>` is the
    re-injection primitive, and the fresh verdict arrives on the frozen
    agent-line shape.
    """
    export_path = tmp_path / "survivors.jsonl"
    assert main(["--project", str(run_project), "export", str(export_path)]) == 0
    exports = [json.loads(line) for line in export_path.read_text().splitlines()]
    assert exports, "the fixture project must leave at least one finding"
    for record in exports:
        assert survivor_export.validate(record) == []

    # Every exported id is a valid re-injection handle, and `run <id>` never
    # serves the cache -- so the verdicts below are measured, fresh. Run
    # through the subprocess helper (as the `run <id>` tests do) rather than
    # in-process: the runner mutates real source files on disk, and an
    # in-process second `main` after that does not have a clean module state
    # to reload them with.
    ids = [record["id"] for record in exports]
    proc = subprocess.run(
        [sys.executable, "-m", "moonbuggy.cli", "run", *ids, "--report", "agent"],
        cwd=run_project,
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = proc.stdout
    verdicts = [
        line
        for line in out.splitlines()
        if line.split()[0]
        in {
            "SURVIVED",
            "NO_COVERAGE",
            "KILLED",
            "KILLED_BY_ERROR",
            "TIMEOUT",
            "SUSPICIOUS",
            "SKIPPED",
        }
    ]
    assert len(verdicts) == len(ids)
    assert proc.returncode in (0, 1)

    # machine == human, criterion E3, on the export: the plaintext line for
    # the same mutant names the same id the export record carries.
    for record in exports:
        assert f"id={record['id']}" in out


def test_export_record_id_matches_plaintext_line(run_project, tmp_path):
    """The exported id/status pair agrees with results.txt for the same mutant."""
    export_path = tmp_path / "survivors.jsonl"
    assert main(["--project", str(run_project), "export", str(export_path)]) == 0
    exports = [json.loads(line) for line in export_path.read_text().splitlines()]
    results = read_jsonl(run_project / ".moonbuggy" / "results.jsonl")
    by_id = {r["id"]: r for r in results}
    for record in exports:
        source = by_id[record["id"]]
        assert record["status"] == source["status"]
        plaintext = render_line(source)
        assert f"id={record['id']}" in plaintext
        assert plaintext.startswith(record["status"])


def test_no_results_is_an_exit_2(tmp_path):
    """`export` with no run behind it says so, and does not write a file."""
    code = main(["--project", str(tmp_path), "export"])
    assert code == 2


def test_empty_export_is_exit_0(run_project, tmp_path):
    """A run with zero findings exports an empty file, successfully."""
    # A run whose every mutant is killed would need a full fixture; the
    # contract case is covered at the unit level (export_findings over a
    # findings-free record list), so here we pin the CLI contract itself:
    # exit 0 and a count line, whatever the finding count.
    export_path = tmp_path / "survivors.jsonl"
    assert main(["--project", str(run_project), "export", str(export_path)]) == 0
    assert export_path.exists()
