"""The fast path: apply each mutant in memory, run only its covering tests.

Both speed levers from section 4 meet here. Coverage-guided selection decides
WHICH tests run (4.1); the in-memory import hook decides HOW the mutation is
applied (4.2). Neither writes a mutated file to disk.

One process per mutant. That is not merely convenient -- it is what makes the
xdist story work without any cross-process state, since the mutant's identity
travels in the environment and every worker installs it independently. See
docs/spike-a-findings.md.
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .plugin import MUTANT_ENV_VAR

PYTEST_OK = 0
PYTEST_TESTS_FAILED = 1


@dataclass(frozen=True)
class Result:
    mutant: object
    status: str
    tests_run: int
    duration: float
    nearest_test: str | None = None


def run_mutants(project_dir, mutants, linemap, timeout=30, python=None, xdist_workers=0):
    """Run every mutant against its selected tests. Returns a list of Results."""
    project_dir = Path(project_dir)
    python = python or sys.executable
    return [
        run_one(project_dir, mutant, linemap, timeout, python, xdist_workers)
        for mutant in mutants
    ]


def run_one(project_dir, mutant, linemap, timeout, python, xdist_workers=0):
    if mutant.suppressed:
        return Result(mutant, "SKIPPED", 0, 0.0)

    selected = sorted(linemap.select_for(mutant))
    nearest = selected[0] if selected else None

    if not selected:
        # No test executes this line. Nothing can kill the mutant, so there is
        # nothing to run -- but it is still a finding (an untested line), not an
        # exclusion, so it is reported SURVIVED rather than SKIPPED.
        return Result(mutant, "SURVIVED", 0, 0.0, nearest_test=None)

    started = time.perf_counter()
    status = _run_pytest(project_dir, mutant, selected, timeout, python, xdist_workers)
    duration = time.perf_counter() - started

    return Result(
        mutant,
        status,
        tests_run=len(selected),
        duration=duration,
        nearest_test=nearest if status == "SURVIVED" else None,
    )


def _run_pytest(project_dir, mutant, selected, timeout, python, xdist_workers):
    command = [
        python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-p", "moonbuggy.plugin", *selected,
    ]
    if xdist_workers:
        command += ["-n", str(xdist_workers)]

    try:
        proc = subprocess.run(
            command,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_env_for(project_dir, mutant),
        )
    except subprocess.TimeoutExpired:
        # The mutant made something never terminate. Report it and carry on --
        # one hanging mutant must not take down the run.
        return "TIMEOUT"

    if proc.returncode == PYTEST_OK:
        return "SURVIVED"
    if proc.returncode == PYTEST_TESTS_FAILED:
        return "KILLED"
    # pytest could not complete: collection error, internal error, usage error,
    # or nothing collected. Not a clean kill, which is what SUSPICIOUS is for.
    return "SUSPICIOUS"


def _env_for(project_dir, mutant):
    env = dict(os.environ)
    env[MUTANT_ENV_VAR] = json.dumps(
        {
            "path": str((project_dir / mutant.module).resolve()),
            "line": mutant.line,
            "mutated": mutant.mutated,
        }
    )
    return env
