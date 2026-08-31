"""Unit tests for the bench_ci speed-moat gate (scripts/bench_ci.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bench_ci import WALL_SLACK, latest_gate, verdict


def _speed_row(wall):
    return {
        "suite": "speed",
        "hypothesis": "baseline",
        "wall_clock": wall,
        "mutants": 96,
        "mutants_per_sec": wall / 96,
        "commit": "aaaaaaa",
    }


def test_priming_without_a_baseline_is_a_pass():
    ok, why = verdict(_speed_row(0.52), None)
    assert ok
    assert "priming" in why


def test_regression_is_rejected():
    base = _speed_row(0.4)
    new = _speed_row(1.0)
    ok, why = verdict(new, base)
    assert not ok
    assert "REGRESSION" in why


def test_wall_slack_threshold():
    base = _speed_row(0.4)
    slow = _speed_row(0.4 * WALL_SLACK + 0.001)
    ok_, _ = verdict(slow, base)
    assert not ok_


def test_improvement_is_reported():
    base = _speed_row(0.9)
    new = _speed_row(0.52)
    ok, why = verdict(new, base)
    assert ok
    assert "improved" in why


def test_latest_gate_picks_the_newest_speed_row():
    rows = [
        {"suite": "fixture", "hypothesis": "baseline", "wall_clock": 8.0},
        {"suite": "speed", "hypothesis": "baseline", "wall_clock": 0.9, "commit": "a"},
        {"suite": "speed", "hypothesis": "baseline", "wall_clock": 0.5, "commit": "b"},
    ]
    assert latest_gate(rows)["commit"] == "b"


def test_unreadable_baseline_raises(tmp_path):
    bad = tmp_path / "base.json"
    bad.write_text("not json")
    from bench_ci import load_base

    with pytest.raises(ValueError):
        load_base(str(bad))
