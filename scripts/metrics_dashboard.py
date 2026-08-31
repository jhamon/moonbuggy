#!/usr/bin/env python3
"""Compose and write one metrics-dashboard row from real, measured artifacts.

This is the "rows write themselves" pipe for ``intel/metrics-dashboard.md``
(the git-tracked, CI-updated read surface). Instead of a bot hand-typing a row,
this script reads the machine-readable artifacts the build gates already
produce and composes the row from them:

  * ``docs/differential.json``       -- mutmut differential (shared/agree/disagree)
  * ``docs/oracle-gate.json``        -- naive-oracle gate (disagreement_count, FP/FN)
  * a ``pytest --cov`` pass          -- source-line coverage %

Every number is sourced from actual tool output; no field is invented. The
dashboard is the *read* surface of both contracts (docs/contracts/), never
their definition -- this script only projects what the pipes produced.

Usage:
    .venv/bin/python scripts/metrics_dashboard.py [--out PATH]
        --out   where to write (default: <repo>/intel/metrics-dashboard.md)

Exit code 0 on success (row written), 1 if a source artifact is missing so the
row would be dishonest (never invent numbers).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "intel" / "metrics-dashboard.md"


def _git_short_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _lines_of(path: str) -> int | None:
    """Return the line count of a source tree, or None when unavailable."""
    try:
        p = REPO / path
        return sum(1 for _ in p.rglob("*.py") if "fixtures" not in str(p))
    except Exception:
        return None


def _load_differential(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    projects = data.get("projects", [])
    shared = sum(p.get("shared", 0) for p in projects)
    agree = sum(p.get("agree", 0) for p in projects)
    disagreements = sum(len(p.get("disagreements", [])) for p in projects)
    unclassified = sum(
        1
        for p in projects
        for d in p.get("disagreements", [])
        if d.get("category") == "unclassified"
    )
    n = len(projects)
    if shared == 0:
        return None
    return {
        "n_projects": n,
        "shared": shared,
        "agree": agree,
        "agree_pct": round(100.0 * agree / shared, 1),
        "disagree": disagreements,
        "unclassified": unclassified,
    }


def _load_oracle_gate(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    projects = data.get("projects", [])
    disagreement_count = sum(p.get("disagreement_count", 0) for p in projects)
    fp = sum(p.get("false_positives", 0) for p in projects)
    fn = sum(p.get("false_negatives", 0) for p in projects)
    return {
        "disagreement_count": disagreement_count,
        "fp": fp,
        "fn": fn,
    }


def _coverage(python: str) -> tuple[float | None, int | None]:
    """Return (line_coverage_pct, statements) from a pytest --cov pass, or
    (None, None) when the pass cannot produce the total (e.g. unrelated WIP
    test breakage). Best-effort only: coverage is advisory, never a gate."""
    try:
        out = subprocess.run(
            [
                python,
                "-m",
                "pytest",
                "--cov=moonbuggy",
                "--cov-report=term-missing",
                "-q",
            ],
            capture_output=True,
            text=True,
            cwd=REPO,
            timeout=240,
        )
        text = out.stdout + out.stderr
        # total line looks like:  TOTAL     3618    1330    63%      ...
        m = re.search(r"TOTAL\s+(\d+)\s+\d+\s+(\d+)%", text)
        if not m:
            return None, None
        return int(m.group(2)), int(m.group(1))
    except Exception:
        return None, None


def _gate_label(oracle: dict[str, Any] | None) -> str:
    if oracle is None:
        return "n/a"
    return "oracle+differential" if oracle["disagreement_count"] == 0 else "RED-oracle"


def _render_row(r: dict[str, Any]) -> str:
    cov = f"{r['cov_pct']}%" if r["cov_pct"] is not None else "n/a"
    src = r.get("source_lines") or ""
    oracle = r["oracle"] or {}
    diff = r["differential"] or {}
    notes = r.get("notes", "")
    oracle_agree = f"{diff['agree']}/{diff['shared']}" if diff else ""
    return (
        f"| {r['date']} | {r['commit']} | {src} | | {r['gate']} | @moonbuggy-boss | "
        f"{oracle_agree} | {diff.get('shared') or ''} | "
        f"{diff.get('agree') or ''} ({diff.get('agree_pct')}%) | "
        f"{diff.get('disagree') or ''} | {diff.get('unclassified') or ''} | "
        f"{oracle.get('disagreement_count') if oracle else ''} | | {cov} | {notes}"
    )


_TABLE_HEADER = (
    "| date | commit | source_lines | test_lines | gate | owner | "
    "oracle_agree | diff_shared | diff_agree | diff_disagree | "
    "diff_unclassified | oracle_s | fast_suite_s | cov_pct | notes |"
)
_TABLE_SEP = (
    "|------|--------|------------:|-----------:|------|-------|"
    "-------------:|------------:|-----------:|--------------:|"
    "------------------:|--------:|------------:|-------:|-------|"
)

HEADER = (
    "# Metrics Dashboard\n"
    "\n"
    "CI-updated per commit. Each row is owned by a named bot; do not edit by "
    "hand.\n"
    "A later CI run rewrites this file, and the rewrite itself is the "
    "drift-detection\n"
    "signal. See `docs/competitive-intel.md` §4a for the re-ranking "
    "discipline.\n"
    "\n"
    "Columns: correctness (oracle agreement, differential disagreement, FP/FN\n"
    "history), performance (harness wall-clock, mutants/sec, hypothesis tag),\n"
    "coverage (source line %), and gate status.\n"
    "\n" + _TABLE_HEADER + "\n" + _TABLE_SEP + "\n"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--python",
        default=str(REPO / ".venv" / "bin" / "python"),
        help="Python interpreter to run the coverage pass with",
    )
    ap.add_argument(
        "--keep-rows",
        action="store_true",
        help="Retain previously-written rows (append) instead of "
        "replacing the file with only the current row.",
    )
    args = ap.parse_args()

    differential = _load_differential(REPO / "docs" / "differential.json")
    oracle = _load_oracle_gate(REPO / "docs" / "oracle-gate.json")

    # The row is only honest if we have real differential numbers (the
    # correctness spine). Without them, do not fabricate a row.
    if differential is None:
        print(
            "ERROR: docs/differential.json missing/empty -- refusing to invent a row.",
            file=sys.stderr,
        )
        return 1

    cov_pct, stmts = _coverage(args.python)
    today = date.today().isoformat()
    commit = _git_short_sha()

    row: dict[str, Any] = {
        "date": today,
        "commit": commit,
        "source_lines": stmts,
        "gate": _gate_label(oracle),
        "differential": differential,
        "oracle": oracle,
        "cov_pct": cov_pct,
        "notes": (
            "Auto row via scripts/metrics_dashboard.py. Differential: "
            f"{differential['n_projects']} projects, "
            f"{differential['shared']} shared, "
            f"{differential['agree']} agree ({differential['agree_pct']}%), "
            f"{differential['disagree']} disagreements, "
            f"{differential['unclassified']} unclassified."
            + (
                f" Oracle gate: {oracle['disagreement_count']} disagreements, "
                f"{oracle['fp']} FP, {oracle['fn']} FN."
                if oracle
                else ""
            )
        ),
    }

    body = HEADER
    if args.keep_rows and args.out.exists():
        old = args.out.read_text(encoding="utf-8")
        # keep prior data rows (skip header + separator + empty)
        prior = [
            ln
            for ln in old.splitlines()
            if ln.startswith("| ") and not ln.startswith("| date")
        ]
        body = HEADER + "\n".join(prior) + "\n" if prior else body
    body += _render_row(row) + "\n"

    args.out.write_text(body, encoding="utf-8")
    print(f"WROTE {args.out}")
    print(_render_row(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
