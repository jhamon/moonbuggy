"""End-to-end: the batched re-injection loop with transitions, on a real project.

`moonbuggy run - --trace-json --against survivors.jsonl --flaky-probe 0` is the
documented consumer surface for survivors.jsonl: one coverage pass serves every
id, and each trace pairs the frozen export record with the fresh verdict under
a closed transition token. Slow (real subprocess runs), like every e2e.
"""

import json

import pytest

pytestmark = pytest.mark.slow

PROJECT = """\
def bump(value):
    return value + 1
"""

TESTS_WEAK = """\
from calc import bump

def test_bump_runs():
    bump(1)
"""

TESTS_STRONG = """\
from calc import bump

def test_bump_adds_one():
    assert bump(1) == 2
"""


@pytest.fixture
def throwaway(tmp_path):
    (tmp_path / "calc.py").write_text(PROJECT)
    (tmp_path / "test_calc.py").write_text(TESTS_WEAK)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
    )
    return tmp_path


def _moonbuggy(project, *args, stdin=None, expect=None):
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "moonbuggy.cli", *args],
        cwd=project,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if expect is not None:
        assert proc.returncode == expect, (
            f"{proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        )
    return proc


def _export_ids(project):
    lines = (project / "survivors.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line]


def test_batched_reinjection_reports_the_catch_transition(throwaway):
    _moonbuggy(throwaway, expect=1)
    _moonbuggy(throwaway, "export", expect=0)
    findings = _export_ids(throwaway)
    assert findings, "the weak test must leave findings"

    (throwaway / "test_calc.py").write_text(TESTS_STRONG)
    ids = "".join(f"{f['id']}\n" for f in findings)
    proc = _moonbuggy(
        throwaway,
        "run",
        "-",
        "--trace-json",
        "--against",
        "survivors.jsonl",
        "--flaky-probe",
        "0",
        stdin=ids,
    )

    traces = [json.loads(line) for line in proc.stdout.splitlines() if line]
    assert len(traces) == len(findings)
    for trace in traces:
        reinject = trace["reinject"]
        assert reinject["reinject_schema"] == 1
        assert reinject["prior"]["status"] == "SURVIVED"
        assert reinject["transition"] == "survived->assertion_failed"
        assert trace["verdict"]["killreason"] == "assertion_failed"
    # Exit 0: nothing re-measured is still a finding.
    assert proc.returncode == 0
    # The transition tally names the catch and nothing else.
    assert "transitions=survived->assertion_failed=2" in proc.stderr
    assert "caught=2" in proc.stderr


def test_batched_reinjection_reports_no_transition_honestly(throwaway):
    # Re-measure against the SAME weak tests: every id must come back
    # survived->survived and still gate the exit code, so silence reads as a
    # finding about the new tests rather than a pass.
    _moonbuggy(throwaway, expect=1)
    _moonbuggy(throwaway, "export", expect=0)
    findings = _export_ids(throwaway)
    ids = "".join(f"{f['id']}\n" for f in findings)

    proc = _moonbuggy(
        throwaway,
        "run",
        "-",
        "--trace-json",
        "--against",
        "survivors.jsonl",
        "--flaky-probe",
        "0",
        stdin=ids,
        expect=1,
    )
    traces = [json.loads(line) for line in proc.stdout.splitlines() if line]
    assert all(
        trace["reinject"]["transition"] == "survived->survived" for trace in traces
    )
    assert "caught=0" in proc.stderr


def test_against_requires_trace_json(throwaway):
    _moonbuggy(throwaway, expect=1)
    proc = _moonbuggy(
        throwaway,
        "run",
        "calc.py:2:arithmetic_swap:0",
        "--against",
        "survivors.jsonl",
    )
    assert proc.returncode == 2
    assert "--trace-json" in proc.stderr


def test_against_rejects_ids_the_export_does_not_name(throwaway):
    # An id that no longer resolves (line moved) is caught by resolve_targets
    # before the prior check; an id that resolves but the export does not name
    # is caught by the prior check. Test the one --against is responsible for:
    # an id that resolves but the export genuinely does not carry.
    _moonbuggy(throwaway, expect=1)
    _moonbuggy(throwaway, "export", expect=0)
    findings = _export_ids(throwaway)
    assert findings
    # Use a real finding's id but point --against at an export from a
    # different (empty) run, so the id resolves yet has no prior.
    (throwaway / "other.jsonl").write_text("")
    proc = _moonbuggy(
        throwaway,
        "run",
        findings[0]["id"],
        "--trace-json",
        "--against",
        "other.jsonl",
    )
    assert proc.returncode == 2
    assert "has no record for" in proc.stderr
