"""M1.1.6's covered-mutant gate (F-4): the count must be derivable from a real run.

The gate answers "did this run reach a verdict on every mutant the CLEAN run
had coverage for?". The clean run's answer lives in its records, not in a
summary key the runner never writes -- F-4 found the rig reading a
non-existent ``covered`` key and silently comparing against 0, making the
gate vacuously satisfiable on every real run.

These tests import the rig as the check-in does: by path, because
``scripts/`` is not a package. Each test pins one derivation the gate
depends on, against a summary.json in the shape the runner actually writes
(and older shapes an old results directory might hold).
"""

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RIG_PATH = REPO / "scripts" / "selfmutation_rig.py"

# The rig imports pytest lazily (it must run without pytest installed), so the
# import itself does not fail here -- but these tests only exercise the gate,
# and the run they model is a pytest run's.

_spec = importlib.util.spec_from_file_location("selfmutation_rig_f4", RIG_PATH)
assert _spec is not None and _spec.loader is not None
rig = importlib.util.module_from_spec(_spec)
sys.modules["selfmutation_rig_f4"] = rig
_spec.loader.exec_module(rig)


# -- helpers ----------------------------------------------------------------


def write_run(tmp_path, counts, schema=1):
    """A run directory with a summary.json in the shape the runner writes.

    ``counts`` is the lower-cased ``counts`` object every real summary
    carries; older/other shapes are built by the tests that need them.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    summary = {
        "schema": schema,
        "total": sum(counts.values()),
        "counts": counts,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary) + "\n")
    return run_dir


def records(run_dir, statuses):
    """Write results.jsonl with the given per-record statuses (one per line)."""
    path = run_dir / "results.jsonl"
    path.write_text(
        "\n".join(json.dumps({"status": s}) for s in statuses) + "\n",
        encoding="utf-8",
    )
    return path


# -- the gate itself --------------------------------------------------------


def test_the_gate_counts_the_clean_runs_verdicts(tmp_path):
    """Covered = verdicts on the clean run's own records, minus NO_COVERAGE.

    Every record IS a verdict; the ones that say nothing about whether the
    mutation was noticed are NO_COVERAGE. This is the number M1.1.6 compares
    against -- 281 covered of 1702 in the round-2 snapshot, 3 of 54 in the
    current scoped receipt -- and it comes from `counts`, not from a field
    that does not exist.
    """
    run_dir = write_run(
        tmp_path,
        {
            "killed": 122,
            "killed_by_error": 57,
            "survived": 91,
            "no_coverage": 11,
            "timeout": 11,
            "suspicious": 0,
            "skipped": 0,
        },
    )
    covered = rig.covered_mutant_count(run_dir)
    assert covered == 122 + 57 + 91 + 11  # everything except no_coverage


def test_the_gate_fails_a_run_that_dropped_covered_mutants(tmp_path):
    """The F-3 scenario, on the shapes the runner actually writes."""
    # Clean: 3 covered. This run: 2 records -- a covered mutant vanished.
    run_dir = write_run(tmp_path, {"killed": 2, "survived": 1})
    records(run_dir, ["KILLED", "SURVIVED"])
    ok = rig.gate_check(run_dir, run_dir)
    assert ok is False, "a run that dropped a covered mutant must fail M1.1.6"


def test_the_gate_passes_when_every_covered_mutant_got_a_verdict(tmp_path):
    run_dir = write_run(tmp_path, {"killed": 2, "survived": 1})
    records(run_dir, ["KILLED", "KILLED", "SURVIVED"])
    assert rig.gate_check(run_dir, run_dir) is True


def test_the_gate_fails_a_zero_record_run(tmp_path):
    """Receipt guard: never pass over nothing."""
    run_dir = write_run(tmp_path, {"killed": 2})
    assert rig.gate_check(run_dir, run_dir) is False


def test_an_old_summary_with_no_counts_key_still_derives_zero(tmp_path):
    """A summary from before the counts key existed derives 0: satisfiable
    gate, as documented. Not a silent pass -- the tests above pin the real
    shapes; this one just keeps the rig working on a directory it may meet."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(json.dumps({"schema": 1, "total": 5}) + "\n")
    assert rig.covered_mutant_count(run_dir) == 0
    assert rig.covered_mutant_count(tmp_path / "absent") == 0


def test_the_gate_never_lets_a_vacuous_zero_look_like_success(tmp_path):
    """The F-4 regression, end to end: the runner writes no 'covered' key.
    The old code read data.get("covered", 0) and the gate compared records
    against 0, passing everything. With derivation the clean counts above
    ARE the comparison, and a shortfall fails."""
    # The clean summary a real runner writes (no 'covered' key anywhere):
    clean = write_run(
        tmp_path,
        {
            "killed": 4,
            "survived": 0,
            "no_coverage": 1,
            "timeout": 0,
            "killed_by_error": 0,
            "suspicious": 0,
            "skipped": 0,
        },
    )
    assert "covered" not in json.loads((clean / "summary.json").read_text())
    # The run it must be checked against: 3 records where 4 were covered.
    records(clean, ["KILLED", "KILLED", "SURVIVED"])
    assert rig.gate_check(clean, clean) is False
