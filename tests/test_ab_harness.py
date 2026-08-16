"""Criterion M2.3.3 -- the A/B harness is not manufacturing wins.

A measurement tool that always finds a difference is worse than no measurement
tool, because its output looks like evidence. The check that matters is the
negative one: fed two things that are the same, does it say so?

Two levels of that check here. The statistics are tested directly on synthetic
samples, which is fast and can cover the cases real runs rarely produce. Then
one end-to-end case actually checks out the same commit twice and runs both --
slow, and the only version of the check that exercises the whole harness rather
than the arithmetic inside it.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ab_compare import (
    DEFAULT_RUNS,
    main,
    median_confidence_interval,
    overlaps,
    verdict,
)


def _result(baseline_times, candidate_times):
    from ab_compare import _describe

    return {
        "shape": "slow-tests",
        "runs": len(baseline_times),
        "baseline": _describe("base", baseline_times),
        "candidate": _describe("cand", candidate_times),
    }


def test_identical_samples_are_indistinguishable():
    times = [0.90, 0.91, 0.89, 0.92, 0.90, 0.91, 0.90]

    headline, significant = verdict(_result(times, list(times)))

    assert not significant
    assert "indistinguishable" in headline


def test_the_noise_episode_from_phase_one_is_not_a_win():
    """The 0.90-vs-0.92 change that shipped on a single run.

    Given the spread these numbers actually have, the harness must decline to
    call it. This is the specific mistake M2.3 exists to prevent, so it is a
    test rather than a paragraph.
    """
    baseline = [0.90, 0.93, 0.89, 0.95, 0.88, 0.91, 0.94]
    candidate = [0.92, 0.90, 0.94, 0.89, 0.93, 0.91, 0.90]

    headline, significant = verdict(_result(baseline, candidate))

    assert not significant, headline


def test_a_large_real_difference_is_reported_as_one():
    """The harness must still have teeth. A test suite containing only the
    negative check would pass with `verdict` hard-coded to return no."""
    baseline = [1.00, 1.02, 0.98, 1.01, 0.99, 1.03, 1.00]
    candidate = [0.50, 0.51, 0.49, 0.52, 0.50, 0.48, 0.51]

    headline, significant = verdict(_result(baseline, candidate))

    assert significant
    assert "faster" in headline
    assert "2.0" in headline


def test_a_regression_is_named_a_regression_not_a_win():
    baseline = [0.50, 0.51, 0.49, 0.52, 0.50, 0.48, 0.51]
    candidate = [1.00, 1.02, 0.98, 1.01, 0.99, 1.03, 1.00]

    headline, significant = verdict(_result(baseline, candidate))

    assert significant
    assert "SLOWER" in headline


def test_the_interval_brackets_the_median():
    times = [0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10]

    low, high = median_confidence_interval(times)

    assert low <= 0.95 <= high


def test_a_wider_spread_gives_a_wider_interval():
    """Otherwise the interval is decoration rather than a measure of doubt."""
    tight = median_confidence_interval([1.00, 1.01, 0.99, 1.00, 1.01, 0.99, 1.00])
    loose = median_confidence_interval([0.50, 1.50, 0.70, 1.30, 0.60, 1.40, 1.00])

    assert (loose[1] - loose[0]) > (tight[1] - tight[0])


def test_overlap_is_inclusive_at_the_boundary():
    """Touching intervals are not separated intervals. Treating them as such
    would make the significance rule a rounding artefact."""
    assert overlaps((1.0, 2.0), (2.0, 3.0))
    assert not overlaps((1.0, 2.0), (2.001, 3.0))


def test_too_few_runs_is_refused_rather_than_averaged():
    with pytest.raises(SystemExit) as raised:
        main(["--baseline", "HEAD", "--candidate", "HEAD", "--runs", "3"])

    assert "at least" in str(raised.value)
    assert str(DEFAULT_RUNS) in str(raised.value)


@pytest.mark.slow
def test_the_same_commit_compared_with_itself_is_indistinguishable():
    """M2.3.3 end to end: two worktrees of one commit, really run.

    This is the check that the harness as a whole -- worktrees, interleaving,
    the workload build, the timing -- does not systematically favour one side.
    A bug that gave the second-run side a warm page cache would show up here
    and nowhere else in this file.
    """
    repo = Path(__file__).resolve().parents[1]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ab_compare.py",
            "--baseline",
            head,
            "--candidate",
            head,
            "--shape",
            "fast-tests",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=900,
    )

    assert proc.returncode == 0, proc.stderr
    assert "indistinguishable" in proc.stdout, proc.stdout
