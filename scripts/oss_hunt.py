"""Milestone M4: run moonbuggy against real open-source libraries.

Run: .venv/bin/python scripts/oss_hunt.py   (or `make oss-hunt`)

**Standing constraint: nothing is posted anywhere.** No issues, no pull
requests, no discussions, no emails. This script clones public repositories
read-only and writes findings to a local file for review. It has no network
write path and must never grow one.

What the harness guarantees, and why each guarantee is here:

- **Pinned.** Each target is cloned at a specific tag, recorded in the output.
  A finding about "more-itertools" that cannot say which more-itertools is not
  a finding, it is an anecdote.
- **Isolated.** Each target gets its own virtualenv with its own dependencies.
  Sharing one environment across five libraries means the first conflicting
  requirement silently changes what the others are tested against.
- **Green first (M4.1).** The project's own suite must pass before anything is
  mutated. Against a red baseline every mutant covered by a failing test is
  reported KILLED regardless of the mutation, so a red baseline does not
  produce weak results -- it produces flattering ones.
- **Bounded.** Mutating an entire library is hours of work and produces more
  survivors than anyone will triage. Each target names the modules to mutate,
  and that choice is recorded in the output so nobody mistakes a sample for a
  census.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Deliberately OUTSIDE the repository. Keeping the checkouts inside it put each
# target underneath moonbuggy's own pyproject.toml, which pytest then chose as
# rootdir -- and that produced node ids no run could resolve. The bug that
# exposed is fixed (see tests/test_rootdir.py), but a harness that arranges its
# subjects inside another project is still measuring an unusual case for no
# reason.
WORKDIR = Path(
    os.environ.get("MOONBUGGY_OSS_WORKDIR", Path.home() / ".cache" / "moonbuggy-oss")
)


@dataclass
class Target:
    """One library to mutate, pinned and described."""

    name: str
    repo: str
    tag: str
    source: str
    """Directory to mutate, relative to the checkout."""
    modules: list = field(default_factory=list)
    """Path fragments to restrict mutation to. Empty means the whole source
    directory. Recorded in the output: a bounded run must not read as a full
    one."""
    install: list = field(default_factory=lambda: ["-e", "."])
    extra_requirements: list = field(default_factory=list)
    test_args: list = field(default_factory=list)
    """Extra pytest arguments this project's own test command uses. Passed to
    the green-baseline check AND to every moonbuggy run, because measuring a
    project against a smaller suite than it really runs inflates its survivor
    count with mutants its own CI would catch."""
    note: str = ""


# Pinned at the latest release as of 2026-08-15. Substitutions are allowed by
# M4 if a target proves unsuitable, with the reason recorded -- see the
# `unsuitable` entries in docs/oss-findings.md if any appear there.
TARGETS = [
    Target(
        name="tomli",
        repo="https://github.com/hukkin/tomli.git",
        tag="2.4.1",
        source="src/tomli",
        note="Pure-Python TOML parser. Small, deterministic, no dependencies.",
    ),
    Target(
        name="humanize",
        repo="https://github.com/python-humanize/humanize.git",
        tag="4.16.0",
        source="src/humanize",
        modules=["number.py", "filesize.py"],
        # pytest-benchmark is needed by tests/test_benchmarks.py. Without it
        # that file errors at collection, the baseline is not green, and M4.1
        # correctly refuses the whole target -- which was a harness omission
        # rather than anything wrong with humanize.
        extra_requirements=["freezegun", "pytest-benchmark"],
        note="Number and filesize formatting; the two modules with the most "
        "branch-heavy arithmetic.",
    ),
    Target(
        name="sqlparse",
        repo="https://github.com/andialbrecht/sqlparse.git",
        tag="0.6.0",
        source="sqlparse",
        modules=["sql.py", "utils.py", "tokens.py"],
        note="SQL parser. Mutating the whole engine is far more than can be "
        "triaged, so the core token and utility modules are the sample.",
    ),
    Target(
        name="more-itertools",
        repo="https://github.com/more-itertools/more-itertools.git",
        tag="v11.1.0",
        source="more_itertools",
        modules=["recipes.py"],
        note="recipes.py is the itertools-recipes half of the library: many "
        "small pure functions, which is the shape mutation testing has "
        "the most to say about.",
    ),
    Target(
        name="boltons",
        repo="https://github.com/mahmoud/boltons.git",
        tag="26.1.0",
        source="boltons",
        modules=["iterutils.py", "mathutils.py", "strutils.py"],
        # boltons' tox command is `pytest --doctest-modules ...`, and its
        # docstrings carry real assertions. Running bare pytest measures it
        # against a suite it does not actually use, and the first run did:
        # four of its survivors were killed by doctests we had not run.
        # Their tox command names the paths as well as the flag. Without them
        # pytest collects doctests from the whole repository -- docs, setup
        # helpers, misc scripts -- and the baseline fails for reasons that have
        # nothing to do with boltons' library code.
        test_args=["--doctest-modules", "boltons", "tests"],
        note="Utility collection. Three modules chosen for having dedicated "
        "test files and no I/O. Its own test command enables doctests, "
        "so ours does too.",
    ),
]

VENV_PYTHON = "bin/python"


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


def prepare(target, root):
    """Clone at the pinned tag and build an isolated venv. Returns (dir, python)."""
    checkout = root / target.name
    if not checkout.exists():
        run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                target.tag,
                target.repo,
                str(checkout),
            ],
            timeout=600,
        )

    venv = checkout / ".venv"
    if not (venv / VENV_PYTHON).exists():
        run([sys.executable, "-m", "venv", str(venv)], timeout=600)

    python = str(venv / VENV_PYTHON)
    run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"], timeout=600)
    run(
        [
            python,
            "-m",
            "pip",
            "install",
            "-q",
            "pytest",
            "pytest-cov",
            *target.extra_requirements,
        ],
        timeout=1200,
    )
    run(
        [python, "-m", "pip", "install", "-q", *target.install],
        cwd=checkout,
        timeout=1200,
    )
    # moonbuggy itself, from this working tree rather than from PyPI, so the
    # findings are about the code that produced them.
    run([python, "-m", "pip", "install", "-q", "-e", str(REPO)], timeout=1200)
    return checkout, python


def baseline_is_green(checkout, python, target):
    """M4.1: the project's own suite must pass before anything is mutated."""
    began = time.perf_counter()
    proc = run(
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *target.test_args],
        cwd=checkout,
        timeout=1800,
        check=False,
    )
    return proc.returncode == 0, time.perf_counter() - began, proc.stdout[-3000:]


def mutate(checkout, python, target, timeout):
    """Run moonbuggy over the chosen modules. Returns (seconds, records)."""
    command = [
        python,
        "-m",
        "moonbuggy.cli",
        "--no-cache",
        "--quiet",
        "--source",
        str(checkout / target.source),
        "--timeout",
        str(timeout),
    ]
    for fragment in target.modules:
        command += ["--include", fragment]
    for extra in target.test_args:
        # `=` form, not two arguments: argparse reads `--pytest-arg
        # --doctest-modules` as two options rather than an option and its value.
        command.append(f"--pytest-arg={extra}")

    began = time.perf_counter()
    proc = run(command, cwd=checkout, timeout=7200, check=False)
    elapsed = time.perf_counter() - began

    results = checkout / ".moonbuggy" / "results.jsonl"
    if not results.exists():
        raise RuntimeError(
            f"moonbuggy produced no results for {target.name}:\n"
            f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
        )
    records = [
        json.loads(line) for line in results.read_text().splitlines() if line.strip()
    ]
    return elapsed, records, proc.stderr[-3000:]


def moonbuggy_version():
    commit = run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO).stdout.strip()
    dirty = run(["git", "status", "--porcelain"], cwd=REPO).stdout.strip()
    sys.path.insert(0, str(REPO / "src"))
    from moonbuggy import __version__

    return f"{__version__}+{commit}{'-dirty' if dirty else ''}"


def hunt(target, root, timeout):
    """Everything for one target. Returns a record dict, never raises."""
    entry = {
        "name": target.name,
        "tag": target.tag,
        "repo": target.repo,
        "modules": target.modules or ["<all>"],
        "note": target.note,
    }
    # A blocked target is data, not a crash -- deliberately broad.
    try:
        checkout, python = prepare(target, root)
    except Exception as error:
        entry["blocker"] = f"could not prepare: {error}"
        return entry

    green, baseline_seconds, output = baseline_is_green(checkout, python, target)
    entry["baseline_seconds"] = round(baseline_seconds, 2)
    if not green:
        # M4.1: a red baseline invalidates every result from this target, so no
        # mutation is attempted and nothing is reported for it.
        entry["blocker"] = "the project's own suite is not green at this tag"
        entry["baseline_output"] = output
        return entry

    try:
        elapsed, records, stderr = mutate(checkout, python, target, timeout)
    except Exception as error:
        entry["blocker"] = f"mutation run failed: {error}"
        return entry

    counts = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    killed = counts.get("KILLED", 0)
    scored = killed + counts.get("SURVIVED", 0)

    entry.update(
        {
            "mutants": len(records),
            "statuses": counts,
            "wall_seconds": round(elapsed, 2),
            # The conventional definition: killed over killed-plus-survived.
            # TIMEOUT and SUSPICIOUS are excluded because neither is evidence
            # either way about whether the suite catches the change.
            "mutation_score": round(killed / scored, 4) if scored else None,
            "survivors": [r for r in records if r["status"] == "SURVIVED"],
            "stderr_tail": stderr,
        }
    )
    return entry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--only", action="append", default=[], help="run just this target (repeatable)"
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="per-mutant timeout in seconds"
    )
    parser.add_argument(
        "--out",
        default=str(REPO / "docs" / "oss-run.json"),
        help="where to write the machine-readable run record",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the checkouts for hand verification (M4.7)",
    )
    args = parser.parse_args(argv)

    targets = [t for t in TARGETS if not args.only or t.name in args.only]
    WORKDIR.mkdir(parents=True, exist_ok=True)

    version = moonbuggy_version()
    print(f"moonbuggy {version}")
    print("Nothing is reported upstream. Findings stay in this repository.\n")

    entries = []
    for target in targets:
        print(f"--- {target.name} @ {target.tag}")
        entry = hunt(target, WORKDIR, args.timeout)
        entries.append(entry)
        if "blocker" in entry:
            print(f"    BLOCKED: {entry['blocker']}\n")
            continue
        print(
            f"    {entry['mutants']} mutants in {entry['wall_seconds']}s, "
            f"score {entry['mutation_score']}, "
            + "  ".join(f"{k}={v}" for k, v in sorted(entry["statuses"].items()) if v)
            + "\n"
        )

    payload = {"moonbuggy": version, "targets": entries}
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"run record -> {args.out}")

    if not args.keep:
        print(f"checkouts kept at {WORKDIR} for hand verification (M4.7)")


if __name__ == "__main__":
    main()
