"""h33 probe on an expensive-test shape: fewer tests, each doing real work.

Not one of the three named shapes -- a fourth, deliberately -- because the
child-teardown question is per-child overhead, and the honest way to see how
much of it remains against a suite that takes real time is to raise the test
time while keeping the child count high. Uses build_custom, which exists for
exactly this (the differential harness's needs).

Usage: .venv/bin/python scripts/_h33_diag_probe_heavy.py
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

with tempfile.TemporaryDirectory() as root:
    rootp = Path(root)
    project = workloads.build_custom(
        root, modules=10, functions=2, tests_per_module=3, iterations=200000
    )
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
            "60",
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
    print(f"wall={prof['wall']:.3f}s")
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
