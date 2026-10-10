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
    # H35: a regression fails only when sustained across two consecutive
    # nights, so the test supplies an above-wall predecessor night.
    base = _speed_row(0.4)
    prev = _speed_row(1.0)
    new = _speed_row(1.0)
    ok, why = verdict(new, base, [prev])
    assert not ok
    assert "REGRESSION" in why


def test_wall_slack_threshold():
    base = _speed_row(0.4)
    prev = _speed_row(0.4 * WALL_SLACK + 0.001)
    slow = _speed_row(0.4 * WALL_SLACK + 0.001)
    ok_, _ = verdict(slow, base, [prev])
    assert not ok_


def test_improvement_is_reported():
    base = _speed_row(0.9)
    new = _speed_row(0.52)
    prev = _speed_row(0.52)
    ok, why = verdict(new, base, [prev])
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
    prev = _speed_row(1.0, host="Linux 6.8.0")
    new = _speed_row(1.0, host="Linux 6.8.0")
    ok, why = verdict(new, base, [prev])
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
    # H35: a lone crossing is now a warning-pass, not a failure; the same
    # draw sustained across two nights is still a hard fail.
    ok_warn, _ = verdict(_speed_row(1.52), lucky, [])
    assert ok_warn
    assert not verdict(_speed_row(1.52), lucky, [_speed_row(1.52)])[0]
    # With rep rows the min absorbs the same draw: 1.10 min gates fine.
    lucky_rep = _speed_row(1.06, min_=1.06, runs=3)
    ok, _ = verdict(_speed_row(1.52, min_=1.10, runs=3), lucky_rep, [])
    assert ok


def test_ratchet_requires_a_genuinely_faster_min():
    # A ratchet-down compares min-to-min and (H35) needs a sustained shift:
    # a lucky median with an ordinary min does not lower the bar, and even a
    # genuinely-faster min needs the previous night below the bar too.
    base = _speed_row(1.20, min_=1.20, runs=3)
    ok, why = verdict(_speed_row(1.10, min_=1.18, runs=3), base, [])
    assert ok
    # 1.18 is not < 1.20 * 0.95 (1.14), so no ratchet message at all.
    assert why is None


def test_genuinely_slow_candidate_still_fails_on_rep_rows():
    # H34/H35 must not loosen the gate for real regressions: a sustained
    # shift shows up on consecutive nights and trips WALL_SLACK exactly as
    # before. Noise only ever makes runs slower, so the min still exposes it.
    base = _speed_row(1.00, min_=1.00, runs=3)
    prev = _speed_row(1.52, min_=1.40, runs=3)
    ok, why = verdict(_speed_row(1.52, min_=1.40, runs=3), base, [prev])
    assert not ok
    assert "REGRESSION" in why
    assert "1.40s" in why and "1.00s" in why


def test_rep_row_candidate_gates_against_legacy_single_rep_baseline():
    # The first post-merge nightly: the stored baseline predates the rep set
    # (no runs/min), the candidate row is a rep row. The gate must fall back
    # to wall_clock on the legacy side and still gate when the crossing is
    # sustained.
    base = _speed_row(1.00)
    prev = _speed_row(1.52, min_=1.40, runs=3)
    ok, why = verdict(_speed_row(1.52, min_=1.40, runs=3), base, [prev])
    assert not ok
    assert "REGRESSION" in why
    # ...and the noisy-but-honest legacy transition passes: the candidate's
    # min 1.10 vs a possibly-lucky 1.00 baseline draw sits inside the slack.
    ok2, _ = verdict(_speed_row(1.52, min_=1.10, runs=3), _speed_row(1.20), [])
    assert ok2


# --- H35: a single above-wall night is noise; a sustained shift is real ----


def test_single_above_wall_night_warns_but_passes():
    # The 10-09 failure, reconstructed: baseline min 0.8303 armed the wall at
    # ~1.04s; an ordinary azure cold draw of 1.35s min-of-3 tripped it. With
    # no above-wall predecessor night, that is noise, not a regression.
    base = _speed_row(0.8303, min_=0.8303, runs=3, host="Linux azure")
    ok, why = verdict(_speed_row(1.35, min_=1.35, runs=3, host="Linux azure"), base, [])
    assert ok
    assert "WARNING" in why


def test_sustained_two_night_crossing_fails():
    base = _speed_row(0.8303, min_=0.8303, runs=3, host="Linux azure")
    prev = _speed_row(1.35, min_=1.35, runs=3, host="Linux azure")
    ok, why = verdict(
        _speed_row(1.36, min_=1.36, runs=3, host="Linux azure"), base, [prev]
    )
    assert not ok
    assert "REGRESSION" in why
    assert "sustained" in why


def test_warning_night_never_fails_alone_even_without_history():
    base = _speed_row(1.00, min_=1.00, runs=3)
    ok, _ = verdict(_speed_row(2.00, min_=1.40, runs=3), base, ())
    assert ok


def test_previous_night_below_wall_breaks_the_streak():
    base = _speed_row(1.00, min_=1.00, runs=3)
    # prev night fine, tonight over the wall -> not sustained.
    ok, why = verdict(_speed_row(1.52, min_=1.40, runs=3), base, [_speed_row(1.0)])
    assert ok
    assert "WARNING" in why


def test_alien_host_history_rows_are_ignored_for_the_streak():
    # A host change re-primes; alien-host rows must not count as a
    # "previous above-wall night" for tonight's streak.
    base = _speed_row(1.00, min_=1.00, runs=3, host="Linux azure")
    prev = _speed_row(1.40, min_=1.40, runs=3, host="Linux old-azure")
    ok, why = verdict(
        _speed_row(1.52, min_=1.40, runs=3, host="Linux azure"), base, [prev]
    )
    assert ok
    assert "WARNING" in why


# --- H35: the ratchet-down needs a sustained improvement too ---------------


def test_ratchet_needs_two_consecutive_improve_nights():
    base = _speed_row(1.20, min_=1.20, runs=3)
    # Tonight below the bar but the previous night was not: no ratchet.
    ok, why = verdict(
        _speed_row(1.10, min_=1.10, runs=3), base, [_speed_row(1.19, min_=1.19, runs=3)]
    )
    assert ok
    assert "ratchet needs a sustained shift" in why
    # Two consecutive nights below the bar: ratchet.
    ok2, why2 = verdict(
        _speed_row(1.10, min_=1.10, runs=3), base, [_speed_row(1.10, min_=1.10, runs=3)]
    )
    assert ok2
    assert "improved" in why2


def test_lucky_min_can_no_longer_arm_a_trap_wall():
    # The H35 root cause, end to end: a lucky 0.83 min ratcheted the bar to
    # 0.83*0.95... no -- the trap was the ratchet landing 0.8303 at all from
    # one night, then ordinary 1.35 minima tripping 0.8303*1.25=1.04. Under
    # H35 the single lucky night cannot lower the bar, and the ordinary
    # night that follows passes with a warning instead of failing CI.
    base = _speed_row(1.06, min_=1.06, runs=3)
    lucky = _speed_row(0.83, min_=0.8303, runs=3)
    ok, why = verdict(lucky, base, [])
    assert ok
    assert "ratchet needs a sustained shift" in why
    # Next night, ordinary draw, bar still 1.06*1.25 = 1.33... 1.35 > 1.33:
    # with the (not-ratcheted) 1.06 baseline, prev night 0.83 is BELOW the
    # wall, so the streak is broken: warning, not failure.
    ok2, why2 = verdict(_speed_row(1.35, min_=1.35, runs=3), base, [lucky])
    assert ok2
    assert "WARNING" in why2
