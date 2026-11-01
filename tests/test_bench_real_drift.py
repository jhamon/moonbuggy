"""Unit tests for the bench-real drift check (scripts/bench_real.py, H40)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bench_real


def _gate_row(wall, host="Darwin 24.1.0", ts="2026-10-01T00:00:00+00:00"):
    return {
        "schema": 1,
        "suite": f"real-{bench_real.PROJECT}-{bench_real.TAG}",
        "hypothesis": "baseline",
        "purpose": "bench",
        "harness": "bench_real.py",
        "moonbuggy": "0.2.0",
        "commit": "aaaaaaa",
        "python": "3.12.13",
        "host": host,
        "timestamp": ts,
        "wall_clock": wall,
        "mutants": 381,
        "mutants_per_sec": 381 / wall,
        "memory_delta": 0.0,
        "runs": 1,
    }


def _control_row(wall, host="Darwin 24.1.0", ts="2026-10-01T00:00:00+00:00"):
    row = _gate_row(wall, host=host, ts=ts)
    row["suite"] = f"real-{bench_real.PROJECT}-{bench_real.TAG}-naive-control"
    row["hypothesis"] = "control"
    return row


def _prime(row, control=None):
    """A stored baseline prime built from a row (the drift check's shape)."""
    prime = dict(row)
    if control is not None:
        prime["naive_control"] = control["wall_clock"]
    return prime


# The bench-real archive only ever carries single cold measurements, so the
# gate statistic must be the wall clock itself (H34's legacy-row fallback).


def test_gate_stat_on_a_single_rep_row_is_the_wall_clock():
    import bench_ci

    assert bench_ci.gate_stat(_gate_row(277.8)) == 277.8


# The full path: check_drift() against a temp archive + baseline, exercising
# the H35/H36/H38 rules end-to-end through the real entry point.


def _run_drift(tmp_path, monkeypatch, rows, baseline=None):
    archive = tmp_path / "real-archive.jsonl"
    baseline_path = tmp_path / "real-baseline.json"
    with open(archive, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(_json_line(row))
    if baseline is not None:
        baseline_path.write_text(_json_line(baseline))
    monkeypatch.setattr(bench_real, "REAL_ARCHIVE", str(archive))
    monkeypatch.setattr(bench_real, "REAL_BASELINE", str(baseline_path))
    return bench_real.check_drift()


def _json_line(row):
    import json

    return json.dumps(row) + "\n"


def test_primes_when_no_baseline_exists(tmp_path, monkeypatch):
    row = _gate_row(277.8)
    rc = _run_drift(tmp_path, monkeypatch, [row])
    assert rc == 0
    prime = _load_prime(tmp_path)
    assert prime["wall_clock"] == 277.8
    assert prime["host"] == "Darwin 24.1.0"


def _load_prime(tmp_path):
    import json

    return json.loads((tmp_path / "real-baseline.json").read_text(encoding="utf-8"))


def test_sustained_regression_fails(tmp_path, monkeypatch):
    base = _gate_row(277.8, ts="2026-10-01T00:00:00+00:00")
    prev = _gate_row(550.0, ts="2026-11-01T00:00:00+00:00")
    new = _gate_row(550.0, ts="2026-11-02T00:00:00+00:00")
    prime = _prime(base)
    rc = _run_drift(tmp_path, monkeypatch, [base, prev, new], baseline=prime)
    assert rc == 2


def test_lone_crossing_warns_and_passes(tmp_path, monkeypatch):
    base = _gate_row(277.8, ts="2026-10-01T00:00:00+00:00")
    prev = _gate_row(277.8, ts="2026-11-01T00:00:00+00:00")
    new = _gate_row(550.0, ts="2026-11-02T00:00:00+00:00")
    prime = _prime(base)
    rc = _run_drift(tmp_path, monkeypatch, [base, prev, new], baseline=prime)
    assert rc == 0
    # a lone crossing must not re-prime
    assert _load_prime(tmp_path)["wall_clock"] == 277.8


def test_host_change_re_primes(tmp_path, monkeypatch):
    base = _gate_row(277.8, host="Darwin 24.1.0", ts="2026-10-01T00:00:00+00:00")
    new = _gate_row(550.0, host="Linux 6.8.0", ts="2026-11-02T00:00:00+00:00")
    prime = _prime(base)
    rc = _run_drift(tmp_path, monkeypatch, [base, new], baseline=prime)
    assert rc == 0
    assert _load_prime(tmp_path)["host"] == "Linux 6.8.0"


def test_lone_improvement_withholds_the_ratchet(tmp_path, monkeypatch):
    base = _gate_row(550.0, ts="2026-10-01T00:00:00+00:00")
    prev = _gate_row(550.0, ts="2026-11-01T00:00:00+00:00")
    new = _gate_row(200.0, ts="2026-11-02T00:00:00+00:00")
    prime = _prime(base)
    rc = _run_drift(tmp_path, monkeypatch, [base, prev, new], baseline=prime)
    assert rc == 0
    assert _load_prime(tmp_path)["wall_clock"] == 550.0


def test_sustained_improvement_ratchets(tmp_path, monkeypatch):
    base = _gate_row(550.0, ts="2026-10-01T00:00:00+00:00")
    prev = _gate_row(200.0, ts="2026-11-01T00:00:00+00:00")
    new = _gate_row(200.0, ts="2026-11-02T00:00:00+00:00")
    prime = _prime(base)
    rc = _run_drift(tmp_path, monkeypatch, [base, prev, new], baseline=prime)
    assert rc == 0
    assert _load_prime(tmp_path)["wall_clock"] == 200.0


def test_sustained_contamination_withholds_the_ratchet(tmp_path, monkeypatch):
    # H38/H40: the naive-control leg rides the bench-real archive; two
    # consecutive control readings >10% off the prime mark the ground
    # contaminated, and a fast draw on contaminated ground must not ratchet.
    base = _gate_row(277.8, ts="2026-10-01T00:00:00+00:00")
    base_control = _control_row(300.0, ts="2026-10-01T00:00:00+00:00")
    prev = _gate_row(277.8, ts="2026-11-01T00:00:00+00:00")
    prev_control = _control_row(500.0, ts="2026-11-01T00:00:00+00:00")
    new = _gate_row(100.0, ts="2026-11-02T00:00:00+00:00")
    new_control = _control_row(480.0, ts="2026-11-02T00:00:00+00:00")
    prime = _prime(base, control=base_control)
    rc = _run_drift(
        tmp_path,
        monkeypatch,
        [base, base_control, prev, prev_control, new, new_control],
        baseline=prime,
    )
    assert rc == 0  # contamination passes the gate -- the reading, not the code
    assert _load_prime(tmp_path)["wall_clock"] == 277.8  # ratchet withheld


def test_control_prime_recorded_on_fresh_prime(tmp_path, monkeypatch):
    row = _gate_row(277.8)
    control = _control_row(3100.0)
    rc = _run_drift(tmp_path, monkeypatch, [row, control])
    assert rc == 0
    assert _load_prime(tmp_path)["naive_control"] == 3100.0


def test_empty_archive_is_an_error(tmp_path, monkeypatch):
    archive = tmp_path / "real-archive.jsonl"
    archive.write_text("", encoding="utf-8")
    monkeypatch.setattr(bench_real, "REAL_ARCHIVE", str(archive))
    monkeypatch.setattr(
        bench_real, "REAL_BASELINE", str(tmp_path / "real-baseline.json")
    )
    assert bench_real.check_drift() == 3


def test_emit_numbers_writes_both_rows(monkeypatch, tmp_path):
    out = tmp_path / "pipe.jsonl"
    monkeypatch.setattr(bench_real, "HARNESS_OUTPUT", str(out))
    rows = [
        ("moonbuggy", 277.8, 381, {"KILLED": 121}),
        ("mutmut", 333.6, 1085, {"KILLED": 893}),
        ("naive baseline", 3716.4, 381, {"KILLED": 170}),
    ]
    bench_real.emit_numbers(rows)
    lines = [line for line in out.read_text().splitlines() if line.strip()]
    import json

    parsed = [json.loads(line) for line in lines]
    suites = {(row["suite"], row["hypothesis"]) for row in parsed}
    assert (
        f"real-{bench_real.PROJECT}-{bench_real.TAG}",
        "baseline",
    ) in suites
    assert (
        f"real-{bench_real.PROJECT}-{bench_real.TAG}-naive-control",
        "control",
    ) in suites
    assert len(parsed) == 2


def test_emit_numbers_is_a_noop_without_a_pipe(monkeypatch):
    monkeypatch.setattr(bench_real, "HARNESS_OUTPUT", None)
    # must not raise
    bench_real.emit_numbers([("moonbuggy", 1.0, 1, {}), ("naive baseline", 1.0, 1, {})])


def test_main_parses_check_drift_flag(monkeypatch):
    called = []
    monkeypatch.setattr(bench_real, "check_drift", lambda: called.append(1) or 0)
    assert bench_real.main(["--check-drift"]) == 0
    assert called == [1]


@pytest.mark.parametrize(
    "wall,prev_wall,expect_rc",
    [
        (550.0, 550.0, 2),  # sustained regression
        (550.0, 277.8, 0),  # lone crossing warns
        (277.8, 277.8, 0),  # in band
    ],
)
def test_wall_outcomes(tmp_path, monkeypatch, wall, prev_wall, expect_rc):
    base = _gate_row(277.8, ts="2026-10-01T00:00:00+00:00")
    prev = _gate_row(prev_wall, ts="2026-11-01T00:00:00+00:00")
    new = _gate_row(wall, ts="2026-11-02T00:00:00+00:00")
    rc = _run_drift(tmp_path, monkeypatch, [base, prev, new], baseline=_prime(base))
    assert rc == expect_rc
