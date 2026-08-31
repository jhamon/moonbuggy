"""Blocking CI gate: moonbuggy vs naive oracle, per-mutant.

Run: .venv/bin/python scripts/oracle_gate.py   (or `make check-oracle-gate`)

Every mutant moonbuggy reports on is independently verified by the naive oracle
(moonbuggy.naive.run_naive) -- a separate implementation that shares no code
with the fast path beyond mutant generation. Every disagreement is classified as
an expected semantic difference, a moonbuggy false positive, a moonbuggy false
negative, or unclassified. The gate blocks on any unexpected disagreement.

Outputs:
    disagreement_count   -- unexpected disagreements only (the gate number)
    docs/oracle-gate.json  -- full results with FP/FN history keyed by commit SHA

A verdict regression MUST be visible as a diff here before it becomes a bug
report. The oracle is the source of truth; when moonbuggy disagrees, moonbuggy
is wrong until proven otherwise.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
FIXTURE = REPO / "tests" / "fixtures" / "sample_project"
ORACLE_TOML = REPO / "tests" / "fixtures" / "oracle.toml"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workloads  # noqa: E402  # needs scripts/ on sys.path first

# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------

# What the naive oracle can produce.
NAIVE_STATUSES = frozenset({"SKIPPED", "SURVIVED", "KILLED", "TIMEOUT", "SUSPICIOUS"})

# What moonbuggy can produce that the naive oracle cannot.
MB_ONLY = frozenset({"NO_COVERAGE", "KILLED_BY_ERROR"})

# Known semantic differences that are NOT bugs.
# (moonbuggy_status, naive_status) pairs that are expected to differ.
EXPECTED_DIFFS: dict[tuple[str, str], str] = {
    ("NO_COVERAGE", "SURVIVED"): (
        "moonbuggy has a coverage map and knows no test reaches this line; "
        "the naive oracle runs the full suite and sees a pass this mutation "
        "did not cause"
    ),
    ("KILLED_BY_ERROR", "KILLED"): (
        "moonbuggy loaded killreason and distinguished an assertion kill "
        "from a crash kill; the naive oracle shares no code with moonbuggy "
        "and reports both as KILLED"
    ),
}

# ---------------------------------------------------------------------------
# Disagreement classification
# ---------------------------------------------------------------------------


def classify_disagreement(mb_status: str, naive_status: str) -> tuple[str, str] | None:
    """Classify a moonbuggy-vs-naive disagreement.

    Returns (category, reason) or None for unclassified.

    Categories:
        expected-semantic-diff  -- known engine gap, not a bug
        moonbuggy-false-positive  -- moonbuggy claims kill, naive says survive
        moonbuggy-false-negative  -- moonbuggy says survive, naive says kill
        moonbuggy-suspicious      -- moonbuggy declined to answer
        naive-suspicious          -- naive declined to answer
    """
    key = (mb_status, naive_status)

    # Known/expected differences.
    if key in EXPECTED_DIFFS:
        return ("expected-semantic-diff", EXPECTED_DIFFS[key])

    # False positive: moonbuggy claims a kill that the naive oracle does not
    # confirm.
    if mb_status in ("KILLED", "KILLED_BY_ERROR") and naive_status == "SURVIVED":
        return (
            "moonbuggy-false-positive",
            f"moonbuggy reports {mb_status} but the naive oracle "
            f"reports SURVIVED -- the full suite passes on this mutant",
        )

    # False negative: moonbuggy misses a kill the naive oracle detects.
    if mb_status == "SURVIVED" and naive_status == "KILLED":
        return (
            "moonbuggy-false-negative",
            "moonbuggy reports SURVIVED but the naive oracle reports KILLED "
            "-- the full suite catches this mutant",
        )

    # moonbuggy declined to answer (SUSPICIOUS), but naive gave a verdict.
    if mb_status == "SUSPICIOUS" and naive_status in ("KILLED", "SURVIVED"):
        return (
            "moonbuggy-suspicious",
            f"moonbuggy reported SUSPICIOUS (declined to answer) but the "
            f"naive oracle reported {naive_status}",
        )

    # Naive gave SUSPICIOUS but moonbuggy gave a verdict.
    if naive_status == "SUSPICIOUS" and mb_status in (
        "KILLED",
        "KILLED_BY_ERROR",
        "SURVIVED",
        "NO_COVERAGE",
    ):
        return (
            "naive-suspicious",
            f"naive oracle reported SUSPICIOUS (declined to answer) but "
            f"moonbuggy reported {mb_status}",
        )

    # TIMEOUT disagreements -- both tools should agree on which mutants
    # time out (they share the same timeout value).
    if "TIMEOUT" in (mb_status, naive_status) and mb_status != naive_status:
        return (
            "timeout-disagreement",
            f"moonbuggy says {mb_status}, naive says {naive_status} -- "
            f"timeout behaviour should be consistent",
        )

    return None


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


def run_moonbuggy(project: Path, source: Path, timeout: float) -> list[dict] | None:
    """Run moonbuggy on a project, return parsed results."""
    command = [
        PYTHON,
        "-m",
        "moonbuggy.cli",
        "--no-cache",
        "--quiet",
        "--source",
        str(source),
        "--timeout",
        str(timeout),
        "--flaky-probe",
        "0",
    ]
    subprocess.run(command, cwd=project, capture_output=True, text=True, timeout=3600)
    results = project / ".moonbuggy" / "results.jsonl"
    if not results.exists():
        return None
    records = []
    for line in results.read_text().splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def run_naive_oracle(
    project: Path, mutants_script: str, timeout: float
) -> dict[str, str]:
    """Run the naive oracle in a subprocess and return {mutant_id: status}.

    Runs out-of-process because the naive oracle copies the project tree and
    runs pytest inside it; doing that from the test process would put two
    conflicting conftest files on the path.
    """
    src_dir = str(REPO / "src")
    script = f"""
import json, sys
from pathlib import Path
sys.path.insert(0, {src_dir!r})
from moonbuggy.generate import generate_mutants
from moonbuggy.naive import run_naive

project = Path({str(project)!r})
mutants = []
{mutants_script}
statuses = run_naive(project, mutants, timeout={timeout!r})
print(json.dumps({{m.id: s for m, s in statuses.items()}}))
"""
    proc = subprocess.run(
        [PYTHON, "-c", script],
        capture_output=True,
        text=True,
        timeout=max(timeout * 50, 600),  # generous: one subprocess per mutant
    )
    if proc.returncode != 0:
        print(f"  naive oracle failed (rc={proc.returncode}):", file=sys.stderr)
        print(f"  {proc.stderr[-2000:]}", file=sys.stderr)
        return {}
    try:
        # Find the last JSON line (in case of stray output).
        lines = [
            ln for ln in proc.stdout.strip().splitlines() if ln.strip().startswith("{")
        ]
        *_, last = lines
        return json.loads(last)
    except (json.JSONDecodeError, ValueError):
        print("  naive oracle produced unparseable output", file=sys.stderr)
        print(f"  {proc.stdout[-2000:]}", file=sys.stderr)
        return {}


def compare_project(
    name: str,
    project: Path,
    source: Path,
    mutant_script: str,
    timeout: float,
) -> dict:
    """Run both engines on one project and compare."""
    began = time.perf_counter()

    # 1. Run moonbuggy.
    records = run_moonbuggy(project, source, timeout)
    if records is None:
        return {"project": name, "blocker": "moonbuggy produced no results"}

    # 2. Run naive oracle.
    naive = run_naive_oracle(project, mutant_script, timeout)
    if not naive:
        return {"project": name, "blocker": "naive oracle produced no results"}

    # 3. Compare per-mutant.
    by_id: dict[str, dict] = {r["id"]: r for r in records}

    entry: dict = {
        "project": name,
        "moonbuggy_mutants": len(records),
        "naive_mutants": len(naive),
        "shared": 0,
        "agree": 0,
        "disagreements": [],
        "expected_diffs": 0,
        "false_positives": 0,
        "false_negatives": 0,
        "unclassified": 0,
    }

    for mutant_id, naive_status in sorted(naive.items()):
        record = by_id.get(mutant_id)
        if record is None:
            # Mutant the naive oracle generated but moonbuggy didn't report.
            entry["disagreements"].append(
                {
                    "mutant_id": mutant_id,
                    "moonbuggy": None,
                    "naive": naive_status,
                    "category": "moonbuggy-missing",
                    "reason": "naive oracle ran this mutant but moonbuggy "
                    "did not report it",
                }
            )
            entry["unclassified"] += 1
            continue

        entry["shared"] += 1
        mb_status = record["status"]

        if mb_status == naive_status:
            entry["agree"] += 1
            continue

        # Disagreement.
        classification = classify_disagreement(mb_status, naive_status)
        if classification is None:
            category, reason = None, None
            entry["unclassified"] += 1
        else:
            category, reason = classification

        if category == "expected-semantic-diff":
            entry["expected_diffs"] += 1
        elif category == "moonbuggy-false-positive":
            entry["false_positives"] += 1
        elif category == "moonbuggy-false-negative":
            entry["false_negatives"] += 1

        entry["disagreements"].append(
            {
                "mutant_id": mutant_id,
                "moonbuggy": mb_status,
                "naive": naive_status,
                "category": category,
                "reason": reason,
            }
        )

    # Mutants moonbuggy reported that naive didn't see.
    for mutant_id in set(by_id) - set(naive):
        entry["disagreements"].append(
            {
                "mutant_id": mutant_id,
                "moonbuggy": by_id[mutant_id]["status"],
                "naive": None,
                "category": "naive-missing",
                "reason": "moonbuggy reported this mutant but the naive "
                "oracle did not run it",
            }
        )
        entry["unclassified"] += 1

    entry["disagreement_count"] = (
        entry["false_positives"] + entry["false_negatives"] + entry["unclassified"]
    )
    entry["seconds"] = round(time.perf_counter() - began, 1)
    return entry


# ---------------------------------------------------------------------------
# Project definitions
# ---------------------------------------------------------------------------


def fixture_mutant_script() -> str:
    """Generate the mutant-scanning code for the sample_project fixture."""
    modules = [
        "sample/discounts.py",
        "sample/inventory.py",
        "sample/loops.py",
        "sample/config.py",
        "sample/predicates.py",
    ]
    lines = []
    for mod in modules:
        lines.append(
            f"mutants += generate_mutants("
            f"(project / {mod!r}).read_text(), module={mod!r})"
        )
    return "\n".join(lines)


def generated_mutant_script() -> str:
    """Generate the mutant-scanning code for a generated project."""
    return """
for path in sorted(project.glob("app/*.py")):
    if path.name == "__init__.py":
        continue
    mutants += generate_mutants(path.read_text(), module=f"app/{path.name}")
"""


# The generated project shapes to test. Varied along dimensions that change
# what moonbuggy and the oracle have to agree about.
GENERATED_SHAPES = [
    ("gen-small", dict(modules=2, functions=3, tests_per_module=6, iterations=0)),
    ("gen-wide", dict(modules=8, functions=3, tests_per_module=6, iterations=0)),
    ("gen-deep", dict(modules=2, functions=8, tests_per_module=24, iterations=0)),
    ("gen-sparse", dict(modules=5, functions=4, tests_per_module=2, iterations=0)),
    ("gen-dense", dict(modules=2, functions=2, tests_per_module=30, iterations=0)),
    ("gen-slow", dict(modules=2, functions=3, tests_per_module=6, iterations=4000)),
    ("gen-uncovered", dict(modules=3, functions=6, tests_per_module=1, iterations=0)),
]


# ---------------------------------------------------------------------------
# History tracking
# ---------------------------------------------------------------------------


def get_commit_sha() -> str:
    """Return the current commit SHA, or 'unknown'."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def load_history(path: Path) -> dict:
    """Load existing FP/FN history, keyed by commit SHA."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def update_history(history: dict, sha: str, entries: list[dict]) -> dict:
    """Record this run's results under the current commit SHA."""
    disagreements = []
    fp_ids = []
    fn_ids = []
    for entry in entries:
        if "blocker" in entry:
            continue
        for d in entry.get("disagreements", []):
            if d.get("category") in ("expected-semantic-diff",):
                continue
            disagreements.append(d)
            if d.get("category") == "moonbuggy-false-positive":
                fp_ids.append(d["mutant_id"])
            elif d.get("category") == "moonbuggy-false-negative":
                fn_ids.append(d["mutant_id"])

    history[sha] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "disagreement_count": len(disagreements)
        - len([d for d in disagreements if d.get("category") == "naive-missing"]),
        "false_positives": fp_ids,
        "false_negatives": fn_ids,
        "other": [
            d["mutant_id"]
            for d in disagreements
            if d.get("category")
            not in (
                "moonbuggy-false-positive",
                "moonbuggy-false-negative",
                "naive-missing",
            )
        ],
    }
    return history


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    desc = (__doc__ or "moonbuggy vs naive oracle gate").splitlines()[0]
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--out", default=str(REPO / "docs" / "oracle-gate.json"))
    parser.add_argument(
        "--history", default=str(REPO / "docs" / "oracle-gate-history.json")
    )
    parser.add_argument(
        "--projects",
        choices=("all", "fixture-only"),
        default="all",
        help="which projects to test (default: all)",
    )
    args = parser.parse_args(argv)

    entries: list[dict] = []
    sha = get_commit_sha()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # 1. Fixture project.
        print("--- fixture")
        fixture = tmp_path / "fixture"
        shutil.copytree(
            FIXTURE,
            fixture,
            ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", ".moonbuggy", "mutants", ".coverage"
            ),
        )
        entry = compare_project(
            "fixture",
            fixture,
            fixture / "sample",
            fixture_mutant_script(),
            args.timeout,
        )
        entries.append(entry)
        _print_entry(entry)

        # 2. Generated projects.
        if args.projects != "fixture-only":
            for name, params in GENERATED_SHAPES:
                print(f"--- {name}")
                project = workloads.build_custom(tmp_path / name, **params)
                entry = compare_project(
                    name,
                    project,
                    project / "app",
                    generated_mutant_script(),
                    args.timeout,
                )
                entries.append(entry)
                _print_entry(entry)

    # Write results.
    payload = {
        "commit": sha,
        "timestamp": datetime.now(UTC).isoformat(),
        "projects": entries,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True))

    # Update FP/FN history.
    history_path = Path(args.history)
    history = load_history(history_path)
    history = update_history(history, sha, entries)
    history_path.write_text(json.dumps(history, indent=2, sort_keys=True))

    # Summary.
    total_disagreements = sum(
        e.get("disagreement_count", 0) for e in entries if "blocker" not in e
    )
    total_fp = sum(e.get("false_positives", 0) for e in entries)
    total_fn = sum(e.get("false_negatives", 0) for e in entries)
    total_unclassified = sum(e.get("unclassified", 0) for e in entries)

    print(f"\nresults -> {args.out}")
    print(f"history -> {args.history}")
    print(
        f"\n  commit {sha[:8] if sha != 'unknown' else sha}  "
        f"disagreement_count={total_disagreements}  "
        f"fp={total_fp}  fn={total_fn}  "
        f"unclassified={total_unclassified}"
    )

    if total_disagreements > 0:
        print(
            f"\nBLOCKED: {total_disagreements} unexpected disagreement(s) "
            f"between moonbuggy and the naive oracle."
        )
        if total_fp:
            print(
                f"  {total_fp} false positive(s): moonbuggy claims a kill "
                f"the naive oracle does not confirm."
            )
        if total_fn:
            print(
                f"  {total_fn} false negative(s): moonbuggy misses a kill "
                f"the naive oracle detects."
            )
        if total_unclassified:
            print(f"  {total_unclassified} unclassified: needs investigation.")
        return 1

    print("\n  All verdicts agree with the naive oracle.")
    return 0


def _print_entry(entry: dict) -> None:
    if "blocker" in entry:
        print(f"    BLOCKED: {entry['blocker'][:200]}")
        return
    print(
        f"    shared {entry['shared']}, agree {entry['agree']}, "
        f"disagree {len(entry['disagreements'])}, "
        f"expected {entry['expected_diffs']}, "
        f"fp={entry['false_positives']}, fn={entry['false_negatives']}, "
        f"unclassified={entry['unclassified']}  ({entry['seconds']}s)"
    )


if __name__ == "__main__":
    sys.exit(main())
