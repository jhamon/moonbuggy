"""Bench-CI driver + speed-moat gate. See perf-hypotheses.md."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = str(Path(__file__).resolve().parent.parent)
WALL_SLACK = 1.25
IMPROVE = 0.95
GATE_SUITE = "speed"
GATE_HYPOTHESIS = "baseline"
ARCHIVE = os.path.join(REPO, "intel", "perf-bench.jsonl")
BASELINE_PATH = os.path.join(REPO, "intel", "perf-baseline.json")
ROW_FILE = os.path.join(REPO, "intel", "perf-bench.md")


def resolve_python():
    return os.environ.get("MB_PYTHON") or sys.executable


def load_rows(path):
    from harness_output import read_jsonl, validate

    if not os.path.exists(path):
        return []
    out = []
    for row in read_jsonl(path):
        errs = validate(row)
        if errs:
            raise ValueError(f"invalid row: {errs}")
        out.append(row)
    return out


def latest_gate(rows):
    for row in reversed(rows):
        if row["suite"] == GATE_SUITE and row["hypothesis"] == GATE_HYPOTHESIS:
            return row
    return None


def load_base(path):
    if not os.path.exists(path):
        return None
    return json.load(open(path, encoding="utf-8"))


def store_base(path, row):
    data = {}
    keys = (
        "schema",
        "suite",
        "hypothesis",
        "purpose",
        "harness",
        "moonbuggy",
        "commit",
        "python",
        "host",
        "timestamp",
        "wall_clock",
        "mutants",
        "mutants_per_sec",
    )
    for k in keys:
        data[k] = row[k]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def verdict(new, base):
    if base is None:
        return True, f"priming: {new['wall_clock']:.2f}s (no baseline)"
    if new["wall_clock"] > base["wall_clock"] * WALL_SLACK:
        return False, (
            f"REGRESSION: {new['wall_clock']:.2f}s past "
            f"{base['wall_clock']:.2f}s ({base['commit']}))"
        )
    if new["wall_clock"] < base["wall_clock"] * IMPROVE:
        return True, (
            f"baseline improved: {base['wall_clock']:.2f}s -> {new['wall_clock']:.2f}s"
        )
    return True, None


def run_make():
    env = dict(os.environ)
    env["MB_HARNESS_OUTPUT"] = ARCHIVE
    env["PYTHON"] = resolve_python()
    env["MB_PYTHON"] = resolve_python()
    proc = subprocess.run(["make", "bench"], cwd=REPO, env=env)

    return proc.returncode


def row_line(row):
    return "| %s | %s | %s | %7.2f | %d | %8.1f |" % (
        row["timestamp"][:10],
        row["commit"],
        row["suite"],
        row["wall_clock"],
        row["mutants"],
        row["mutants_per_sec"],
    )


def write_row_file(ok):
    body = _HEADER
    body += "| bench gate: PASS |\n" if ok else "| bench gate: FAIL |\n"
    seen = set()
    for row in reversed(load_rows(ARCHIVE)):
        if row["suite"] not in ("speed", "fixture"):
            continue
        if row["suite"] in seen:
            continue
        body += row_line(row) + "\n"
        seen.add(row["suite"])
        if len(seen) == 2:
            break
    with open(ROW_FILE, "w", encoding="utf-8") as fh:
        fh.write(body)


_HEADER = (
    "# Perf bench\n"
    "Auto-written by scripts/bench_ci.py. Machine rows: intel/perf-bench.jsonl.\n\n"
    "| date | commit | suite | wall_s | mut | mut/sec |\n"
    "|------|--------|------|------:|----:|--------:|\n"
)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bench_ci")
    ap.add_argument("--skip-bench", action="store_true")
    args = ap.parse_args(argv)

    code = 0
    if not args.skip_bench:
        code = run_make()
        if code:
            print("make bench failed: exit %d; G1-G4 gate red" % code)
            return code

    try:
        rows = load_rows(ARCHIVE)
    except ValueError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 3

    new = latest_gate(rows)
    if new is None:
        print("ERROR: no speed/baseline rows in the archive", file=sys.stderr)
        return 3

    try:
        base = load_base(BASELINE_PATH)
    except (ValueError, json.JSONDecodeError):
        print("ERROR: baseline unreadable", file=sys.stderr)
        return 3

    ok, why = verdict(new, base)
    print("speed workload: %.2fs / %d mutants" % (new["wall_clock"], new["mutants"]))
    if why:
        print("  %s" % why)
    if base is None:
        store_base(BASELINE_PATH, new)
        print("  primed baseline: arming the speed-moat gate")
    elif ok:
        if new["wall_clock"] < base["wall_clock"] * IMPROVE:
            store_base(BASELINE_PATH, new)
            print("  wrote updated baseline")

    write_row_file(ok)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
