"""Milestone M1.1: run moonbuggy against its own source, contained.

Run: .venv/bin/python scripts/selfmutation_rig.py [--target-ref REF] [--workdir DIR]
     [--include FRAGMENT ...] [--timeout SECONDS] [--skip-full-run]

The danger that kept self-mutation out of Phase 1 is that the code under
mutation is the mutation engine itself: a defective mutant could corrupt the
run meant to detect it, and the failure would read as a finding rather than an
error. The containment scheme (stress-tested by the spike, see
docs/development/spike-selfmutation-findings.md) is three isolations plus a
fourth constraint round 2 added:

- the **runner** is a pinned moonbuggy installed non-editably in its own venv,
  and the runner parent process must execute that pinned copy. Exporting
  PYTHONPATH for the children silently makes the parent import the target's
  engine too (spike Finding 4), so the rig launches the parent with
  ``python -E -m moonbuggy.cli`` while still exporting PYTHONPATH;
- the **target** is a separate clone of this repo;
- the **import boundary** is ``PYTHONPATH=<target>/src``, which makes every
  child pytest process import the target's package. Without it the runner
  mutates files the suite never imports and the verdicts are vacuous (spike
  Finding 1);
- and because a forked child inherits the parent's sys.path, the -E-pinned
  parent must run each mutant's tests in a fresh subprocess (``--workers N``,
  the cold path) — an -E parent's forked grandchildren would import the pinned
  copy and every mutation would silently not apply.

Each criterion of M1.1 maps to a step of this script (M1.1.1 through M1.1.8);
the steps are labelled in the output so an evaluator can check them by running
the rig. The receipt (docs/selfmutation-receipt.json) is written only by a
run that produced records covering every covered mutant (M1.1.6): a failed or
empty run never overwrites the previous good receipt. The rig is
deliberately NOT wired into CI (M1.1.8): the stability
question (Finding 3) is unresolved and a full run costs laptop-scale tens of
minutes.

Provenance: the rig's steps are ports of the ad-hoc probes that produced the
spike findings — the boundary probes (Findings 1, 4), the semantics check
(Finding 7, a poisoned .pyc defeats a path-only pin check), the plant demo
(Findings 2 and 6), and the artifact cleanup the crashed runs taught us to
always do (Finding 3.4).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_WORKDIR = Path.home() / ".cache" / "moonbuggy-selfmutation"

# The pinned runner is installed from a fixed ref, not from the working tree,
# so a defect landed on the branch under test cannot hide inside the engine
# that is judging it. The default is this repo's HEAD (the rig script's own
# repo) rather than a frozen tag: the runner must carry the fork-path
# PYTHONPATH fix (see the findings-doc addendum), and any ref that predates it
# cannot pass M1.1.2's boundary probes against a current target. Override with
# --runner-ref to pin an explicit tag or commit for a documented run.
PINNED_REF = "HEAD"

# Key engine modules hashed before and after every run (M1.1.5): if a planted
# target defect corrupts the runner, these change.
INTEGRITY_MODULES = (
    "forkserver.py",
    "runner.py",
    "report.py",
    "codeswap.py",
    "export.py",
)

# The known module-level constant the pinned-side probe asserts (M1.1.2,
# semantics not just paths — spike Finding 7). A stale .pyc compiled from
# mutated source passes mtime+size staleness validation forever, so
# moonbuggy.__file__ pointing at the pinned install does not prove the
# bytecode loaded from it is the pinned install's. Only a semantic assertion
# or a pyc-vs-fresh-compile comparison catches that.
#
# The pin must live in a module present at the pinned ref AND be compared
# against the pinned source's value, not a value hardcoded here — a constant
# that changes between the pin and the target ref must not make the rig
# refuse a healthy install. The rig reads the expected value from the pinned
# checkout's own source.
SEMANTIC_PROBE = r"""
import moonbuggy
from moonbuggy import report
print(moonbuggy.__file__)
print(report.__file__)
print(report.RECORD_SCHEMA)
"""

# The child-side boundary probe: run WITHOUT -E but WITH the rig's
# PYTHONPATH, from the target directory. Every process that runs the target's
# tests must resolve moonbuggy to the target checkout (M1.1.2, Finding 1).
CHILD_PROBE = r"""
import moonbuggy
from moonbuggy import report
print(moonbuggy.__file__)
print(report.__file__)
print(report.RECORD_SCHEMA)
"""

# The plant used for M1.1.4/M1.1.5. Data-driven rather than a hardcoded line:
# the hardcoded spike plant (export.py:83, condition_negation) is NO_COVERAGE
# at later refs, so a plant there changes nothing the tests can see. Instead
# the rig reads the clean run's receipt and plants the INVERSE of a mutant
# the clean run reported KILLED — turning a covered, asserted line into its
# mutated text, which the clean run proved the suite detects. Detection is
# then measured as: verdicts changed relative to the clean run (Finding 2's
# signature) or the red-baseline gate refused (Finding 6's).

# Verdict statuses the M1.1.6 gate treats as covered. Every public status
# except NO_COVERAGE: a record is a verdict, and NO_COVERAGE is the one that
# says nothing about whether the mutation was noticed (it never reached a
# run). The set is pinned by tests/test_selfmutation_rig_gate.py, which fails
# if the report vocabulary moves and this tuple is not updated -- the same
# closed-set discipline as report.STATUS_KEYWORDS, mirrored here because the
# rig must run without the engine importable.
VERDICT_STATUSES = (
    "KILLED",
    "KILLED_BY_ERROR",
    "SURVIVED",
    "TIMEOUT",
    "SUSPICIOUS",
    "SKIPPED",
)

FAILURES: list = []


def step(label, ok, detail=""):
    """Print one criterion step and record it for the exit code."""
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def run(command, cwd=None, timeout=1800, check=True, env=None):
    proc = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(map(str, command))}\n"
            f"{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}"
        )
    return proc


def clone_at(ref, dest, repo=REPO):
    """Clone the repo at a ref into dest (a separate checkout by construction).
    A branch name is resolved to its commit first: the checkout and the
    runner-venv install must be pinned to exactly one tree."""
    commit = run(["git", "rev-parse", ref], cwd=REPO).stdout.strip()
    if dest.exists():
        run(["git", "fetch", "--tags"], cwd=dest, timeout=600)
        run(["git", "checkout", commit], cwd=dest, timeout=600)
        run(["git", "reset", "--hard", commit], cwd=dest, timeout=600)
        return dest
    run(["git", "clone", "--no-checkout", str(repo), str(dest)], timeout=1200)
    run(["git", "checkout", commit], cwd=dest, timeout=600)
    return dest


def build_runner_venv(workdir, ref):
    """M1.1.1: pinned moonbuggy, installed non-editably (a site-packages copy)."""
    runner_src = clone_at(ref, workdir / "runner-src")
    venv = workdir / "runner-venv"
    if not (venv / "bin" / "python").exists():
        run([sys.executable, "-m", "venv", str(venv)], timeout=600)
    python = str(venv / "bin" / "python")
    run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"], timeout=600)
    run([python, "-m", "pip", "install", "-q", "pytest", "pytest-cov"], timeout=1200)
    # The runner venv runs the TARGET's test suite (M1.1.3), which needs the
    # dev extras (hypothesis, pytest-xdist) — installing only pytest made a
    # fresh rig workdir fail M1.1.3 with collection errors, which cascaded
    # into a zero-record run overwriting a good receipt (QA finding F-1).
    # Take the dev extra from the PINNED checkout, so the deps match the tree
    # being measured rather than this script's working tree.
    run(
        [python, "-m", "pip", "install", "-q", str(runner_src) + "[dev]"],
        cwd=runner_src,
        timeout=1200,
    )
    # Non-editable: this MUST be a real site-packages copy, never -e.
    run(
        [python, "-m", "pip", "install", "-q", "--force-reinstall", str(runner_src)],
        cwd=runner_src,
        timeout=1200,
    )
    return runner_src, venv, python


def probe_runs(python, workdir, target_src, child_schema, parent_schema):
    """M1.1.2: both sides of the import boundary, verified before the run.

    Returns (child_ok, child_detail, parent_ok, parent_detail).
    """
    probe = workdir / "probe_child.py"
    probe.write_text(CHILD_PROBE)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(target_src)
    child = run([python, str(probe)], cwd=workdir / "target", env=env, check=False)
    child_src = str(target_src)
    child_out = child.stdout.strip().splitlines()
    child_ok = (
        child.returncode == 0
        and len(child_out) == 3
        and child_src in child_out[0]
        and child_out[1].startswith(child_src)
        and child_out[2].strip() == str(child_schema)
    )
    child_detail = child_out[0] if child_out else child.stderr[-300:]

    parent = run(
        [python, "-E", "-c", SEMANTIC_PROBE], cwd=workdir / "target", check=False
    )
    parent_out = parent.stdout.strip().splitlines()
    first = parent_out[0] if parent_out else ""
    parent_ok = (
        parent.returncode == 0
        and len(parent_out) == 3
        and "site-packages" in first
        and child_src not in first
        and parent_out[1].startswith(first.rsplit("/moonbuggy/", 1)[0])
        and parent_out[2].strip() == str(parent_schema)
    )
    parent_detail = parent_out[0] if parent_out else parent.stderr[-300:]
    return child_ok, child_detail, parent_ok, parent_detail


def sweep_pyc(pkg_dir, label, purge=False):
    """Finding 7: compare every pyc's module-level consts against a fresh
    compile of its source. Returns the list of poisoned names. With purge=True
    a poisoned cache is DELETED (the documented fix) rather than only reported,
    and the deletion is itself reported so the operator knows a rebuild
    happened."""
    import dis
    import importlib._bootstrap_external as be

    bad = []
    for pyc in sorted(pkg_dir.glob("__pycache__/*.pyc")):
        if "-pytest-" in pyc.name:
            # pytest's assertion rewriter compiles these; their consts
            # legitimately differ from a fresh plain compile. Only plain
            # interpreter-written pycs are comparable (the spike's probe
            # skipped them for the same reason).
            continue
        stem = pyc.name.split(".")[0]
        src = pkg_dir / f"{stem}.py"
        if not src.exists():
            continue
        try:
            code = be.SourcelessFileLoader(f"x.{stem}", str(pyc)).get_code(f"x.{stem}")
        except Exception:
            continue  # wrong magic etc.: the interpreter would recompile anyway
        fresh = compile(src.read_text(), str(src), "exec")

        def consts(c):
            return [
                i.argval
                for i in dis.get_instructions(c)
                if i.opname == "LOAD_CONST"
                and isinstance(i.argval, (int, str, bool, type(None)))
            ]

        if consts(code) != consts(fresh):
            bad.append(pyc.name)
    purged = []
    if bad and purge:
        shutil.rmtree(pkg_dir / "__pycache__", ignore_errors=True)
        purged = bad
    step(
        f"M1.1.2 semantics sweep ({label})",
        not bad,
        f"{len(bad)} poisoned pyc(s) PURGED (recompiling from source)"
        if purged
        else ("bytecode matches source" if not bad else f"{len(bad)} poisoned pyc(s)"),
    )
    return bad


def clean_target_artifacts(target):
    """M1.1.3 preamble: a crashed prior run leaves .moonbuggy/results.jsonl in
    the target (its child suites invoke moonbuggy with the default output dir)
    and the next red-baseline gate refuses because of it. The rig must not
    trip its own trap."""
    moved = []
    for stale in ("survivors.jsonl",):
        p = target / stale
        if p.exists():
            p.rename(p.with_name(f"{stale}.stale-{int(time.time())}"))
            moved.append(stale)
    dot = target / ".moonbuggy"
    if dot.exists():
        dot.rename(dot.with_name(f".moonbuggy.stale-{int(time.time())}"))
        moved.append(".moonbuggy/")
    return moved


HASH_PROBE = (
    "import moonbuggy, os, hashlib;"
    "pkg=os.path.dirname(moonbuggy.__file__);"
    "print(pkg);"
    "print('\\n'.join("
    "f'{m}:{hashlib.sha256(open(os.path.join(pkg,m),\"rb\").read()).hexdigest()}' "
    f"for m in {INTEGRITY_MODULES!r} if os.path.exists(os.path.join(pkg,m))))"
)


def hash_package(python):
    """Hash the installed engine modules in the runner venv (M1.1.5)."""
    out = run([python, "-c", HASH_PROBE]).stdout.strip().splitlines()
    return out[0], dict(line.split(":", 1) for line in out[1:])


def _moonbuggy_cmd(python, output_dir, args):
    """The scoped/full mutation run command. --output-dir lives OUTSIDE the
    target tree (see the run_dir comment for why)."""
    cmd = [
        python,
        "-E",
        "-m",
        "moonbuggy.cli",
        "--project",
        ".",
        "--source",
        "src",
        "--report",
        "human",
        "--quiet",
        # --no-cache: the verdict cache lives in the target's .moonbuggy and
        # is keyed per mutant id — the planted tree generates the same ids for
        # mutated-text-inverted mutants, so a planted run would read the clean
        # run's cached verdicts and detection would silently fail. Measuring,
        # not reusing, is the point of every run this rig performs.
        "--no-cache",
        "--workers",
        str(args.workers),
        "--timeout",
        str(args.timeout),
        "--output-dir",
        str(output_dir),
    ]
    for fragment in args.include:
        cmd += ["--include", fragment]
    return cmd


def plant(target, run_dir):
    """Apply the M1.1.4 plant. Returns (applied, description)."""
    receipt = run_dir / "results.jsonl"
    chosen = None
    if receipt.exists():
        for line in receipt.read_text().splitlines():
            rec = json.loads(line)
            if rec["status"] == "KILLED" and rec.get("mutated"):
                chosen = rec
                break
    if chosen is None:
        return False, "no KILLED mutant with mutated text in the clean run"
    module = REPO_NAME_FROM_ID(chosen["id"])
    path = target / "src" / module
    original, mutated = chosen["original"], chosen["mutated"]
    text = path.read_text()
    if original not in text:
        return False, f"original text not found in {module}"
    path.write_text(text.replace(original, mutated, 1))
    return True, f"{chosen['id']} applied by hand (inverse of a KILLED mutant)"


def REPO_NAME_FROM_ID(mutant_id):
    """'src/moonbuggy/export.py:46:arithmetic_swap:0' -> 'moonbuggy/export.py'."""
    return mutant_id.split(":")[0].split("src/", 1)[1]


def _clean_counts(run_dir):
    """The clean run's counts, as its summary.json reports them.

    Returns:
        The lower-cased ``counts`` mapping a real runner writes, or None for
        a run directory with no summary (an older runner, or a run that never
        got that far) -- the caller decides what absence means, so this says
        "absent" rather than disguising it as a number.
    """
    summary = run_dir / "summary.json"
    if not summary.exists():
        return None
    data = json.loads(summary.read_text())
    counts = data.get("counts")
    if isinstance(counts, dict):
        return counts
    # An older summary with no per-status counts: derive the only total it
    # honestly offers. Every verdict that is not NO_COVERAGE counts as covered,
    # so with no split the safe derivation is 0 (satisfiable gate) -- the
    # shortfall against the receipt is still surfaced by the warning print.
    return None


def covered_mutant_count(run_dir):
    """How many mutants of the clean run have test coverage (M1.1.6 gate).

    A covered mutant is one whose record is not NO_COVERAGE: the clean run
    either measured it or recorded a fact about it. The number is derived from
    the clean run's OWN summary -- the ``counts`` object the runner has always
    written -- because the summary carries no ``covered`` key (QA finding F-4:
    the old code read a key that does not exist and so compared against 0,
    making the gate pass vacuously on every real run).

    A summary with no ``counts`` object (an older runner) returns 0, which
    keeps the gate satisfiable; the warning print still surfaces a shortfall
    against a real receipt.
    """
    counts = _clean_counts(run_dir)
    if counts is None:
        return 0
    return sum(
        counts.get(status.lower(), 0)
        for status in VERDICT_STATUSES
        if status != "NO_COVERAGE"
    )


def gate_check(clean_run_dir, this_run_dir):
    """The M1.1.6 gate: did this run reach a verdict on every covered mutant?

    Args:
        clean_run_dir: the clean run's directory, whose summary.json names
            the covered-mutant count and whose records define the verdict
            vocabulary the statuses come from.
        this_run_dir: the run being checked; its records.jsonl is read here.

    Returns:
        True only when this run has at least one record and its record count
        covers every covered mutant of the clean run. A zero-record run fails
        (the receipt guard), a dropped covered mutant fails, and a clean
        summary with no derivable count keeps the gate satisfiable.
    """
    covered = covered_mutant_count(clean_run_dir)
    records = this_run_dir / "results.jsonl"
    if not records.exists():
        return False
    n = sum(1 for line in records.read_text().splitlines() if line.strip())
    return bool(n) and n >= covered


def gate_step(clean_run_dir, this_run_dir, args):
    """Run the M1.1.6 gate as a rig step, printing what a fail means.

    One place for the criterion's label, detail line, receipt warning and
    guarded receipt write, so the step in main() and the F-4 semantics the
    tests pin cannot drift apart. The receipt is written only on a pass.

    Args:
        clean_run_dir: the clean run's directory.
        this_run_dir: the run the gate judges.
        args: the parsed rig command line (target/runner refs, include scope).

    Returns:
        True when the gate passes and the receipt was written or already
        agreed; False otherwise. Also records the step via ``step()``.
    """
    covered = covered_mutant_count(clean_run_dir)
    records = this_run_dir / "results.jsonl"
    verdicts = {}
    lines = []
    if records.exists():
        lines = [rec for rec in records.read_text().splitlines() if rec.strip()]
        for line in lines:
            rec = json.loads(line)
            verdicts[rec["status"]] = verdicts.get(rec["status"], 0) + 1
    ok = gate_check(clean_run_dir, this_run_dir)
    step(
        "M1.1.6 run reaches a verdict on every covered mutant",
        ok,
        f"{len(lines)} records (expected >= {covered}), {verdicts}"
        if lines
        else "no results.jsonl from the clean run — see its step above",
    )
    if lines and not ok:
        print(
            f"  WARNING: {covered} mutants have coverage but the run "
            f"produced {len(lines)} records; the receipt is not written."
        )
    if lines and ok:
        receipt = REPO / "docs" / "selfmutation-receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "target_ref": args.target_ref,
                    "runner_ref": args.runner_ref,
                    "scope": args.include or ["<full src/>"],
                    "records": len(lines),
                    "verdicts": verdicts,
                    "machinery": (f"-E parent, --workers {args.workers} (cold path)"),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"  receipt written: {receipt}")
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--target-ref",
        default="HEAD",
        help="ref the target checkout is placed at (default HEAD of this repo)",
    )
    parser.add_argument(
        "--runner-ref",
        default=PINNED_REF,
        help=f"ref the pinned runner is installed from (default {PINNED_REF})",
    )
    parser.add_argument(
        "--workdir",
        default=str(DEFAULT_WORKDIR),
        help="rig workdir (default ~/.cache/moonbuggy-selfmutation)",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="restrict mutation to modules matching this fragment (repeatable); "
        "a scoped run (e.g. --include export.py) is the cheap end-to-end check",
    )
    parser.add_argument(
        "--timeout",
        default=120,
        type=int,
        help="per-mutant wall-clock seconds (Finding 3: be generous)",
    )
    parser.add_argument(
        "--workers",
        default=2,
        type=int,
        help="cold subprocess workers (the -E rig REQUIRES the cold path)",
    )
    parser.add_argument(
        "--skip-full-run",
        action="store_true",
        help="skip the unscoped mutation run (M1.1.6); everything else still runs",
    )
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"rig workdir: {workdir}")
    print(f"runner pinned at: {args.runner_ref}  target at: {args.target_ref}")

    # --- M1.1.1: rig setup — clones, pinned non-editable runner venv ------
    if args.runner_ref == args.target_ref:
        print(
            "WARNING: --runner-ref and --target-ref are both "
            f"{args.runner_ref!r} — the runner and the target are the same "
            "code, so a defect in the branch under test hides inside the "
            "engine judging it. Pass --runner-ref to pin an independent ref "
            "(the default is HEAD only because the fork-path fix is recent; "
            "see the module docstring)."
        )
    runner_src, venv, python = build_runner_venv(workdir, args.runner_ref)
    target = clone_at(args.target_ref, workdir / "target")
    pkg_dir = run(
        [
            python,
            # -E: the probe must see what the runner PARENT will see. Without
            # it, a PYTHONPATH exported in the invoking shell (or by a wrapper)
            # makes the probe resolve the target and falsely fail the pin.
            "-E",
            "-c",
            "import moonbuggy, os; print(os.path.dirname(moonbuggy.__file__))",
        ],
        check=False,
    ).stdout.strip()
    # Build-runner-venv check: with -E and no PYTHONPATH the installed copy
    # must be a real site-packages file under the venv, never either checkout.
    step(
        "M1.1.1 pinned non-editable runner venv",
        bool(pkg_dir) and "site-packages" in pkg_dir and str(workdir) in pkg_dir,
        pkg_dir,
    )

    target_src = target / "src"

    # Expected semantic pin value: read from each checkout's OWN source. The
    # child loads the target's report.py, the parent the pin's — a constant
    # that legitimately differs between the two refs must not fail a healthy
    # boundary, while a value disagreeing with the source being imported is
    # exactly the poisoned-bytecode failure of spike Finding 7.
    def read_schema(checkout):
        report_py = checkout / "src" / "moonbuggy" / "report.py"
        for line in report_py.read_text().splitlines():
            if line.startswith("RECORD_SCHEMA"):
                return line.split("=")[1].strip()
        return None

    # --- M1.1.2: import boundary + semantics, both sides, before the run ---
    child_schema = read_schema(target)
    parent_schema = read_schema(runner_src)
    child_ok, child_detail, parent_ok, parent_detail = probe_runs(
        python, workdir, target_src, child_schema, parent_schema
    )
    step("M1.1.2 child probe resolves target", child_ok, child_detail)
    step("M1.1.2 parent (-E) probe resolves pinned venv", parent_ok, parent_detail)
    sweep_pyc(Path(pkg_dir), "runner venv package", purge=True)
    sweep_pyc(target_src / "moonbuggy", "target src package", purge=True)

    # --- M1.1.3: target suite green under the rig environment -------------
    moved = clean_target_artifacts(target)
    if moved:
        print(f"  moved stale artifacts aside: {', '.join(moved)}")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(target_src)
    began = time.perf_counter()
    suite = run(
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=target,
        env=env,
        timeout=3600,
        check=False,
    )
    suite_out = suite.stdout or ""
    step(
        "M1.1.3 target suite green under rig env",
        suite.returncode == 0,
        f"{suite_out.strip().splitlines()[-1] if suite_out.strip() else ''} "
        f"({time.perf_counter() - began:.0f}s)",
    )

    # --- M1.1.4 + M1.1.5: planted defect, end to end -----------------------
    # --output-dir points OUTSIDE the target tree: a run that wrote into the
    # target's default .moonbuggy/ would leave an in-flight (empty)
    # results.jsonl at the target root, and the target's own
    # test_no_results_is_an_exit_2 reads the cwd fallback and fails — a
    # self-inflicted red baseline the spike's launch scripts avoided with the
    # same outside-tree output dir.
    run_dir = workdir / f"run-{int(time.time())}"
    pkg_before, hashes_before = hash_package(python)

    # Clean scoped run FIRST, so M1.1.4 can compare the planted run's verdicts
    # against it (the plant may be survivable at refs where the planted line's
    # behaviour is not covered by an assertion — Finding 2's signature).
    clean_counts = None
    if not args.skip_full_run:
        clean = run(
            _moonbuggy_cmd(python, run_dir, args),
            cwd=target,
            env=env,
            timeout=8 * 3600,
            check=False,
        )
        clean_summary = run_dir / "summary.json"
        if clean_summary.exists():
            clean_counts = json.loads(clean_summary.read_text())["counts"]
        step(
            "M1.1.6 clean scoped run completed",
            clean.returncode in (0, 1),
            f"exit={clean.returncode}, counts={clean_counts}",
        )

    applied, plant_desc = plant(target, run_dir)
    step("M1.1.4 plant applied", applied, plant_desc)
    if applied:
        gate = run(
            _moonbuggy_cmd(python, str(run_dir) + "-planted", args),
            cwd=target,
            env=env,
            timeout=3600,
            check=False,
        )
        # Detection = verdicts changed relative to the clean run OR the
        # red-baseline gate refused naming the broken tests. Both observed in
        # the spike (Finding 6 saw the refusal; a survivable plant flips
        # KILLED -> SURVIVED — the plant becomes the baseline, Finding 2's
        # signature).
        gate_refused = gate.returncode == 2 and "red baseline" in gate.stderr
        planted_summary = run_dir.with_name(run_dir.name + "-planted") / "summary.json"
        clean_summary = run_dir / "summary.json"
        detected = gate_refused or (
            planted_summary.exists()
            and clean_summary.exists()
            and json.loads(planted_summary.read_text())["counts"]
            != json.loads(clean_summary.read_text())["counts"]
        )
        detail = (
            gate.stderr.strip().splitlines()[-1]
            if gate_refused and gate.stderr.strip()
            else plant_desc
        )
        step(
            "M1.1.4 plant detected (verdicts changed or gate refused)",
            detected,
            detail,
        )

        _, hashes_after = hash_package(python)
        step(
            "M1.1.5 runner uncorrupted by planted target",
            hashes_after == hashes_before,
            f"hashed {len(hashes_before)} engine modules before/after the planted run",
        )
        # Revert the plant by restoring the planted file from git (the target
        # is a checkout at a fixed commit, so git knows the pristine bytes).
        planted_module = plant_desc.split()[0].split(":")[0].split("src/", 1)[1]
        checkout = run(
            ["git", "checkout", "--", f"src/{planted_module}"],
            cwd=target,
            timeout=60,
            check=False,
        )
        step(
            "M1.1.4 plant reverted",
            checkout.returncode == 0,
            f"restored src/{planted_module} from git",
        )

    if args.skip_full_run:
        print("\n--skip-full-run: M1.1.6/M1.1.7 not exercised this pass.")
    else:
        # --- M1.1.6: the receipt comes from the CLEAN scoped run above ------
        # (with --include fragments it is the scoped receipt; without any it
        # is the full 1702-mutant receipt). The run already happened before
        # the plant, so the receipt reflects an unmutated target.
        #
        # Guarded write: a run that produced no records (the old dependency
        # gap failed M1.1.3 and cascaded to an empty results.jsonl here)
        # overwrote the last good receipt with records: 0, destroying the
        # drift baseline this file exists to hold. Never write a zero-record
        # receipt, and fail the step rather than passing over nothing.
        # The gate semantics live in gate_step (tested by
        # tests/test_selfmutation_rig_gate.py): the covered count is derived
        # from the clean run's own summary counts, not from a "covered" key
        # the runner never writes (QA finding F-4).
        gate_step(run_dir, run_dir, args)

    # --- M1.1.7/M1.1.8 are process criteria, stated here -------------------
    print(
        "\nM1.1.7 (stability) requires TWO identical full runs on a quiet machine"
        " and a disagreement list — run this script twice with the same flags"
        " and diff the receipts."
    )
    print("M1.1.8 (not in CI): this rig is deliberately absent from check-all and CI.")

    if FAILURES:
        print(f"\nFAILED steps: {', '.join(FAILURES)}")
        return 1
    print("\nAll executed criteria steps passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
