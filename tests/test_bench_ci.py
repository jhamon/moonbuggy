"""Unit tests for the bench_ci speed-moat gate (scripts/bench_ci.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bench_ci import WALL_SLACK, gate_stat, latest_gate, verdict


def _speed_row(wall, host="Darwin 24.1.0", min_=None, runs=None):
    row = {
        "suite": "speed",
        "hypothesis": "baseline",
        "wall_clock": wall,
        "mutants": 96,
        "mutants_per_sec": wall / 96,
        "commit": "aaaaaaa",
        "host": host,
    }
    if min_ is not None:
        row["min"] = min_
    if runs is not None:
        row["runs"] = runs
    return row


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


def test_host_change_re_primes_instead_of_gate_failing():
    base = _speed_row(0.4, host="Darwin 24.1.0")
    new = _speed_row(1.0, host="Linux 6.8.0")
    ok, why = verdict(new, base)
    assert ok
    assert "host changed" in why


def test_same_host_still_gates():
    base = _speed_row(0.4, host="Linux 6.8.0")
    new = _speed_row(1.0, host="Linux 6.8.0")
    ok, why = verdict(new, base)
    assert not ok
    assert "REGRESSION" in why


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


def test_gate_stat_uses_min_on_rep_rows():
    # H34: rep-set rows gate on the min, not the noisy single-run wall.
    assert gate_stat(_speed_row(1.52, min_=1.10, runs=3)) == 1.10


def test_gate_stat_falls_back_for_legacy_single_rep_rows():
    assert gate_stat(_speed_row(1.52)) == 1.52
    assert gate_stat(_speed_row(1.52, min_=1.10, runs=1)) == 1.52


def test_min_stat_absorbs_runner_noise_the_wall_slack_cannot():
    # The 10-06 failure, reconstructed: baseline ratcheted to a lucky 1.06s
    # single-rep draw; an ordinary 1.52s draw tripped 1.06 * 1.25 = 1.33.
    lucky = _speed_row(1.06)
    assert not verdict(_speed_row(1.52), lucky)[0]
    # With rep rows the min absorbs the same draw: 1.10 min gates fine.
    lucky_rep = _speed_row(1.06, min_=1.06, runs=3)
    ok, _ = verdict(_speed_row(1.52, min_=1.10, runs=3), lucky_rep)
    assert ok


def test_ratchet_requires_a_genuinely_faster_min():
    # A ratchet-down now compares min-to-min: a lucky median with an ordinary
    # min does not lower the bar.
    base = _speed_row(1.20, min_=1.20, runs=3)
    ok, why = verdict(_speed_row(1.10, min_=1.18, runs=3), base)
    assert ok
    assert why is None  # 1.18 is not < 1.20 * 0.95, so no ratchet


def test_genuinely_slow_candidate_still_fails_on_rep_rows():
    # H34 must not loosen the gate: a real regression shows up in the min
    # (noise only ever makes runs slower, so the min still exposes it) and
    # must trip WALL_SLACK exactly as a single-rep row would.
    base = _speed_row(1.00, min_=1.00, runs=3)
    ok, why = verdict(_speed_row(1.52, min_=1.40, runs=3), base)
    assert not ok
    assert "REGRESSION" in why
    assert "1.40s" in why and "1.00s" in why


def test_rep_row_candidate_gates_against_legacy_single_rep_baseline():
    # The first post-merge nightly: the stored baseline predates the rep set
    # (no runs/min), the candidate row is a rep row. The gate must fall back
    # to wall_clock on the legacy side and still gate.
    base = _speed_row(1.00)
    ok, why = verdict(_speed_row(1.52, min_=1.40, runs=3), base)
    assert not ok
    assert "REGRESSION" in why
    # ...and the noisy-but-honest legacy transition passes: the candidate's
    # min 1.10 vs a possibly-lucky 1.00 baseline draw sits inside the slack.
    ok2, _ = verdict(_speed_row(1.52, min_=1.10, runs=3), _speed_row(1.20))
    assert ok2
