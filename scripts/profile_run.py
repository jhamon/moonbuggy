"""Criterion M2.1: a wall-clock breakdown of a run, by named phase.

Run: .venv/bin/python scripts/profile_run.py   (or `make profile`)

What this exists to prevent: the Phase 1 benchmark record contains two changes
implemented on a hunch and measured as noise, with a note that the profile
which would have prevented it was cheap. So the rule for M2 is that no
optimisation is attempted before its phase has been measured, and this is the
measurement.

Three things it is careful about:

- **Unattributed time is printed, not absorbed.** M2.1.2 gates on the named
  phases covering 95% of wall clock. An "other" bucket holding 30% looks like
  a rounding error and is actually the answer.
- **Interpreter startup is counted.** The in-process profiler's clock starts
  when moonbuggy is imported, so it cannot see the interpreter start or the
  import itself. The harness measures the subprocess from the outside and
  reports the difference, rather than letting a fixed ~100ms disappear.
- **Profiling overhead is measured, not assumed.** M2.1.4 wants the profiled
  run within 20% of an unprofiled one, or the discrepancy explained. Both are
  run here and the ratio is printed whether or not it is flattering.
"""

import json
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

# Each shape is run this many times and the median taken. One run of a
# sub-second workload is mostly scheduler noise, and a profile built from
# noise sends the optimisation effort somewhere arbitrary.
REPEATS = 5

# M2.1.2 and M2.1.4, as numbers rather than as prose.
ATTRIBUTION_FLOOR = 0.95
OVERHEAD_CEILING = 1.20


def run_once(project, profile_path=None):
    """One moonbuggy run. Returns (external wall clock, profile dict or None)."""
    command = [
        PYTHON,
        "-m",
        "moonbuggy.cli",
        "--no-cache",
        "--quiet",
        "--timeout",
        "10",
    ]
    env = None
    if profile_path is not None:
        import os

        env = dict(os.environ)
        env["MOONBUGGY_PROFILE"] = str(profile_path)

    began = time.perf_counter()
    subprocess.run(command, cwd=project, capture_output=True, text=True, env=env)
    wall = time.perf_counter() - began

    if profile_path is None:
        return wall, None
    with open(profile_path) as handle:
        return wall, json.load(handle)


def profile_shape(shape, root):
    """Profile one workload shape `REPEATS` times. Returns a summary dict."""
    project = workloads.build(root / shape, shape)

    profiled = []
    profiles = []
    for index in range(REPEATS):
        wall, profile = run_once(project, root / f"{shape}-{index}.json")
        profiled.append(wall)
        profiles.append(profile)

    plain = [run_once(project)[0] for _ in range(REPEATS)]

    phases = {}
    for name in profiles[0]["phases"]:
        phases[name] = statistics.median(p["phases"][name] for p in profiles)

    in_process = statistics.median(p["wall"] for p in profiles)
    external = statistics.median(profiled)

    return {
        "shape": shape,
        "phases": phases,
        # Everything outside moonbuggy's own clock, at both ends: the
        # interpreter starting and reaching the first moonbuggy import, and
        # then tearing itself down after the last line of `main`. Named for
        # both halves because it is measured as one number and it is not only
        # startup -- moonbuggy's own import chain is a separate phase the CLI
        # reports from the inside.
        "interpreter start/teardown": external - in_process,
        "other": statistics.median(p["other"] for p in profiles),
        "external_wall": external,
        "unprofiled_wall": statistics.median(plain),
        "mutants": profiles[0]["notes"].get("mutants", 0),
    }


def report(summary):
    shape = summary["shape"]
    wall = summary["external_wall"]
    print(f"\n{shape}  --  {workloads.describe(shape)}")
    print(f"  {summary['mutants']} mutants, {wall:.3f}s median of {REPEATS} runs\n")

    rows = list(summary["phases"].items())
    rows.append(("interpreter start/teardown", summary["interpreter start/teardown"]))
    rows.sort(key=lambda row: -row[1])

    print(f"  {'phase':<26} {'seconds':>9} {'share':>7}")
    print("  " + "-" * 45)
    for name, seconds in rows:
        print(f"  {name:<26} {seconds:>8.4f}s {seconds / wall:>6.1%}")
    print("  " + "-" * 45)

    attributed = sum(seconds for _, seconds in rows)
    unattributed = wall - attributed
    print(
        f"  {'other (unattributed)':<26} {unattributed:>8.4f}s "
        f"{unattributed / wall:>6.1%}"
    )

    share = attributed / wall
    print(
        f"\n  M2.1.2 phases cover >= 95% of wall clock : "
        f"{_verdict(share >= ATTRIBUTION_FLOOR)}  ({share:.1%})"
    )

    overhead = summary["external_wall"] / summary["unprofiled_wall"]
    print(
        f"  M2.1.4 profiled run within 20% of plain  : "
        f"{_verdict(overhead <= OVERHEAD_CEILING)}  "
        f"({overhead:.2f}x, {summary['unprofiled_wall']:.3f}s unprofiled)"
    )
    return share >= ATTRIBUTION_FLOOR, overhead <= OVERHEAD_CEILING


def _verdict(ok):
    return "PASS" if ok else "FAIL"


def main():
    print("moonbuggy phase profile (M2.1)")
    print(f"  {REPEATS} runs per shape, median reported; --no-cache throughout,")
    print("  so every run is cold and none of them is measuring the cache.")

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for shape in workloads.SHAPES:
            summary = profile_shape(shape, root)
            attributed_ok, overhead_ok = report(summary)
            if not attributed_ok:
                failures.append(f"{shape}: phases cover under 95% of wall clock")
            if not overhead_ok:
                failures.append(f"{shape}: profiling overhead above 20%")

    print()
    if failures:
        raise SystemExit("\n".join(f"FAILED {failure}" for failure in failures))
    print("All shapes profiled within the M2.1 bounds.")


if __name__ == "__main__":
    main()
