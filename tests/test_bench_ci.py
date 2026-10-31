"""Unit tests for the bench_ci speed-moat gate (scripts/bench_ci.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bench_ci import (
    CONTAMINATION,
    WALL_SLACK,
    contamination_note,
    gate_stat,
    latest_control,
    latest_gate,
    sustained_contamination,
    verdict,
)


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
    # draw sustained across two nights is still a hard fail. H36 (slack 1.8)
    # widens the wall further: 1.52 < 1.06 * 1.8 = 1.91, so the ordinary
    # draw now passes clean; a sustained *real* regression needs a bigger
    # shift (see test_sustained_regression_still_fails_past_h36_wall).
    ok_warn, why = verdict(_speed_row(1.52), lucky, [])
    assert ok_warn
    assert why is None
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
    # H34/H35/H36 must not loosen the gate for real regressions: a sustained
    # shift shows up on consecutive nights and trips WALL_SLACK exactly as
    # before. Noise only ever makes runs slower, so the min still exposes it.
    # Under H36 the wall is 1.8x, so the shift must exceed it to trip.
    base = _speed_row(1.00, min_=1.00, runs=3)
    prev = _speed_row(1.95, min_=1.95, runs=3)
    ok, why = verdict(_speed_row(1.95, min_=1.95, runs=3), base, [prev])
    assert not ok
    assert "REGRESSION" in why
    assert "1.95s" in why and "1.00s" in why


def test_rep_row_candidate_gates_against_legacy_single_rep_baseline():
    # The first post-merge nightly: the stored baseline predates the rep set
    # (no runs/min), the candidate row is a rep row. The gate must fall back
    # to wall_clock on the legacy side and still gate when the crossing is
    # sustained. Under H36 the wall is 1.8x, so the sustained shift must
    # exceed it.
    base = _speed_row(1.00)
    prev = _speed_row(1.95, min_=1.95, runs=3)
    ok, why = verdict(_speed_row(1.95, min_=1.95, runs=3), base, [prev])
    assert not ok
    assert "REGRESSION" in why
    # ...and the noisy-but-honest legacy transition passes: the candidate's
    # min 1.10 vs a possibly-lucky 1.00 baseline draw sits inside the slack.
    ok2, _ = verdict(_speed_row(1.52, min_=1.10, runs=3), _speed_row(1.20), [])
    assert ok2


# --- H35: a single above-wall night is noise; a sustained shift is real ----


def test_single_above_wall_night_warns_but_passes():
    # The 10-09 failure, reconstructed: baseline min 0.8303 armed the wall at
    # ~1.04s; an ordinary azure cold draw of 1.35s min-of-3 tripped it. Under
    # H35 that lone crossing warned; under H36 (slack 1.8, wall 0.8303 * 1.8
    # = 1.49) the ordinary draw is inside the wall and passes clean -- the
    # exact trap H35 left armed is now defused.
    base = _speed_row(0.8303, min_=0.8303, runs=3, host="Linux azure")
    ok, why = verdict(_speed_row(1.35, min_=1.35, runs=3, host="Linux azure"), base, [])
    assert ok
    assert why is None
    # A draw above the H36 wall still warns on a lone night.
    slow = _speed_row(1.60, min_=1.60, runs=3, host="Linux azure")
    ok2, why2 = verdict(slow, base, [])
    assert ok2
    assert "WARNING" in why2


def test_sustained_two_night_crossing_fails():
    base = _speed_row(0.8303, min_=0.8303, runs=3, host="Linux azure")
    prev = _speed_row(1.60, min_=1.60, runs=3, host="Linux azure")
    ok, why = verdict(
        _speed_row(1.61, min_=1.61, runs=3, host="Linux azure"), base, [prev]
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
    # prev night fine, tonight over the wall (1.00 * 1.8 = 1.80) -> warning.
    ok, why = verdict(_speed_row(1.95, min_=1.90, runs=3), base, [_speed_row(1.0)])
    assert ok
    assert "WARNING" in why


def test_alien_host_history_rows_are_ignored_for_the_streak():
    # A host change re-primes; alien-host rows must not count as a
    # "previous above-wall night" for tonight's streak.
    base = _speed_row(1.00, min_=1.00, runs=3, host="Linux azure")
    prev = _speed_row(1.90, min_=1.90, runs=3, host="Linux old-azure")
    ok, why = verdict(
        _speed_row(1.95, min_=1.90, runs=3, host="Linux azure"), base, [prev]
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
    # Under H36 the wall is 0.8303 * 1.8 = 1.49, so the ordinary 1.35 draw
    # passes clean and even the next ordinary draw cannot arm a streak.
    base = _speed_row(1.06, min_=1.06, runs=3)
    lucky = _speed_row(0.83, min_=0.8303, runs=3)
    ok, why = verdict(lucky, base, [])
    assert ok
    assert "ratchet needs a sustained shift" in why
    # Next night, ordinary draw 1.35 < 1.49 wall: clean pass.
    ok2, why2 = verdict(_speed_row(1.35, min_=1.35, runs=3), base, [lucky])
    assert ok2
    assert why2 is None


# --- H36: WALL_SLACK calibrated to the observed cross-night spread ----------


def test_h36_wall_covers_the_observed_ordinary_draw_band():
    # The H35 falsification numbers: ordinary azure min-of-3 band 1.34-1.47.
    # Any ordinary baseline prime (lucky min 0.8303 or ordinary 1.47) must
    # arm a wall at or above the top of the ordinary band, so ordinary
    # nights pass with no warning.
    lucky = _speed_row(0.8303, min_=0.8303, runs=3, host="Linux 26")
    ordinary = _speed_row(1.4461, min_=1.4461, runs=3, host="Linux 26")
    for draw in (1.34, 1.47):
        for base in (lucky, ordinary):
            ok, why = verdict(
                _speed_row(draw, min_=draw, runs=3, host="Linux 26"), base, []
            )
            assert ok, why
            # A draw below the improve bar carries the ratchet note; only
            # wall crossings are absent. Just assert not a WARNING/FAIL.
            assert why is None or "ratchet needs a sustained shift" in why


def test_h36_sustained_regression_still_fails():
    # A real regression (>1.8x baseline, two consecutive nights) still trips
    # the gate -- the sustained rule is untouched.
    base = _speed_row(1.4461, min_=1.4461, runs=3, host="Linux 26")
    prev = _speed_row(2.70, min_=2.70, runs=3, host="Linux 26")
    regressed = _speed_row(2.70, min_=2.70, runs=3, host="Linux 26")
    ok, why = verdict(regressed, base, [prev])
    assert not ok
    assert "REGRESSION" in why
    # And a sub-1.8x shift does NOT fail, even sustained -- that is the cost
    # H36 accepts (a real 1.5x regression is caught by G2/A-B, not the wall).
    base2 = _speed_row(1.4461, min_=1.4461, runs=3, host="Linux 26")
    prev2 = _speed_row(2.10, min_=2.10, runs=3, host="Linux 26")
    mid = _speed_row(2.10, min_=2.10, runs=3, host="Linux 26")
    ok2, _ = verdict(mid, base2, [prev2])
    assert ok2


def test_h36_ratchet_still_needs_two_consecutive_improve_nights():
    # The ratchet-down is unchanged: two consecutive nights below
    # base * IMPROVE (0.95), min-to-min. A lucky single night cannot lower
    # the bar (the H35 root cause stays closed).
    base = _speed_row(1.40, min_=1.40, runs=3)
    ok, why = verdict(
        _speed_row(1.30, min_=1.30, runs=3), base, [_speed_row(1.39, min_=1.39, runs=3)]
    )
    assert ok
    assert "ratchet needs a sustained shift" in why
    ok2, why2 = verdict(
        _speed_row(1.30, min_=1.30, runs=3), base, [_speed_row(1.30, min_=1.30, runs=3)]
    )
    assert ok2
    assert "improved" in why2


# --- H38: the naive control's contamination call needs two nights ----------


def _control_row(wall, host="Linux azure", timestamp="2026-10-25T08:00:00+00:00"):
    return {
        "suite": "naive-control",
        "hypothesis": "control",
        "wall_clock": wall,
        "mutants": 96,
        "mutants_per_sec": wall / 96,
        "commit": "aaaaaaa",
        "host": host,
        "timestamp": timestamp,
    }


def test_lone_control_shift_is_a_note_not_a_fail():
    # The 10-24 replay: prime 52.64s, tonight's control 33.36s (36% off),
    # no prior control night agrees -> NOT contaminated, just a lone-night
    # note; the gate passes and the ratchet logic is the caller's to gate.
    tonight = _control_row(33.36)
    ok, why = verdict(
        _speed_row(1.35, min_=1.35, runs=3, host="Linux azure"),
        _speed_row(1.40, min_=1.40, runs=3, host="Linux azure"),
        [],
        control=tonight,
        control_history=[],
    )
    # No naive_control prime on this baseline -> silence (legacy archive).
    assert ok and why is None


def test_lone_control_shift_warns_with_a_prime():
    base = dict(
        _speed_row(1.40, min_=1.40, runs=3, host="Linux azure"), naive_control=52.64
    )
    ok, why = verdict(
        _speed_row(1.35, min_=1.35, runs=3, host="Linux azure"),
        base,
        [],
        control=_control_row(33.36),
        control_history=[],
    )
    assert ok
    assert why is not None and "NOTE" in why
    assert "sustained shift (H38)" in why


def test_sustained_control_shift_is_flagged_contaminated():
    # Two consecutive nights >10% off the prime: the contamination call
    # fires. The gate still passes (a shifted environment invalidates the
    # reading, not the code) but the note is the CONTAMINATED verdict.
    base = dict(
        _speed_row(1.40, min_=1.40, runs=3, host="Linux azure"), naive_control=52.64
    )
    prev = _control_row(33.0, timestamp="2026-10-24T08:00:00+00:00")
    ok, why = verdict(
        _speed_row(1.35, min_=1.35, runs=3, host="Linux azure"),
        base,
        [],
        control=_control_row(33.36),
        control_history=[prev],
    )
    assert ok
    assert "CONTAMINATED" in why
    assert "sustained" in why


def test_control_back_within_band_breaks_the_streak():
    base = dict(
        _speed_row(1.40, min_=1.40, runs=3, host="Linux azure"), naive_control=52.64
    )
    prev = _control_row(33.0, timestamp="2026-10-24T08:00:00+00:00")
    ok, why = verdict(
        _speed_row(1.35, min_=1.35, runs=3, host="Linux azure"),
        base,
        [],
        control=_control_row(52.0),
        control_history=[prev],
    )
    assert ok and why is None


def test_alien_host_control_rows_do_not_sustain_the_streak():
    base = dict(
        _speed_row(1.40, min_=1.40, runs=3, host="Linux azure"), naive_control=52.64
    )
    prev = _control_row(33.0, host="Linux old-azure")
    ok, why = verdict(
        _speed_row(1.35, min_=1.35, runs=3, host="Linux azure"),
        base,
        [],
        control=_control_row(33.36),
        control_history=[prev],
    )
    assert ok
    assert why is not None and "NOTE" in why and "CONTAMINATED" not in why


def test_control_within_band_is_silent():
    base = dict(
        _speed_row(1.40, min_=1.40, runs=3, host="Linux azure"), naive_control=52.64
    )
    ok, why = verdict(
        _speed_row(1.35, min_=1.35, runs=3, host="Linux azure"),
        base,
        [],
        control=_control_row(54.0),
        control_history=[],
    )
    assert ok and why is None


def test_sustained_contamination_helper_scopes():
    prime = 52.64
    # No prime: never contaminated (pre-H38 baseline).
    assert sustained_contamination(33.36, None, []) == (False, None)
    # Lone night: not sustained.
    assert sustained_contamination(33.36, prime, [], host="Linux azure") == (
        False,
        None,
    )
    # Previous night agrees: sustained.
    prev = _control_row(33.0)
    sustained, prev_val = sustained_contamination(
        33.36, prime, [prev], host="Linux azure"
    )
    assert sustained and prev_val == 33.0
    # Within the band: not a shift at all.
    assert sustained_contamination(54.0, prime, [prev]) == (False, None)
    # Alien-host previous night: not sustained.
    alien = _control_row(33.0, host="Linux old-azure")
    assert sustained_contamination(33.36, prime, [alien], host="Linux azure") == (
        False,
        None,
    )


def test_contamination_threshold_is_ten_percent():
    assert CONTAMINATION == 0.10
    prime = 52.64
    # A 9% shift is inside the band; a 10%+ shift (exclusive) is a crossing.
    assert abs(prime * 1.09 - prime) <= prime * CONTAMINATION
    assert abs(prime * 1.10 - prime) > prime * CONTAMINATION - 1e-9


def test_contamination_note_classifies():
    base = _speed_row(1.40)
    prime = 52.64
    quiet = _control_row(54.0)
    lone = _control_row(33.36)
    prev = _control_row(33.0)
    assert contamination_note(_speed_row(1.0), base, quiet, prime, []) is None
    assert "NOTE" in contamination_note(_speed_row(1.0), base, lone, prime, [])
    note = contamination_note(_speed_row(1.0), base, lone, prime, [prev])
    assert note.startswith("CONTAMINATED")
    # Legacy archive: no control row -> silence.
    assert contamination_note(_speed_row(1.0), base, None, prime, []) is None
    # Pre-H38 baseline: no prime -> silence.
    assert contamination_note(_speed_row(1.0), base, lone, None, [prev]) is None


def test_latest_control_picks_the_newest_control_row():
    rows = [
        {"suite": "speed", "hypothesis": "baseline", "wall_clock": 1.0},
        {"suite": "naive-control", "hypothesis": "control", "wall_clock": 51.0},
        {"suite": "naive-control", "hypothesis": "control", "wall_clock": 33.36},
    ]
    assert latest_control(rows)["wall_clock"] == 33.36
    assert latest_control([{"suite": "speed", "hypothesis": "baseline"}]) is None


def test_h37_ten_twentyfour_full_replay_lone_shift_does_not_ratchet():
    # The exact H37-round scenario, end to end: baseline 1.40 prime,
    # control prime 52.64s, tonight draws min 0.86 (below the improve bar)
    # against a lone 33.36s control reading. Under H36's single-night rule
    # this draw was "contaminated"; under H38 it is one noisy control night,
    # so the draw is ordinary noise on the control and the ratchet decision
    # falls to the sustained_improvement rule (which alone withholds it).
    base = dict(
        _speed_row(1.40, min_=1.40, runs=3, host="Linux azure"), naive_control=52.64
    )
    ok, why = verdict(
        _speed_row(0.8868, min_=0.8599, runs=3, host="Linux azure"),
        base,
        [],
        control=_control_row(33.36),
        control_history=[],
    )
    assert ok
    # The draw is below the improve bar, so the ratchet message wins -- the
    # point of the replay is that the control's lone shift does NOT upgrade
    # it to CONTAMINATED under H38 (under H36's single-night rule it did).
    assert why is not None
    assert "CONTAMINATED" not in why
    assert "ratchet needs a sustained shift" in why


def test_ratchet_is_not_withheld_when_control_is_quiet():
    # The improve path still works untouched when the control is in band.
    base = dict(
        _speed_row(1.40, min_=1.40, runs=3, host="Linux azure"), naive_control=52.64
    )
    prev_gate = _speed_row(1.30, min_=1.30, runs=3, host="Linux azure")
    ok, why = verdict(
        _speed_row(1.30, min_=1.30, runs=3, host="Linux azure"),
        base,
        [prev_gate],
        control=_control_row(52.0),
        control_history=[],
    )
    assert ok
    assert "improved" in why


def test_sustained_improvement_ratchets_stored_wall_clock(tmp_path, monkeypatch):
    """A sustained improvement writes tonight's row as the new baseline prime.

    The nightly ratchet previously reloaded the stored row and refreshed only
    its ``naive_control`` field, leaving ``wall_clock`` fixed until the next
    host-change re-prime. This test pins the intended ratchet behavior.
    """
    import json

    import bench_ci

    archive = tmp_path / "archive.jsonl"
    baseline_path = tmp_path / "baseline.json"
    monkeypatch.setattr(bench_ci, "ARCHIVE", str(archive))
    monkeypatch.setattr(bench_ci, "BASELINE_PATH", str(baseline_path))
    monkeypatch.setattr(bench_ci, "ROW_FILE", str(tmp_path / "row.md"))

    def row(wall, ts, min_=None):
        data = {
            "schema": 1,
            "suite": "speed",
            "hypothesis": "baseline",
            "purpose": "bench",
            "harness": "bench_mutation.py",
            "moonbuggy": "0.2.0",
            "commit": "aaaaaaa",
            "python": "3.12.13",
            "host": "Darwin 24.1.0",
            "timestamp": ts,
            "wall_clock": wall,
            "runs": 1,
            "mutants": 96,
            "mutants_per_sec": wall / 96,
            "memory_delta": 0.0,
        }
        if min_ is not None:
            data["min"] = min_
        return data

    base = row(100.0, "2026-10-01T00:00:00+00:00")
    prev = row(50.0, "2026-11-01T00:00:00+00:00", min_=50.0)
    new = row(50.0, "2026-11-02T00:00:00+00:00", min_=50.0)
    control = {
        **row(500.0, "2026-11-02T00:00:00+00:00"),
        "suite": "naive-control",
        "hypothesis": "control",
    }
    with open(archive, "w", encoding="utf-8") as fh:
        for data in (base, prev, new, control):
            fh.write(json.dumps(data) + "\n")
    baseline_path.write_text(
        json.dumps({**base, "naive_control": 500.0}) + "\n", encoding="utf-8"
    )

    rc = bench_ci.main(["--skip-bench"])
    assert rc == 0

    prime = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert prime["wall_clock"] == 50.0
    assert prime["min"] == 50.0
    assert prime["timestamp"] == "2026-11-02T00:00:00+00:00"
    assert prime["naive_control"] == 500.0
