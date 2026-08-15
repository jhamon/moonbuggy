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

from . import forkserver
from .inmemory import install
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
    from_cache: bool = False
    """Runtime metadata, deliberately kept OUT of the JSONL record: criterion F3
    requires a fully cached run's output to match a cold run's, and a field that
    differs by definition would defeat that check."""


def run_mutants(
    project_dir, mutants, linemap, timeout=30, python=None, xdist_workers=0,
    cache=None, use_fork=None, jobs=None,
):
    """Run every mutant against its selected tests. Returns a list of Results."""
    project_dir = Path(project_dir)
    python = python or sys.executable

    # Forking is the fast path and the default where it exists. xdist needs real
    # subprocesses, so asking for workers opts out.
    if use_fork is None:
        use_fork = forkserver.available() and not xdist_workers
    if jobs is None:
        # Leave a core for the parent and whatever else the machine is doing.
        jobs = max(1, (os.cpu_count() or 2) - 1)
    if use_fork:
        forkserver.warm_up()
        return _run_forked_batch(project_dir, mutants, linemap, timeout, cache, jobs)

    return [
        run_one(project_dir, mutant, linemap, timeout, python, xdist_workers, cache, use_fork)
        for mutant in mutants
    ]


def _run_forked_batch(project_dir, mutants, linemap, timeout, cache, concurrency):
    """Resolve cache hits and trivial cases first, then fork the rest in parallel."""
    results = {}
    keys = {}
    to_run = []

    for index, mutant in enumerate(mutants):
        if mutant.suppressed:
            results[index] = Result(mutant, "SKIPPED", 0, 0.0)
            continue

        selected = sorted(linemap.select_for(mutant))
        if cache is not None:
            keys[index] = cache.key_for(mutant, project_dir, selected)
            hit = cache.get(keys[index])
            if hit is not None:
                results[index] = Result(
                    mutant, hit["status"], hit["tests_run"], 0.0,
                    nearest_test=hit["nearest_test"], from_cache=True,
                )
                continue

        if not selected:
            results[index] = Result(mutant, "SURVIVED", 0, 0.0, nearest_test=None)
        else:
            to_run.append((index, mutant, selected))

    if to_run:
        started = time.perf_counter()
        statuses = forkserver.run_batch(
            project_dir,
            [(mutant, selected) for _, mutant, selected in to_run],
            timeout,
            _apply_in_child,
            concurrency,
        )
        # Wall clock is shared across concurrent children, so per-mutant duration
        # is an average rather than a measurement. Recorded as such instead of
        # pretending to a precision forking does not allow.
        share = (time.perf_counter() - started) / len(to_run)
        for (index, mutant, selected), status in zip(to_run, statuses):
            results[index] = Result(
                mutant, status, len(selected), share,
                nearest_test=sorted(selected)[0] if status == "SURVIVED" else None,
            )

    if cache is not None:
        for index, result in results.items():
            # Suppressed mutants have no key: they never consult the cache, so
            # storing them would only add entries nothing ever reads.
            if index in keys and not result.from_cache:
                cache.put(keys[index], {
                    "status": result.status,
                    "tests_run": result.tests_run,
                    "nearest_test": result.nearest_test,
                })

    return [results[index] for index in range(len(mutants))]


def run_one(
    project_dir, mutant, linemap, timeout, python, xdist_workers=0, cache=None, use_fork=False
):
    if mutant.suppressed:
        return Result(mutant, "SKIPPED", 0, 0.0)

    selected = sorted(linemap.select_for(mutant))
    nearest = selected[0] if selected else None

    if cache is not None:
        key = cache.key_for(mutant, project_dir, selected)
        hit = cache.get(key)
        if hit is not None:
            return Result(
                mutant,
                hit["status"],
                tests_run=hit["tests_run"],
                duration=0.0,
                nearest_test=hit["nearest_test"],
                from_cache=True,
            )

    if not selected:
        # No test executes this line. Nothing can kill the mutant, so there is
        # nothing to run -- but it is still a finding (an untested line), not an
        # exclusion, so it is reported SURVIVED rather than SKIPPED.
        result = Result(mutant, "SURVIVED", 0, 0.0, nearest_test=None)
    else:
        started = time.perf_counter()
        if use_fork:
            status = forkserver.run_in_fork(
                project_dir, mutant, selected, timeout, _apply_in_child
            )
        else:
            status = _run_pytest(
                project_dir, mutant, selected, timeout, python, xdist_workers
            )
        result = Result(
            mutant,
            status,
            tests_run=len(selected),
            duration=time.perf_counter() - started,
            nearest_test=nearest if status == "SURVIVED" else None,
        )

    if cache is not None:
        cache.put(
            key,
            {
                "status": result.status,
                "tests_run": result.tests_run,
                "nearest_test": result.nearest_test,
            },
        )
    return result


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


def _apply_in_child(mutant):
    """Install the mutation inside a forked child.

    The path is resolved relative to the child's cwd, which forkserver has
    already set to the project root.
    """
    install(str(Path(mutant.module).resolve()), mutant.line, mutant.mutated)


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
