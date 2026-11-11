"""H43 A/B: warm-cache wall on the three canonical shapes, before vs after.

Cadence per the H40 addendum and the t_ad5398cd rules: A/B with a shared
workload per shape, interleaved to cancel drift, median of 7 reps per side.
The comparison is warm-cache `moonbuggy run` (no --no-cache) on identical
fixture copies, against worktree-local interpreters via MB_PYTHON.

Shapes are the bench-real fixture set used by prior rounds:
- fast-tests (tests/fixtures/sample_project) -- many small mutants
- slow-tests: generated workload with a sleep-heavy test module
- many-files: generated workload, many modules
"""

import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_A = Path("/Users/jhamon/projects/moonbuggy")  # before = main @ b581846
REPO_B = Path(  # after = this branch
    "/Users/jhamon/.hermes/kanban/boards/moonbuggy/workspaces/t_c23e261b"
)
PY_A = str(REPO_A / ".venv" / "bin" / "python")
PY_B = str(REPO_B / ".venv" / "bin" / "python")
FIXTURE = REPO_A / "tests" / "fixtures" / "sample_project"

SLOW_BODY = '''
import time


def slow_one():
    time.sleep(0.001)
    return 1


def slow_two(x):
    return x + 1 if x else 0
'''
SLOW_TEST = '''
from pkg.mod_{i} import slow_one, slow_two


def test_slow_one():
    assert slow_one() == 1


def test_slow_two():
    assert slow_two(1) == 2
'''


def build_shape(root: Path, name: str, modules: int, slow: bool) -> Path:
    project = root / name
    src = project / "pkg"
    tests = project / "tests"
    src.mkdir(parents=True)
    tests.mkdir()
    (src / "__init__.py").write_text("")
    (tests / "__init__.py").write_text("")
    for i in range(modules):
        if slow:
            (src / f"mod_{i}.py").write_text(SLOW_BODY)
            (tests / f"test_mod_{i}.py").write_text(SLOW_TEST.format(i=i))
        else:
            (src / f"mod_{i}.py").write_text(
                f"def add_{i}(a, b):\n    return a + b\n\ndef scale_{i}(a):\n"
                f"    return a * 2\n"
            )
            (tests / f"test_mod_{i}.py").write_text(
                f"from pkg.mod_{i} import add_{i}, scale_{i}\n\n\n"
                f"def test_add_{i}():\n    assert add_{i}(1, 2) == 3\n\n\n"
                f"def test_scale_{i}():\n    assert scale_{i}(2) == 4\n"
            )
    return project


def run_one(python: str, project: Path, src_override: str | None = None):
    """One warm-cache run; first call primes both caches.

    `src_override` puts a worktree's src/ first on sys.path, which is how the
    after side runs the branch rather than the editable install's main.
    """
    env = None
    if src_override:
        import os

        env = dict(os.environ)
        env["PYTHONPATH"] = src_override + (
            ":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
    proc = subprocess.run(
        [
            python,
            "-m",
            "moonbuggy.cli",
            "--quiet",
            "--timeout",
            "8",
            "--jobs",
            "0",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        env=env,
    )
    summary_path = project / ".moonbuggy" / "summary.json"
    if proc.returncode not in (0, 1) or not summary_path.exists():
        raise RuntimeError(
            f"run failed rc={proc.returncode}\nSTDOUT:\n{proc.stdout[-2000:]}"
            f"\nSTDERR:\n{proc.stderr[-2000:]}"
        )
    summary = json.loads(summary_path.read_text())
    wall = float(summary["elapsed"])
    return wall, summary


def shape_ab(name: str, modules: int, slow: bool, reps: int = 7):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # One workload per shape, two identical copies (A and B): same tree,
        # so the map key is the same kind of input on both sides.
        proj_a = build_shape(root, name + "-a", modules, slow)
        proj_b = build_shape(root, name + "-b", modules, slow)
        proj_a.rename(root / "wl-a")
        proj_b.rename(root / "wl-b")
        proj_a = root / "wl-a"
        proj_b = root / "wl-b"

        # Prime: one run each (cold for cache, builds both caches).
        run_one(PY_A, proj_a)
        run_one(PY_B, proj_b, src_override=str(REPO_B / "src"))

        rows_a, rows_b = [], []
        sa: dict | None = None
        sb: dict | None = None
        for _ in range(reps):
            wa, sa = run_one(PY_A, proj_a)
            wb, sb = run_one(PY_B, proj_b, src_override=str(REPO_B / "src"))
            rows_a.append(wa)
            rows_b.append(wb)
        # Verify verdicts agree side by side (same workload, same mutants):
        # compare every record's (id, status, tests_run, nearest_test) from
        # the last rep, ignoring duration fields that legitimately differ.
        def verdicts(project: Path) -> list:
            lines = (project / ".moonbuggy" / "results.jsonl").read_text().splitlines()
            return [
                (r["id"], r["status"], r["tests_run"], r.get("nearest_test"))
                for r in (json.loads(line) for line in lines)
            ]

        same = verdicts(proj_a) == verdicts(proj_b)
        med_a, med_b = statistics.median(rows_a), statistics.median(rows_b)
        print(
            f"{name}: before median {med_a:.3f}s  after median {med_b:.3f}s  "
            f"delta {(med_a - med_b) / med_a * 100:+.1f}%  "
            f"(min a {min(rows_a):.3f} / b {min(rows_b):.3f})  "
            f"verdicts equal: {same}"
        )
        print(f"    before runs {[round(r, 3) for r in rows_a]}")
        print(f"    after  runs {[round(r, 3) for r in rows_b]}")
        return {
            "shape": name,
            "before": rows_a,
            "after": rows_b,
            "median_before": med_a,
            "median_after": med_b,
            "verdicts_equal": same,
            "counts_after": sb["counts"],
            "total_after": sb["total"],
        }


def main() -> int:
    results = [
        shape_ab("fast-tests", 6, slow=False),
        shape_ab("many-files", 40, slow=False),
        shape_ab("slow-tests", 6, slow=True),
    ]
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("h43_ab.json")
    out.write_text(json.dumps(results, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
