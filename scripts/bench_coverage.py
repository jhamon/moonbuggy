"""Criterion B3: benchmark the candidate coverage mechanisms, with real numbers.

Compares, on an identical generated workload:

  baseline        plain pytest, no instrumentation
  sys.monitoring  moonbuggy's own collector (PEP 669, 3.12+)
  coverage.py     dynamic contexts (`dynamic_context = test_function`)

The workload is GENERATED rather than reusing tests/fixtures/sample_project,
which runs in 0.01s. Measuring instrumentation overhead against a suite that
does no work measures startup noise and nothing else. This one does enough
arithmetic per test that the overhead ratio means something.

Both mechanisms must also produce a usable line->test map, checked here -- a
mechanism that is fast because it records less is not a candidate.

Run: .venv/bin/python scripts/bench_coverage.py
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = str(REPO / ".venv" / "bin" / "python")

MODULES = 10
FUNCS_PER_MODULE = 20
TESTS_PER_MODULE = 20
INNER_ITERATIONS = 2000
REPEATS = 3


def generate_project(root):
    src = root / "workload"
    tests = root / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "__init__.py").write_text("")

    for m in range(MODULES):
        body = [f'"""Generated workload module {m}."""', ""]
        for f in range(FUNCS_PER_MODULE):
            body += [
                f"def compute_{f}(n):",
                "    total = 0",
                "    for i in range(n):",
                f"        if i % {f + 2} == 0:",
                "            total += i",
                "        else:",
                "            total -= i",
                "    return total",
                "",
            ]
        (src / f"mod_{m}.py").write_text("\n".join(body))

        test_body = [f"from workload.mod_{m} import *", ""]
        for t in range(TESTS_PER_MODULE):
            called = [(t * 3 + k) % FUNCS_PER_MODULE for k in range(3)]
            calls = " + ".join(f"compute_{c}({INNER_ITERATIONS})" for c in called)
            test_body += [
                f"def test_{m}_{t}():",
                f"    assert isinstance({calls}, int)",
                "",
            ]
        (tests / f"test_mod_{m}.py").write_text("\n".join(test_body))

    (root / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
    )
    return src


def time_run(cmd, cwd, env=None, repeats=REPEATS):
    """Best-of-N wall clock. Best rather than mean: we want the machine's
    capability, not the average interference from whatever else is running."""
    best = None
    for _ in range(repeats):
        start = time.perf_counter()
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
        elapsed = time.perf_counter() - start
        if proc.returncode != 0:
            raise SystemExit(
                f"command failed: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}"
            )
        best = elapsed if best is None else min(best, elapsed)
    return best


def bench_baseline(root):
    return time_run([PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider"], root)


def bench_monitoring(root, src, out):
    env = dict(os.environ)
    env["MOONBUGGY_COVERAGE_TARGETS"] = json.dumps(
        [str(p) for p in src.glob("mod_*.py")]
    )
    env["MOONBUGGY_COVERAGE_OUTPUT"] = str(out)
    elapsed = time_run(
        [
            PYTHON,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "moonbuggy.covplugin",
        ],
        root,
        env=env,
    )
    entries = json.loads(out.read_text())
    return elapsed, len(entries), sum(len(e["tests"]) for e in entries)


def bench_coverage_py(root):
    (root / ".coveragerc").write_text(
        "[run]\ndynamic_context = test_function\nsource = workload\n"
    )
    elapsed = time_run(
        [
            PYTHON,
            "-m",
            "coverage",
            "run",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        root,
    )
    import coverage

    data = coverage.CoverageData(basename=str(root / ".coverage"))
    data.read()
    pairs = 0
    attributions = 0
    for filename in data.measured_files():
        for _line, contexts in data.contexts_by_lineno(filename).items():
            real = [c for c in contexts if c]
            if real:
                pairs += 1
                attributions += len(real)
    return elapsed, pairs, attributions


def bench_pytest_cov(root):
    """coverage.py driven by pytest-cov, which records real pytest node ids.

    This is the configuration moonbuggy actually uses: `dynamic_context =
    test_function` yields `module.function` strings, which cannot be handed back
    to pytest as a selection argument.
    """
    elapsed = time_run(
        [
            PYTHON,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--cov=workload",
            "--cov-context=test",
            "--cov-report=",
        ],
        root,
    )
    import coverage

    data = coverage.CoverageData(basename=str(root / ".coverage"))
    data.read()
    pairs = 0
    attributions = 0
    for filename in data.measured_files():
        for _line, contexts in data.contexts_by_lineno(filename).items():
            real = {c.split("|")[0] for c in contexts if c}
            if real:
                pairs += 1
                attributions += len(real)
    return elapsed, pairs, attributions


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "bench"
        root.mkdir()
        src = generate_project(root)
        out = Path(tmp) / "linemap.json"

        tests = MODULES * TESTS_PER_MODULE
        print(f"workload: {MODULES} modules, {tests} tests, best of {REPEATS}\n")

        baseline = bench_baseline(root)
        mon_time, mon_pairs, mon_attr = bench_monitoring(root, src, out)
        shutil.rmtree(root / ".pytest_cache", ignore_errors=True)
        cov_time, cov_pairs, cov_attr = bench_coverage_py(root)
        for stale in (".coverage", ".coveragerc"):
            (root / stale).unlink(missing_ok=True)
        pc_time, pc_pairs, pc_attr = bench_pytest_cov(root)

        print(
            f"{'mechanism':<20} {'wall':>8} {'overhead':>10} "
            f"{'lines':>8} {'attribs':>9}"
        )
        print("-" * 60)
        print(f"{'baseline':<20} {baseline:>7.2f}s {'--':>10} {'--':>8} {'--':>9}")
        for label, t, pairs, attr in (
            ("sys.monitoring", mon_time, mon_pairs, mon_attr),
            ("coverage.py ctx", cov_time, cov_pairs, cov_attr),
            ("pytest-cov ctx", pc_time, pc_pairs, pc_attr),
        ):
            print(f"{label:<20} {t:>7.2f}s {t / baseline:>9.2f}x {pairs:>8} {attr:>9}")

        print(
            "\nlines   = distinct (file, line) pairs with at least one covering test"
            "\nattribs = total line->test attributions (the map's actual content)"
        )
        if mon_pairs == 0 or cov_pairs == 0:
            raise SystemExit("\nFAIL: a mechanism produced an empty map")


if __name__ == "__main__":
    main()
