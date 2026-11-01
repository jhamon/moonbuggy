"""Real-project benchmark: moonbuggy vs mutmut vs naive on pinned open source.

This is the ADDITIONAL, public-facing credibility benchmark. The synthetic
benchmark (``scripts/bench_mutation.py``) stays the development signal; this
one answers a different objection: "your benchmark is a codebase you wrote
yourself." Here the workload is a widely-used, real open-source project at a
fixed commit, so anyone can reproduce the exact run and no one has to wonder
whether we hand-picked the code to flatter the tool.

Subject: **more-itertools** v11.1.0, commit ``64be96ce`` (released 2024-11).
Flat layout, zero runtime dependencies, and -- load-bearing -- not a package
mutmut itself depends on (mutmut's CLI pulls in ``click``, ``coverage``,
``libcst``, ``pytest``, ``textual``; mutating any of those would corrupt the
comparator). more-itertools is chosen precisely because it is none of them.

Scope: we mutate ``more_itertools/recipes.py`` (the itertools-recipes module,
the most widely-read half of the library) and run ``tests/test_recipes.py``
(its dedicated test file). All three tools see the SAME scope and the SAME
test selection, so the comparison is like-for-like. The scope is bounded and
recorded, not the whole library: the naive baseline re-runs the selected tests
once per mutant, which on a real project is the honest cost that this
benchmark exists to make visible.

Every tool runs in its own fresh source-only copy of the checkout, so none can
benefit from artifacts another left behind (mutmut writes mutants/, moonbuggy
writes .moonbuggy/, a warm cache flatters whoever ran second). The isolated
venv is built ONCE and shared; ``requests``-style src-layout resolution is not
needed because more-itertools is flat, resolved via ``pythonpath .``.

Run: .venv/bin/python scripts/bench_real.py   (or ``make bench-real``)
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = os.environ.get("MB_PYTHON") or str(REPO / ".venv" / "bin" / "python")
_MUTMUT_SIBLING = Path(sys.executable).parent / "mutmut"
MUTMUT = str(_MUTMUT_SIBLING) if _MUTMUT_SIBLING.exists() else "mutmut"

# Where the pinned checkout + isolated venv are kept. Outside the repo, so this
# checkout's own pyproject is never picked up as moonbuggy's pytest rootdir.
WORKDIR = Path(
    os.environ.get(
        "MOONBUGGY_REAL_BENCH", Path.home() / ".cache" / "moonbuggy-real-bench"
    )
)

PROJECT = "more-itertools"
REPO_URL = "git@github.com:more-itertools/more-itertools.git"
SHA = "64be96ceb2a6e836f76f069f4a96d2394d59fd0c"
TAG = "v11.1.0"

# Bounded mutation + test scope (see module docstring). Same for every tool.
SOURCE = "more_itertools/recipes.py"  # relative to checkout root
TESTS = "tests/test_recipes.py"  # relative to checkout root

TIMEOUT = 30

# When set, the harness writes a numbers-pipe JSONL (scripts/harness_output.py)
# of the moonbuggy measurement to this path, so a dashboard or PR can quote a
# versioned row rather than a prose table. Same contract as bench_mutation.py.
HARNESS_OUTPUT = os.environ.get("MB_HARNESS_OUTPUT")
BENCH_HYPOTHESIS = "baseline"
# H40: the naive leg rides the pipe as bench-real's environment control, in
# its own suite/hypothesis pair (same convention as bench_mutation.py's
# "naive-control" rows for the synthetic gate).
CONTROL_HYPOTHESIS = "control"

# The bench-real drift-check archive + stored prime (H40). Kept separate from
# the synthetic gate's archive/baseline so the two surfaces can never bleed
# into each other's verdicts.
REAL_ARCHIVE = os.environ.get(
    "MB_REAL_ARCHIVE", str(REPO / "intel" / "perf-bench-real-baseline.jsonl")
)
REAL_BASELINE = os.environ.get(
    "MB_REAL_BASELINE", str(REPO / "intel" / "perf-baseline-real.json")
)

# The naive baseline and mutmut both need the project importable from the tree
# they are running inside. more-itertools is flat (package dir at the root), so
# a pytest.ini with `pythonpath =.` resolves it from the working copy -- the
# same trick bench_mutation.py uses for the flat sample_project fixture.
FLAT_PYTEST = "[pytest]\ntestpaths = tests\npythonpath = .\n"


def run(command, cwd=None, check=True, timeout=1800):
    proc = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(map(str, command))}\n"
            f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
        )
    return proc


def prepare():
    """Clone at the pinned SHA (idempotent) and build the shared isolated venv.

    Returns (checkout_dir, venv_python). The venv gets pytest, moonbuggy from
    this working tree, and mutmut -- but NOT more-itertools, which every tool
    must resolve from its own working copy via ``pythonpath .``.
    """
    checkout = WORKDIR / PROJECT
    if not (checkout / ".git").exists():
        checkout.mkdir(parents=True)
        run(["git", "init", "-q"], cwd=checkout)
        run(["git", "remote", "add", "origin", REPO_URL], cwd=checkout)
        run(["git", "fetch", "-q", "--depth", "1", "origin", SHA], cwd=checkout)
        run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=checkout)
    # Re-pin even if it exists: a stale checkout must not silently move the
    # benchmark's subject. -f discards any tracked-file edits a probe may have
    # left; untracked config files are excluded from copies below anyway.
    run(["git", "fetch", "-q", "--depth", "1", "origin", SHA], cwd=checkout)
    run(["git", "checkout", "-q", "-f", "FETCH_HEAD"], cwd=checkout)

    venv = WORKDIR / "venv"
    vpython = str(venv / "bin" / "python")
    if not (venv / "bin" / "python").exists():
        run([sys.executable, "-m", "venv", str(venv)])
    run([vpython, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    run([vpython, "-m", "pip", "install", "-q", "pytest"])
    run([vpython, "-m", "pip", "install", "-q", "-e", str(REPO)])
    run([vpython, "-m", "pip", "install", "-q", "mutmut"])
    return checkout, vpython


def fresh_source_copy(root, name):
    """A source-only copy of the checkout: no .venv/.git/artifacts."""
    target = root / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        WORKDIR / PROJECT,
        target,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".moonbuggy",
            "mutants",
            ".mutmut-cache",
            ".coverage",
            # Stray config from a probe or a prior bench run: every tool writes
            # its own pytest.ini/pyproject for this scope, and whatever is in
            # the checkout is not the project's intended config for this bench.
            "pytest.ini",
            "pyproject.toml",
        ),
    )
    return target


def timed(command, cwd):
    start = time.perf_counter()
    proc = run(command, cwd, check=False)
    return time.perf_counter() - start, proc


def _counts(statuses):
    counts = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return counts


def run_moonbuggy(project, python):
    """Coverage-guided run over the scope. --no-cache = cold, like the others."""
    elapsed, _ = timed(
        [
            python,
            "-m",
            "moonbuggy.cli",
            "--no-cache",
            "--quiet",
            "--source",
            "more_itertools",
            "--include",
            "recipes.py",
            "--pytest-arg",
            TESTS,
            "--timeout",
            str(TIMEOUT),
        ],
        project,
    )
    results = project / ".moonbuggy" / "results.jsonl"
    records = [
        json.loads(line) for line in results.read_text().splitlines() if line.strip()
    ]
    counts = {}
    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return elapsed, len(records), counts


def run_mutmut(project, python):
    """mutmut over the scope, using its own source_paths + flat pythonpath."""
    mutmut = str(Path(python).parent / "mutmut")
    (project / "pytest.ini").write_text(
        "[pytest]\ntestpaths = mutants/tests\npythonpath = mutants\n"
    )
    (project / "pyproject.toml").write_text(
        '[tool.mutmut]\nsource_paths = ["more_itertools"]\n'
        'only_mutate = ["more_itertools/recipes.py"]\n'
    )
    elapsed, proc = timed([mutmut, "run"], project)
    output = proc.stdout.replace("\r", "\n")

    # mutmut's final progress line, e.g.
    #   "381/381  🎉 200 🫥 2  ⏰ 0  🤔 0  🙁 179 ..."
    totals = re.findall(r"(\d+)/(\d+)\s+🎉\s*(\d+).*?⏰\s*(\d+).*?🙁\s*(\d+)", output)
    if not totals:
        tail = "\n".join(output.splitlines()[-6:])
        raise RuntimeError(
            "could not parse mutmut output for "
            f"{PROJECT}:\n{tail}\n{proc.stderr[-2000:]}"
        )
    _, total, killed, timeout, survived = totals[-1]
    return (
        elapsed,
        int(total),
        {
            "KILLED": int(killed),
            "TIMEOUT": int(timeout),
            "SURVIVED": int(survived),
        },
    )


def run_naive(project, python):
    """The naive baseline: re-run the selected tests per mutant, no selection.

    Shares moonbuggy's operator set (so the mutant count is directly
    comparable), but no import hook, no coverage map, no warm process. Each
    mutant is applied to a fresh copy and the whole test file re-run.
    """
    sys.path.insert(0, str(REPO / "src"))
    from moonbuggy.generate import generate_mutants
    from moonbuggy.naive import run_naive as naive

    source = project / SOURCE
    mutants = list(generate_mutants(source.read_text(), module=str(SOURCE)))

    # Restrict what pytest sees to the scope's own test file.
    (project / "pytest.ini").write_text(
        f"[pytest]\ntestpaths = {TESTS}\npythonpath = .\n"
    )

    start = time.perf_counter()
    results = naive(project, mutants, timeout=TIMEOUT, python=python)
    return time.perf_counter() - start, len(results), _counts(results.values())


def report(title, note, rows):
    print(f"\n{title}")
    print(f"  {note}\n")
    print(f"  {'tool':<14} {'wall':>8} {'mutants':>9} {'mut/sec':>9}   breakdown")
    print("  " + "-" * 80)
    for label, elapsed, count, counts in rows:
        breakdown = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()) if v)
        print(
            f"  {label:<14} {elapsed:>7.1f}s {count:>9} "
            f"{count / elapsed:>9.1f}   {breakdown}"
        )


def emit_numbers(rows):
    """Write the moonbuggy measurement to the D2 numbers-pipe JSONL.

    Only moonbuggy's own row is archived (matching bench_mutation.py: mutmut
    and the naive baseline are comparators, not numbers to claim as ours). The
    suite name carries the pinned subject so the row is unambiguous; the
    hypothesis tag is the reserved ``baseline`` marker. The naive leg is also
    archived (suite ``real-naive-control``, hypothesis ``control``) -- it is
    bench-real's environment control (H40): same operators as the gate row,
    no selection, so it moves when the machine moves and not when moonbuggy's
    code does. The drift check keys its H38-analog contamination call off
    this row.

    Args:
        rows: the (label, elapsed, count, counts) rows for this run.

    Returns:
        None.
    """
    if not HARNESS_OUTPUT:
        return

    import harness_output

    out_path = Path(HARNESS_OUTPUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from moonbuggy import __version__

    moon_row = next(r for r in rows if r[0] == "moonbuggy")
    naive_row = next(r for r in rows if r[0] == "naive baseline")
    for suite, hypothesis, row in (
        (f"real-{PROJECT}-{TAG}", BENCH_HYPOTHESIS, moon_row),
        (f"real-{PROJECT}-{TAG}-naive-control", CONTROL_HYPOTHESIS, naive_row),
    ):
        _label, elapsed, count, _counts = row
        doc = harness_output.build(
            suite=suite,
            purpose="bench",
            harness="bench_real.py",
            wall_clock=elapsed,
            mutants=count,
            hypothesis=hypothesis,
            moonbuggy=__version__,
        )
        errors = harness_output.validate(doc)
        if errors:
            raise SystemExit(f"harness-output row invalid: {errors}")
        harness_output.write_jsonl(doc, out_path)
    print(f"  wrote numbers-pipe rows to {out_path}")


def check_drift():
    """H40: check the newest bench-real row for drift against its prime.

    Reuses the synthetic gate's verdict machinery (``bench_ci.py``) with
    bench-real's suite names, so the two surfaces share one calibration --
    H34 min-statistic / H35 sustained-two-night wall + ratchet / H36
    host-change re-prime / H38 sustained-two-night contamination -- instead
    of growing a second, subtly different rule set. The bench-real row is a
    single cold measurement (~4 min leg, no rep set), so ``gate_stat`` falls
    back to ``wall_clock`` per H34's legacy-row rule.

    The stored prime lives in ``intel/perf-baseline-real.json`` (same shape
    as the synthetic gate's ``perf-baseline.json``); it is primed on the
    first check after it is absent or the host changes, and ratchets down on
    a sustained improvement only -- identical to the synthetic gate.

    Returns:
        0 pass/prime, 2 regression, 3 archive/baseline error.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bench_ci

    gate_suite = f"real-{PROJECT}-{TAG}"
    control_suite = f"real-{PROJECT}-{TAG}-naive-control"
    try:
        rows = bench_ci.load_rows(REAL_ARCHIVE)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    def _rows_of(suite, hypothesis):
        return [
            row
            for row in reversed(rows)
            if row["suite"] == suite and row["hypothesis"] == hypothesis
        ]

    gate_rows = _rows_of(gate_suite, BENCH_HYPOTHESIS)
    control_rows = _rows_of(control_suite, CONTROL_HYPOTHESIS)
    if not gate_rows:
        print("ERROR: no bench-real gate rows in the archive", file=sys.stderr)
        return 3
    new = gate_rows[0]
    history = gate_rows[1:]
    control = control_rows[0] if control_rows else None
    control_history = control_rows[1:]

    try:
        base = bench_ci.load_base(REAL_BASELINE)
    except (ValueError, json.JSONDecodeError):
        print("ERROR: bench-real baseline unreadable", file=sys.stderr)
        return 3

    ok, why = bench_ci.verdict(
        new,
        base,
        history,
        control=control,
        control_history=control_history,
    )
    print(f"bench-real: {new['wall_clock']:.1f}s / {new['mutants']} mutants")
    if control is not None:
        print(f"  naive control: {control['wall_clock']:.1f}s")
    if why:
        print(f"  {why}")

    if base is None or new["host"] != base["host"]:
        prime = {}
        if control is not None:
            prime["naive_control"] = control["wall_clock"]
        bench_ci.store_base(REAL_BASELINE, {**new, **prime})
        print("  primed bench-real baseline: arming the drift check")
    elif ok and bench_ci.gate_stat(new) < bench_ci.gate_stat(base) * bench_ci.IMPROVE:
        contaminated = bench_ci.contamination_note(
            new, base, control, _control_prime(base), control_history
        )
        if contaminated and contaminated.startswith("CONTAMINATED"):
            print(f"  {contaminated}")
            print("  ratchet withheld: contaminated ground (H38/H40)")
        elif bench_ci.sustained_improvement(new, base, history):
            # Ratchet: the stored baseline becomes tonight's row (with the
            # control prime refreshed). NOTE: bench_ci.py's nightly ratchet
            # rewrites the loaded OLD row instead of tonight's row, so the
            # synthetic gate's stored wall_clock never actually moves on an
            # improvement -- a latent defect recorded in the H40 entry and
            # deliberately not changed here (verdict behavior is QA's).
            fresh = {**new}
            if control is not None:
                fresh["naive_control"] = control["wall_clock"]
            with open(REAL_BASELINE, "w", encoding="utf-8") as fh:
                json.dump(fresh, fh, indent=2, sort_keys=True)
                fh.write("\n")
            print("  wrote updated bench-real baseline (sustained improvement)")

    return 0 if ok else 2


def _control_prime(base):
    """The baseline's stored naive-control prime (H38/H40 shape passthrough)."""
    return base.get("naive_control") if base else None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Real-project credibility benchmark (moonbuggy vs mutmut vs naive)."
    )
    parser.add_argument(
        "--check-drift",
        action="store_true",
        help=(
            "skip the bench; check the newest archived bench-real row for "
            "drift against intel/perf-baseline-real.json (H40)"
        ),
    )
    args = parser.parse_args(argv)
    if args.check_drift:
        return check_drift()

    print(
        f"python: {platform.python_version()}   "
        f"platform: {platform.system()} {platform.release()}   "
        f"timeout: {TIMEOUT}s"
    )
    print(f"project: {PROJECT} @ {TAG} ({SHA[:12]})   scope: {SOURCE} + {TESTS}")

    checkout, vpython = prepare()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rows = [
            (
                "moonbuggy",
                *run_moonbuggy(fresh_source_copy(root, "real-moon"), vpython),
            ),
            ("mutmut", *run_mutmut(fresh_source_copy(root, "real-mutmut"), vpython)),
            (
                "naive baseline",
                *run_naive(fresh_source_copy(root, "real-naive"), vpython),
            ),
        ]

    report(
        f"REAL PROJECT -- {PROJECT} {TAG} @ {SHA[:12]}",
        f"mutating {SOURCE}, running {TESTS} (its dedicated test file).\n"
        f"  A bounded real slice: the naive baseline re-runs the selected tests\n"
        f"  once per mutant, which on real code is the honest cost selection\n"
        f"  exists to avoid.",
        rows,
    )

    emit_numbers(rows)

    moon = dict(zip(("time", "count", "counts"), rows[0][1:], strict=True))
    mutmut = dict(zip(("time", "count", "counts"), rows[1][1:], strict=True))
    naive = dict(zip(("time", "count", "counts"), rows[2][1:], strict=True))

    def _verdict(ok):
        return "PASS" if ok else "FAIL"

    print("\nVerdicts (on the real-project scope)")
    print(
        "  faster than mutmut : "
        + _verdict(moon["time"] < mutmut["time"])
        + f"  ({mutmut['time'] / moon['time']:.2f}x)"
    )
    print(
        "  faster than naive  : "
        + _verdict(moon["time"] < naive["time"])
        + f"  ({naive['time'] / moon['time']:.2f}x)"
    )
    print(
        "  no mutants pruned  : "
        + _verdict(moon["count"] == naive["count"])
        + f"  (moonbuggy {moon['count']} vs naive {naive['count']}, same operator set)"
    )


if __name__ == "__main__":
    raise SystemExit(main())
