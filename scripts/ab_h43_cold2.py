"""Cold A/B, interleaved and role-swapped, to separate interpreter from code.

Runs: A(main), B(branch) alternating, then repeats with PYTHONPATH swapped so
each workload sees both code paths. If the delta follows the interpreter/
workload pairing rather than the code, it is environment, not regression.
"""
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ab_h43 import PY_A, REPO_A, REPO_B, build_shape  # noqa: E402

SRC_A = str(REPO_A / "src")
SRC_B = str(REPO_B / "src")


def cold_run(project: Path, src: str) -> float:
    env = dict(os.environ)
    env["PYTHONPATH"] = src
    proc = subprocess.run(
        [
            PY_A,
            "-m",
            "moonbuggy.cli",
            "--quiet",
            "--no-cache",
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
    summary = json.loads((project / ".moonbuggy" / "summary.json").read_text())
    assert proc.returncode in (0, 1), proc.stderr[-400:]
    return summary["elapsed"]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        w1 = build_shape(root, "w1", 6, False)
        w2 = build_shape(root, "w2", 6, False)
        w1.rename(root / "wl-1")
        w2.rename(root / "wl-2")
        w1, w2 = root / "wl-1", root / "wl-2"
        cold_run(w1, SRC_A)
        cold_run(w2, SRC_B)
        rows_a, rows_b = [], []
        for i in range(14):
            if i % 2 == 0:
                rows_a.append(cold_run(w1, SRC_A))
                rows_b.append(cold_run(w2, SRC_B))
            else:
                rows_b.append(cold_run(w2, SRC_B))
                rows_a.append(cold_run(w1, SRC_A))
        ma, mb = statistics.median(rows_a), statistics.median(rows_b)
        print(f"cold interleaved: main {ma:.3f}s branch {mb:.3f}s delta {(ma - mb) / ma * 100:+.1f}%")
        print(f"    main    {[round(r, 3) for r in rows_a]}")
        print(f"    branch  {[round(r, 3) for r in rows_b]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
