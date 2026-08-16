"""Criterion M4.7: verify a survivor by hand, mechanically.

Run: .venv/bin/python scripts/verify_findings.py [--target NAME] [--limit N]

An unverified finding is a guess. moonbuggy reported these mutants as surviving
the tests *it selected*; M4.7 asks for something stronger — apply the mutation
to the real checkout and run the project's **entire** suite. If the suite still
passes, the gap is real and is not an artefact of selection.

Three outcomes, all of them worth having:

    suite passes  -> confirmed survivor. Selection was right.
    suite fails   -> moonbuggy was wrong. Some test outside the selected set
                     catches it, which means the coverage map missed a covering
                     test -- a false SURVIVED, and a bug in us (M4.5, M4.8).
    suite errors  -> the mutation broke collection; recorded as inconclusive.

The mutation is applied to a copy of the file and restored in a `finally`, so
an interrupted run does not leave a mutated checkout behind.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from moonbuggy.srcio import read_source, replace_line

RUN_RECORD = REPO / "docs" / "oss-run.json"


def checkout_for(name, workdir):
    return Path(workdir) / name


def run_suite(checkout, python, timeout=1800):
    """The project's whole suite. Returns (passed, tail of output)."""
    proc = subprocess.run(
        [python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
         "--rootdir", str(checkout)],
        cwd=checkout, capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-1200:]


def verify(checkout, python, survivor, baseline_code):
    """Apply one mutation, run the full suite, restore. Returns a verdict dict."""
    target = checkout / survivor["file"]
    original_text = read_source(target)
    original_bytes = target.read_bytes()

    mutated_line = survivor["diff"].split("\n", 1)[1][2:].strip()
    try:
        target.write_text(replace_line(original_text, survivor["line"], mutated_line))
        began = time.perf_counter()
        code, output = run_suite(checkout, python)
        elapsed = time.perf_counter() - began
    finally:
        target.write_bytes(original_bytes)

    if code == baseline_code:
        verdict = "confirmed"   # suite still green: nothing catches it
    elif code == 1:
        verdict = "refuted"     # a test outside the selected set caught it
    else:
        verdict = "inconclusive"

    return {
        "id": survivor["id"], "file": survivor["file"], "line": survivor["line"],
        "operator": survivor["operator"], "diff": survivor["diff"],
        "tests_run": survivor["tests_run"], "exit_code": code,
        "verdict": verdict, "seconds": round(elapsed, 1),
        "output": "" if verdict == "confirmed" else output,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--limit", type=int, default=4,
                        help="survivors to verify per target")
    parser.add_argument(
        "--workdir", default=str(Path.home() / ".cache" / "moonbuggy-oss")
    )
    parser.add_argument("--out", default=str(REPO / "docs" / "oss-verified.json"))
    args = parser.parse_args(argv)

    run = json.loads(RUN_RECORD.read_text())
    verified = []

    for entry in run["targets"]:
        if "blocker" in entry or (args.target and entry["name"] not in args.target):
            continue
        checkout = checkout_for(entry["name"], args.workdir)
        python = str(checkout / ".venv" / "bin" / "python")
        if not Path(python).exists():
            print(f"--- {entry['name']}: no checkout at {checkout}, skipping")
            continue

        baseline_code, _ = run_suite(checkout, python)
        print(f"--- {entry['name']} (baseline exit {baseline_code})")

        for survivor in entry["survivors"][: args.limit]:
            result = verify(checkout, python, survivor, baseline_code)
            result["target"] = entry["name"]
            result["tag"] = entry["tag"]
            verified.append(result)
            print(f"    {result['verdict']:12} {survivor['file']}:{survivor['line']} "
                  f"{survivor['operator']}  ({result['seconds']}s)")

    Path(args.out).write_text(json.dumps(verified, indent=2, sort_keys=True))
    counts = {}
    for item in verified:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    print(
        f"\n{len(verified)} verified: "
        + "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    )
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
