"""h33 diagnostic probe: run moonbuggy on a generated shape with teardown diag on.

Usage: .venv/bin/python scripts/_h33_diag_probe.py <shape>
Writes nothing into the repo; the generated project and artifacts live in a
TemporaryDirectory. Prints the phase profile and the per-grandchild teardown
decomposition (median/mean/max).
"""

import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))
import workloads  # noqa: E402

shape = sys.argv[1]

with tempfile.TemporaryDirectory() as root:
    rootp = Path(root)
    project = workloads.build(root, shape)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    env["MOONBUGGY_PROFILE"] = str(rootp / "prof.json")
    env["MOONBUGGY_TEARDOWN_DIAG"] = str(rootp / "diag.jsonl")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "moonbuggy.cli",
            "--no-cache",
            "--quiet",
            "--timeout",
            "10",
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )
    print("exit", r.returncode)
    if r.returncode not in (0, 1):
        print(r.stderr[-800:])
        sys.exit(1)
    prof = json.loads((rootp / "prof.json").read_text())
    print(f"--- {shape}: wall={prof['wall']:.3f}s")
    for phase, secs in sorted(prof["phases"].items(), key=lambda kv: -kv[1]):
        if secs:
            print(f"  {phase:30s} {secs:.4f}s")
    recs = [
        json.loads(line) for line in (rootp / "diag.jsonl").read_text().splitlines()
    ]
    print(f"grandchildren diagnosed: {len(recs)}")
    for key in (
        "fork_to_entry_us",
        "swap_us",
        "import_us",
        "test_us",
        "report_write_us",
    ):
        vals = [rec[key] for rec in recs]
        print(
            f"  {key:20s} med={statistics.median(vals) / 1000:.2f}ms"
            f" mean={statistics.mean(vals) / 1000:.2f}ms max={max(vals) / 1000:.2f}ms"
        )
    test_means = statistics.mean(rec["test_seconds"] for rec in recs)
    print(f"  test_seconds          mean={test_means:.4f}s")
