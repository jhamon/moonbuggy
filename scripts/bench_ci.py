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


def gate_stat(row):
    """The noise-robust wall-clock statistic the gate compares.

    H34: a single cold run of the speed workload carries up to ~30%
    one-sided scheduling noise on shared CI runners, which both tripped the
    gate on ordinary draws and let the baseline ratchet lower the bar on one
    lucky one. Rows that carry a rep-set ``min`` (runs > 1) are therefore
    gated on that minimum -- scheduling noise can only make a run slower,
    never faster. Legacy single-rep rows fall back to ``wall_clock``.

    Args:
        row: a harness-output row from the numbers pipe.

    Returns:
        The wall-clock seconds to gate on.
    """
    if row.get("runs", 1) > 1 and row.get("min") is not None:
        return row["min"]
    return row["wall_clock"]


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
    # Carry the rep-set fields so the gate can keep comparing min-to-min
    # across nights (H34); absent on legacy single-rep rows.
    for k in ("runs", "median", "min"):
        if k in row:
            data[k] = row[k]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _gate_rows(rows):
    """Archive rows that carry the gate statistic, newest first.

    Args:
        rows: rows from the numbers pipe, oldest first.

    Returns:
        Speed/baseline rows only, newest first.
    """
    return [
        row
        for row in reversed(rows)
        if row["suite"] == GATE_SUITE and row["hypothesis"] == GATE_HYPOTHESIS
    ]


def sustained_shift(new, base, history):
    """H35: is the wall crossing a sustained shift, not one noisy night?

    Min-of-3 on a single cold ~1-2s subprocess measurement still carries
    >25% cross-night spread on shared runners (observed 0.83s-1.35s nightly
    minima on identical code+host), so a 1.25x wall over a rep-min trips on
    ordinary draws. But runner noise does not repeat on consecutive nights,
    while a real regression does. The gate therefore fails only when the
    wall is exceeded on this night AND the immediately preceding night --
    H34's own "sustained shift across consecutive nights" criterion, which
    its fix assumed the rep-min would make unnecessary.

    Args:
        new: tonight's gate row.
        base: the stored baseline row.
        history: prior gate rows, newest first (tonight's row excluded).

    Returns:
        (is_sustained, prev_stat) -- whether the crossing repeats on the
        previous night, and that night's gate statistic (None when there
        is no comparable previous night, in which case the crossing is
        NOT treated as sustained: gate passes with a warning).
    """
    wall = gate_stat(base) * WALL_SLACK
    if gate_stat(new) <= wall:
        return False, None
    for row in history:
        if row.get("host") != base.get("host"):
            continue  # a host change re-primes; skip alien-host rows
        prev = gate_stat(row)
        return prev > wall, prev
    return False, None


def sustained_improvement(new, base, history):
    """H35: is the sub-IMPROVE reading a sustained shift, not one lucky draw?

    Symmetric guard on the ratchet-down: the 10-08 baseline (min 0.8303)
    was a lucky rep-min that armed the wall at 1.04s for ordinary 1.35s
    nights. Lowering the bar now requires two consecutive nights below
    ``base * IMPROVE``.

    Args:
        new: tonight's gate row.
        base: the stored baseline row.
        history: prior gate rows, newest first (tonight's row excluded).

    Returns:
        True when tonight AND the previous same-host night both read below
        the improvement threshold.
    """
    bar = gate_stat(base) * IMPROVE
    if gate_stat(new) >= bar:
        return False
    for row in history:
        if row.get("host") != base.get("host"):
            continue
        return gate_stat(row) < bar
    return False


def verdict(new, base, history=()):
    if base is None:
        return True, f"priming: {new['wall_clock']:.2f}s (no baseline)"
    if new["host"] != base["host"]:
        return True, (
            f"host changed: {base['host']} -> {new['host']}; re-priming baseline"
        )
    new_stat, base_stat = gate_stat(new), gate_stat(base)
    if new_stat > base_stat * WALL_SLACK:
        sustained, prev = sustained_shift(new, base, history)
        if sustained:
            return False, (
                f"REGRESSION: {new_stat:.2f}s past {base_stat:.2f}s "
                f"({base['commit']}), sustained (prev night {prev:.2f}s)"
            )
        return True, (
            f"WARNING: {new_stat:.2f}s past {base_stat:.2f}s "
            f"({base['commit']}) on one night; gate trips on a sustained "
            "shift (H35), watch the next nightly run"
        )
    if new_stat < base_stat * IMPROVE:
        if sustained_improvement(new, base, history):
            return True, f"baseline improved: {base_stat:.2f}s -> {new_stat:.2f}s"
        return True, (
            f"below improve bar ({new_stat:.2f}s < {base_stat:.2f}s * "
            f"{IMPROVE}) on one night; ratchet needs a sustained shift (H35)"
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


def write_row_file(ok, gate_row=None):
    host_note = (
        "Gate host: %s. The gate compares runs on the same host only; a host"
        " change re-primes the baseline.\n" % gate_row["host"]
        if gate_row
        else ""
    )
    gate_line = (
        "Bench gate: **%s**. %s" % ("PASS" if ok else "FAIL", host_note)
    ).rstrip()
    head, tail = _HEADER.split("\n\n", 1)
    body = head + "\n\n" + gate_line + "\n\n" + tail
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

    gate_rows = _gate_rows(rows)
    new = latest_gate(rows)
    if new is None:
        print("ERROR: no speed/baseline rows in the archive", file=sys.stderr)
        return 3
    # H35: the gate and the ratchet both need the cross-night history --
    # every gate row older than tonight's, newest first.
    history = gate_rows[1:] if gate_rows and gate_rows[0] is new else gate_rows
    if not history:
        history = [row for row in gate_rows if row is not new]

    try:
        base = load_base(BASELINE_PATH)
    except (ValueError, json.JSONDecodeError):
        print("ERROR: baseline unreadable", file=sys.stderr)
        return 3

    ok, why = verdict(new, base, history)
    print("speed workload: %.2fs / %d mutants" % (new["wall_clock"], new["mutants"]))
    if gate_stat(new) != new["wall_clock"]:
        print("  gate on best-of-%d min: %.2fs" % (new.get("runs", 1), gate_stat(new)))
    if why:
        print("  %s" % why)
    if base is None or new["host"] != base["host"]:
        store_base(BASELINE_PATH, new)
        print("  primed baseline: arming the speed-moat gate")
    elif ok and gate_stat(new) < gate_stat(base) * IMPROVE:
        if sustained_improvement(new, base, history):
            store_base(BASELINE_PATH, new)
            print("  wrote updated baseline (sustained improvement)")

    write_row_file(ok, gate_row=new)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
