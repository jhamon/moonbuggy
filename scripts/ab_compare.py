"""Criterion M2.3: A/B two git refs, and refuse to call noise a win.

Run: make ab BASELINE=<ref> CANDIDATE=<ref>

The reason this exists is on the record. Phase 1 shipped two performance
changes on the strength of single runs that moved 0.90s to 0.92s, which is
inside the noise of this machine and told nobody anything. A benchmark that
cannot distinguish a real change from scheduler jitter does not merely fail to
help -- it manufactures confidence, and confidence is the thing being spent.

Four choices, each aimed at a specific way of fooling yourself:

- **Interleaved runs.** A then B then A then B, never all of A followed by all
  of B. A laptop that thermally throttles, or a background process that starts
  halfway through, biases a blocked design entirely toward whichever side ran
  second. Interleaving spreads that drift evenly across both.
- **Bootstrap CI on the median, not the mean.** Run times have a long right
  tail -- an occasional run is slow because something else woke up -- and the
  mean chases that tail. The median does not, and bootstrapping needs no
  assumption about the distribution's shape, which is good because it is
  visibly not normal.
- **Overlapping intervals mean "indistinguishable".** Not "probably a small
  win". M2.3.2 makes this the rule, and the self-check in the test suite feeds
  the harness two identical refs to prove it is capable of saying so.
- **Real worktrees.** Each ref is checked out and run from its own tree, so
  what is compared is two versions of the code rather than one version with a
  flag flipped, which is a different and much weaker claim.
"""

import argparse
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = str(REPO / ".venv" / "bin" / "python")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workloads

DEFAULT_RUNS = 7  # M2.3.1's floor, not a target.
BOOTSTRAP_SAMPLES = 10_000
CONFIDENCE = 0.95

# Fixed so a verdict is reproducible from the same measurements. The randomness
# is in the resampling, not in the data, and a verdict that changed between two
# readings of identical numbers would be its own kind of noise.
BOOTSTRAP_SEED = 20260815


def median_confidence_interval(samples, level=CONFIDENCE, draws=BOOTSTRAP_SAMPLES):
    """A percentile bootstrap interval for the median.

    :param samples: measured wall-clock times.
    :param level: e.g. 0.95 for a 95% interval.
    :param draws: bootstrap resamples.
    :returns: ``(low, high)``.
    """
    if len(samples) < 2:
        value = samples[0] if samples else 0.0
        return value, value

    rng = random.Random(BOOTSTRAP_SEED)
    size = len(samples)
    medians = sorted(
        statistics.median(rng.choices(samples, k=size)) for _ in range(draws)
    )
    tail = (1 - level) / 2
    low = medians[int(tail * draws)]
    high = medians[min(int((1 - tail) * draws), draws - 1)]
    return low, high


def overlaps(first, second):
    """Whether two intervals share any point."""
    return not (first[1] < second[0] or second[1] < first[0])


def checkout(ref, destination):
    """Put `ref` in its own worktree at `destination`."""
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(destination), ref],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return destination


def remove_worktree(destination):
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(destination)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def run_once(tree, project):
    """Time one cold moonbuggy run of `tree`'s code against `project`."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(tree) / "src")
    # Every run must start from the same state, or the second one is measuring
    # the cache and the first is measuring the tool.
    shutil.rmtree(Path(project) / ".moonbuggy", ignore_errors=True)

    began = time.perf_counter()
    proc = subprocess.run(
        [PYTHON, "-m", "moonbuggy.cli", "--no-cache", "--quiet", "--timeout", "10"],
        cwd=project,
        capture_output=True,
        text=True,
        env=env,
    )
    elapsed = time.perf_counter() - began
    if proc.returncode not in (0, 1):
        raise SystemExit(f"moonbuggy failed in {tree}:\n{proc.stdout}\n{proc.stderr}")
    return elapsed


def compare(baseline_ref, candidate_ref, shape, runs):
    """Measure both refs on one workload shape. Returns a result dict."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        baseline_tree = checkout(baseline_ref, root / "baseline-tree")
        candidate_tree = checkout(candidate_ref, root / "candidate-tree")
        try:
            baseline_project = workloads.build(root / "baseline-project", shape)
            candidate_project = workloads.build(root / "candidate-project", shape)

            # One discarded run each, so neither side pays for a cold page
            # cache the other side then benefits from.
            run_once(baseline_tree, baseline_project)
            run_once(candidate_tree, candidate_project)

            baseline_times = []
            candidate_times = []
            for _ in range(runs):
                baseline_times.append(run_once(baseline_tree, baseline_project))
                candidate_times.append(run_once(candidate_tree, candidate_project))
        finally:
            remove_worktree(baseline_tree)
            remove_worktree(candidate_tree)

    return {
        "shape": shape,
        "runs": runs,
        "baseline": _describe(baseline_ref, baseline_times),
        "candidate": _describe(candidate_ref, candidate_times),
    }


def _describe(ref, times):
    return {
        "ref": ref,
        "times": times,
        "median": statistics.median(times),
        "min": min(times),
        "interval": median_confidence_interval(times),
    }


def verdict(result):
    """The claim the measurements support, and nothing more.

    :returns: ``(headline, is_significant)``.
    """
    baseline = result["baseline"]
    candidate = result["candidate"]

    if overlaps(baseline["interval"], candidate["interval"]):
        return (
            "indistinguishable -- the 95% intervals overlap, so these "
            "measurements do not support any claim of a difference",
            False,
        )

    ratio = baseline["median"] / candidate["median"]
    if candidate["median"] < baseline["median"]:
        return (f"candidate is faster ({ratio:.2f}x)", True)
    return (f"candidate is SLOWER ({1 / ratio:.2f}x)", True)


def report(result):
    print(f"\n{result['shape']}  --  {workloads.describe(result['shape'])}")
    print(f"  {result['runs']} runs each, interleaved\n")
    print(f"  {'ref':<24} {'median':>9} {'min':>9}   95% interval")
    print("  " + "-" * 66)
    for side in ("baseline", "candidate"):
        entry = result[side]
        low, high = entry["interval"]
        print(
            f"  {entry['ref'][:24]:<24} {entry['median']:>8.3f}s "
            f"{entry['min']:>8.3f}s   [{low:.3f}s, {high:.3f}s]"
        )

    headline, significant = verdict(result)
    print(f"\n  verdict: {headline}")
    return significant


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", required=True, help="git ref to measure against")
    parser.add_argument("--candidate", required=True, help="git ref to measure")
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"runs per side (default and minimum: {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--shape",
        default=None,
        choices=workloads.SHAPES,
        help="one workload shape (default: all three)",
    )
    args = parser.parse_args(argv)

    if args.runs < DEFAULT_RUNS:
        raise SystemExit(
            f"--runs must be at least {DEFAULT_RUNS}: M2.3.1 sets that floor, and "
            "fewer runs make the interval too wide to say anything with."
        )

    shapes = [args.shape] if args.shape else list(workloads.SHAPES)
    print(f"A/B: {args.baseline} (baseline) vs {args.candidate} (candidate)")

    for shape in shapes:
        report(compare(args.baseline, args.candidate, shape, args.runs))

    print(
        "\nA result is only a result on the shape it was measured on. "
        "M2.4.2 asks\nthat an accepted change not regress another shape by "
        "more than 10%, which is\nwhy all three are run unless --shape says "
        "otherwise."
    )


if __name__ == "__main__":
    main()
