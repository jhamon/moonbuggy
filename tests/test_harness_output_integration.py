"""End-to-end check: the bench harness's emit gate writes conforming rows.

Runs the real emit_numbers() from bench_mutation.py against synthetic rows to
prove the wiring (version import, build, validate, write) works without paying
for a full three-tool comparison.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import bench_mutation
import harness_output


def test_emit_gate_writes_only_moonbuggy_rows(tmp_path):
    bench_mutation.HARNESS_OUTPUT = str(tmp_path / "out.jsonl")
    fixture_rows = [("moonbuggy", 8.46, 22, {}), ("mutmut", 15.61, 26, {})]
    speed_rows = [("moonbuggy", 0.50, 84, {}), ("naive baseline", 18.9, 84, {})]

    bench_mutation.emit_numbers(speed_rows, fixture_rows)

    rows = harness_output.read_jsonl(tmp_path / "out.jsonl")
    # Only moonbuggy's two measurements; mutmut and naive are comparators.
    assert len(rows) == 2
    for row in rows:
        assert harness_output.validate(row) == []
        assert row["purpose"] == "bench"
        assert row["hypothesis"] == "baseline"

    suites = {row["suite"] for row in rows}
    assert suites == {"fixture", "speed"}


def test_emit_gate_is_inert_when_not_requested(tmp_path):
    bench_mutation.HARNESS_OUTPUT = None
    out = tmp_path / "out.jsonl"
    # Must not create the file.
    bench_mutation.emit_numbers([], [])
    assert not out.exists()


def test_emitted_moonbuggy_row_is_self_consistent():
    # The throughput field must equal mutants / wall_clock for a debuggable row.
    row = json.loads(
        json.dumps(
            harness_output.build(
                suite="slow-tests",
                purpose="ab",
                harness="ab_compare.py",
                wall_clock=0.940,
                mutants=84,
                hypothesis="H21",
                moonbuggy="0.2.0",
                runs=7,
            )
        )
    )
    assert row["mutants_per_sec"] == round(84 / 0.940, 4)
