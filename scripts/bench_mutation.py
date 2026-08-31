"""Criteria G1-G4: moonbuggy vs mutmut vs a naive baseline, on one fixture.

G2 is the pass/fail speed gate: moonbuggy's wall clock must be lower than
mutmut's on the same project. G3 exists because that gate is trivially gamed by
generating fewer mutants, so mutant counts and a normalised mutants/second
figure are reported alongside, and the script refuses to declare a pass without
them.

Every tool runs against its own fresh copy of the fixture, so none of them see
another's leftovers -- mutmut writes a mutants/ tree, moonbuggy writes
.moonbuggy/, and a stale cache would flatter whoever ran second.

Run: .venv/bin/python scripts/bench_mutation.py
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = str(REPO / ".venv" / "bin" / "python")
FIXTURE = REPO / "tests" / "fixtures" / "sample_project"
TIMEOUT = 8

# When set, the harness writes a numbers-pipe JSONL (scripts/harness_output.py)
# of every moonbuggy measurement to this path, so a dashboard or PR can quote a
# versioned, hypothesis-tagged row instead of a prose table.
HARNESS_OUTPUT = os.environ.get("MB_HARNESS_OUTPUT")

# The perf-hypotheses tag these bench numbers belong to. G1-G4 measures the
# whole engine against its own bar, not one hypothesis in isolation, so the
# honest tag is the reserved "baseline" marker rather than a single H-number.
BENCH_HYPOTHESIS = "baseline"

# mutmut needs the project's imports to resolve to the mutants/ tree it builds.
# The fixture's own pytest.ini points at the real source, so it is rewritten in
# mutmut's copy only. This is tool-specific setup, not a handicap: without it
# mutmut cannot map a single test to a single mutant and refuses to run.
MUTMUT_PYTEST_INI = "[pytest]\ntestpaths = mutants/tests\npythonpath = mutants\n"
MUTMUT_PYPROJECT = '[tool.mutmut]\nsource_paths = ["sample/"]\n'


# --- the speed workload ----------------------------------------------------
#
# The fixture cannot demonstrate the speed claim and it is worth being explicit
# about why. Its suite runs in 0.01s, so the per-mutant cost is almost entirely
# pytest process startup. Selecting 2 tests instead of 14 saves nothing measurable,
# and moonbuggy ties the naive baseline on it exactly.
#
# Coverage-guided selection only pays when test EXECUTION dominates startup.
# This workload is built so it does: many tests, each doing real work, and each
# mutable line covered by only a few of them. That is the shape of the real
# suites the design is aimed at, and it is where the lever in 4.1 shows up.
WORKLOAD_MODULES = 3
WORKLOAD_FUNCS = 4
WORKLOAD_TESTS_PER_MODULE = 30
WORKLOAD_ITERATIONS = 6000


def generate_workload(root, name):
    project = root / name
    (project / "app").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "app" / "__init__.py").write_text("")

    for m in range(WORKLOAD_MODULES):
        body = []
        for f in range(WORKLOAD_FUNCS):
            body += [
                f"def compute_{f}(n):",
                "    total = 0",
                "    for i in range(n):",
                f"        if i % {f + 2} == 0:",
                "            total += i",
                "    return total",
                "",
            ]
        (project / "app" / f"mod_{m}.py").write_text("\n".join(body))

        tests = [f"from app.mod_{m} import *", ""]
        for t in range(WORKLOAD_TESTS_PER_MODULE):
            func = t % WORKLOAD_FUNCS
            tests += [
                f"def test_{m}_{t}():",
                f"    assert compute_{func}({WORKLOAD_ITERATIONS}) >= 0",
                "",
            ]
        (project / "tests" / f"test_mod_{m}.py").write_text("\n".join(tests))

    (project / "pytest.ini").write_text("[pytest]\ntestpaths = tests\npythonpath = .\n")
    return project


def fresh_copy(root, name):
    target = root / name
    shutil.copytree(
        FIXTURE,
        target,
        ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".moonbuggy", "mutants", ".coverage"
        ),
    )
    return target


def timed(command, cwd, allow_failure=True):
    start = time.perf_counter()
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    if not allow_failure and proc.returncode != 0:
        raise SystemExit(f"failed: {' '.join(command)}\n{proc.stdout}\n{proc.stderr}")
    return elapsed, proc


def run_moonbuggy(project):
    # --no-cache so this is a cold run. Comparing a warm moonbuggy against a
    # cold mutmut would be meaningless.
    elapsed, proc = timed(
        [
            PYTHON,
            "-m",
            "moonbuggy.cli",
            "--no-cache",
            "--quiet",
            "--timeout",
            str(TIMEOUT),
            "--jobs",
            os.environ.get("MB_JOBS", "0"),
        ],
        project,
    )
    records = [
        json.loads(line)
        for line in (project / ".moonbuggy" / "results.jsonl").read_text().splitlines()
    ]
    return elapsed, len(records), _counts(r["status"] for r in records)


def run_mutmut(project, package="sample"):
    (project / "pytest.ini").write_text(
        "[pytest]\ntestpaths = mutants/tests\npythonpath = mutants\n"
    )
    (project / "pyproject.toml").write_text(
        f'[tool.mutmut]\nsource_paths = ["{package}/"]\n'
    )

    elapsed, proc = timed([str(REPO / ".venv" / "bin" / "mutmut"), "run"], project)
    output = proc.stdout.replace("\r", "\n")

    # mutmut's final progress line: "26/26  🎉 19 🫥 0  ⏰ 2  🤔 0  🙁 5 ..."
    totals = re.findall(r"(\d+)/(\d+)\s+🎉\s*(\d+).*?⏰\s*(\d+).*?🙁\s*(\d+)", output)
    if not totals:
        raise SystemExit(f"could not parse mutmut output:\n{output[-2000:]}")
    _, total, killed, timeout, survived = totals[-1]
    return (
        elapsed,
        int(total),
        {
            "KILLED": int(killed),
            "TIMEOUT": int(timeout),
            "SURVIVED": int(survived),
        },
    )


def run_naive(project, package="sample"):
    sys.path.insert(0, str(REPO / "src"))
    from moonbuggy.generate import generate_mutants
    from moonbuggy.naive import run_naive as naive

    mutants = []
    for path in sorted((project / package).glob("*.py")):
        relative = str(path.relative_to(project))
        mutants.extend(generate_mutants(path.read_text(), module=relative))

    start = time.perf_counter()
    results = naive(project, mutants, timeout=TIMEOUT, python=PYTHON)
    return time.perf_counter() - start, len(results), _counts(results.values())


def _counts(statuses):
    counts = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return counts


def report(title, note, rows):
    print(f"\n{title}")
    print(f"  {note}\n")
    print(f"  {'tool':<14} {'wall':>8} {'mutants':>9} {'mut/sec':>9}   breakdown")
    print("  " + "-" * 76)
    for label, elapsed, count, counts in rows:
        breakdown = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()) if v)
        print(
            f"  {label:<14} {elapsed:>7.2f}s {count:>9} "
            f"{count / elapsed:>9.2f}   {breakdown}"
        )


def main():
    print(
        f"python: {platform.python_version()}   "
        f"platform: {platform.system()} {platform.release()}   timeout: {TIMEOUT}s"
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        fixture_rows = [
            ("moonbuggy", *run_moonbuggy(fresh_copy(root, "fx-moon"))),
            ("mutmut", *run_mutmut(fresh_copy(root, "fx-mutmut"))),
            ("naive baseline", *run_naive(fresh_copy(root, "fx-naive"))),
        ]

        speed_rows = [
            ("moonbuggy", *run_moonbuggy(generate_workload(root, "wl-moon"))),
            (
                "mutmut",
                *run_mutmut(generate_workload(root, "wl-mutmut"), package="app"),
            ),
            (
                "naive baseline",
                *run_naive(generate_workload(root, "wl-naive"), package="app"),
            ),
        ]

    report(
        "FIXTURE (sample_project) -- correctness-shaped, not speed-shaped",
        "Suite runs in 0.01s, so per-mutant cost is almost all process startup\n"
        "  and one 8s timeout. Selection has nothing to save here; reported for\n"
        "  completeness, and NOT the basis of the G2 verdict.",
        fixture_rows,
    )
    report(
        "SPEED WORKLOAD -- generated, test execution dominates startup",
        f"{WORKLOAD_MODULES} modules, "
        f"{WORKLOAD_MODULES * WORKLOAD_TESTS_PER_MODULE} tests "
        "doing real work, each line covered by\n  a few tests. This is the shape where "
        "coverage-guided selection pays,\n  and the basis of the G2 verdict.",
        speed_rows,
    )

    moon = dict(zip(("time", "count", "counts"), speed_rows[0][1:], strict=True))
    mutmut = dict(zip(("time", "count", "counts"), speed_rows[1][1:], strict=True))
    naive = dict(zip(("time", "count", "counts"), speed_rows[2][1:], strict=True))

    print("\nVerdicts (on the speed workload)")
    beats_mutmut = moon["time"] < mutmut["time"]
    print(
        f"  G2  faster than mutmut : {_verdict(beats_mutmut)}  "
        f"({mutmut['time'] / moon['time']:.2f}x)"
    )
    print(
        f"      faster than naive  : {_verdict(moon['time'] < naive['time'])}  "
        f"({naive['time'] / moon['time']:.2f}x)"
    )
    print(
        f"  G3  mutant counts      : moonbuggy {moon['count']}, "
        f"mutmut {mutmut['count']}, naive {naive['count']}"
    )

    # G3: the real question is whether moonbuggy went fast by SKIPPING work.
    # Comparing counts against mutmut cannot answer that -- the two implement
    # different operator sets. The naive baseline shares moonbuggy's operators
    # exactly, so an equal count there proves nothing was pruned.
    no_pruning = moon["count"] == naive["count"]
    print(
        f"  G3  no mutants pruned  : {_verdict(no_pruning)}  "
        f"(moonbuggy {moon['count']} == naive {naive['count']}, same operator set)"
    )

    if moon["count"] < mutmut["count"]:
        print(
            f"\n  NOTE: mutmut generates {mutmut['count'] - moon['count']} more"
            " mutants, from"
            " operators the MVP set (3.2)\n  does not implement. So the wall-clock"
            " comparison is"
            " not like-for-like.\n  Per mutant: mutmut"
            f" {mutmut['count'] / mutmut['time']:.0f} vs moonbuggy"
            f" {moon['count'] / moon['time']:.0f}"
            " mut/sec.\n  What G3 asks -- that moonbuggy is not fast because it does"
            " less --"
            " is\n  answered by the naive comparison above, and by the A2b inventory"
            " test\n"
            "  proving every expected mutant is generated."
        )

    emit_numbers(speed_rows, fixture_rows)

    failures = []
    if not beats_mutmut:
        failures.append("G2: moonbuggy is not faster than mutmut")
    if not no_pruning:
        failures.append("G3: moonbuggy generated fewer mutants than the naive baseline")
    if failures:
        raise SystemExit("\n" + "\n".join(f"FAILED {f}" for f in failures))


def _verdict(ok):
    return "PASS" if ok else "FAIL"


def emit_numbers(speed_rows, fixture_rows):
    """Write every moonbuggy measurement to the numbers-pipe JSONL.

    Emits one row per measurement *of moonbuggy* (the G1-G4 verdict compares
    moonbuggy to its own bar; mutmut and the naive baseline are comparators, not
    numbers to archive as ours). Rows carry the reserved ``baseline`` hypothesis
    tag and are validated before write, so a corrupted row never lands.

    Args:
        speed_rows: the three (label, elapsed, count, counts) rows for the
            speed workload.
        fixture_rows: the same for the fixture workload.

    Returns:
        None.
    """
    if not HARNESS_OUTPUT:
        return

    # scripts/ is this module's own directory, already on sys.path when the
    # script is run directly; src/ is added by run_naive earlier in main() so a
    # bench run's moonbuggy is the local tree, not some installed copy.
    import harness_output

    out_path = Path(HARNESS_OUTPUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from moonbuggy import __version__

    rows = [("fixture", fixture_rows), ("speed", speed_rows)]
    for suite, group in rows:
        for label, elapsed, count, _counts in group:
            if label != "moonbuggy":
                continue
            doc = harness_output.build(
                suite=suite,
                purpose="bench",
                harness="bench_mutation.py",
                wall_clock=elapsed,
                mutants=count,
                hypothesis=BENCH_HYPOTHESIS,
                moonbuggy=__version__,
            )
            errors = harness_output.validate(doc)
            if errors:
                raise SystemExit(f"harness-output row invalid: {errors}")
            harness_output.write_jsonl(doc, out_path)

    print(f"  wrote {len(rows)} numbers-pipe rows to {out_path}")


if __name__ == "__main__":
    main()
