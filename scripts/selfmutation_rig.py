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
the rig. The rig is deliberately NOT wired into CI (M1.1.8): the stability
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

# The plant used for M1.1.4/M1.1.5. The same edit moonbuggy's own
# condition_negation operator generates at export.py:83. With the plant live
# the red-baseline gate refuses to start naming the three test_survivor_export
# tests (spike Finding 6) — that is detection, and the refusal is the correct
# outcome because the gate's verdicts would be meaningless.
PLANT_LINE = 'if record["accepted"]:'
PLANT_MUTATED = 'if not record["accepted"]:'

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
    """Clone the repo at a ref into dest (a separate checkout by construction)."""
    if dest.exists():
        run(["git", "fetch", "--tags"], cwd=dest, timeout=600)
        run(["git", "checkout", ref], cwd=dest, timeout=600)
        run(["git", "reset", "--hard", ref], cwd=dest, timeout=600)
        return dest
    run(["git", "clone", "--no-checkout", str(repo), str(dest)], timeout=1200)
    run(["git", "checkout", ref], cwd=dest, timeout=600)
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


def plant(target):
    """Apply the M1.1.4 plant. Returns (applied, original_line)."""
    export = target / "src" / "moonbuggy" / "export.py"
    lines = export.read_text().splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if PLANT_LINE in line]
    if not hits:
        return False, None
    i = hits[0]
    lines[i] = lines[i].replace(PLANT_LINE, PLANT_MUTATED)
    export.write_text("".join(lines))
    return True, lines[i]


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
    runner_src, venv, python = build_runner_venv(workdir, args.runner_ref)
    target = clone_at(args.target_ref, workdir / "target")
    pkg_dir = run(
        [
            python,
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
    pkg_before, hashes_before = hash_package(python)
    applied, _ = plant(target)
    step("M1.1.4 plant applied", applied, "export.py condition_negation hand-edit")
    if applied:
        gate = run(
            [
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
                *(f"--include {f}" for f in args.include),
            ],
            cwd=target,
            env=env,
            timeout=3600,
            check=False,
        )
        # Detection = verdicts changed OR the red-baseline gate refused naming
        # the broken tests (spike Finding 6: with the plant live the gate's
        # refusal is the correct outcome).
        detected = gate.returncode != 0 and (
            "test_survivor_export" in gate.stderr or "FAILED" in gate.stderr
        )
        step(
            "M1.1.4 plant detected (verdicts changed or gate refused)",
            detected,
            gate.stderr.strip().splitlines()[-1]
            if gate.stderr.strip()
            else "run was clean?!",
        )

        _, hashes_after = hash_package(python)
        step(
            "M1.1.5 runner uncorrupted by planted target",
            hashes_after == hashes_before,
            f"hashed {len(hashes_before)} engine modules before/after the planted run",
        )
        # Revert the plant.
        export = target / "src" / "moonbuggy" / "export.py"
        text = export.read_text()
        export.write_text(text.replace(PLANT_MUTATED, PLANT_LINE))
        step("M1.1.4 plant reverted", PLANT_MUTATED not in export.read_text())

    if args.skip_full_run:
        print("\n--skip-full-run: M1.1.6/M1.1.7 not exercised this pass.")
    else:
        # --- M1.1.6: the full run, cold path, every covered mutant judged --
        clean_target_artifacts(target)
        began = time.perf_counter()
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
            "--workers",
            str(args.workers),
            "--timeout",
            str(args.timeout),
        ]
        for fragment in args.include:
            cmd += ["--include", fragment]
        full = run(cmd, cwd=target, env=env, timeout=8 * 3600, check=False)
        results = target / ".moonbuggy" / "results.jsonl"
        ok = full.returncode == 0 and results.exists()
        verdicts = {}
        if ok:
            text = results.read_text()
            records = [json.loads(rec) for rec in text.splitlines() if rec.strip()]
            for r in records:
                verdicts[r["status"]] = verdicts.get(r["status"], 0) + 1
            elapsed = time.perf_counter() - began
            step(
                "M1.1.6 run reaches a verdict on every covered mutant",
                ok,
                f"{len(records)} records, {verdicts}, {elapsed:.0f}s",
            )
            receipt = REPO / "docs" / "selfmutation-receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "target_ref": args.target_ref,
                        "runner_ref": args.runner_ref,
                        "records": len(records),
                        "verdicts": verdicts,
                        "machinery": f"-E parent, --workers {args.workers} (cold path)",
                    },
                    indent=2,
                )
                + "\n"
            )
            print(f"  receipt written: {receipt}")
        else:
            step(
                "M1.1.6 run reaches a verdict on every covered mutant",
                False,
                f"exit={full.returncode}; {full.stderr[-300:] if full.stderr else ''}",
            )

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
