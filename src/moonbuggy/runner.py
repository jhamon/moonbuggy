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
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import forkserver, profiling
from .baseline import check as check_baseline
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
    cache=None, use_fork=None, jobs=None, flaky=(), on_result=None,
):
    """Run every mutant against its selected tests.

    :param flaky: test node ids whose outcome is not reproducible; mutants
        selecting one are settled SUSPICIOUS rather than run (M1.4.3).
    :param on_result: called with each :class:`Result` as it is settled.
    :returns: a list of :class:`Result`, one per mutant, in the input order.
    """
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
        return _run_forked_batch(
            project_dir, mutants, linemap, timeout, cache, jobs, flaky, on_result
        )

    results = []
    for mutant in mutants:
        result = run_one(
            project_dir, mutant, linemap, timeout, python, xdist_workers, cache,
            use_fork, flaky,
        )
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results


def _run_forked_batch(
    project_dir, mutants, linemap, timeout, cache, concurrency, flaky=(), on_result=None,
):
    """Resolve cache hits and trivial cases first, then fork the rest in parallel."""
    plan = _plan(project_dir, mutants, linemap, cache, flaky)
    results, keys, to_run = plan["results"], plan["keys"], plan["to_run"]
    if on_result is not None:
        for result in results.values():
            on_result(result)

    if to_run:
        started = time.perf_counter()
        jobs_for_fork = [(mutant, selected) for _, mutant, selected in to_run]

        # Warm path first: one host imports the suite, grandchildren mutate in
        # place. Falls back to cold forks if the host dies or any mutation
        # cannot be applied in place -- never silently, since an unapplied
        # mutation reports a false SURVIVED.
        statuses = forkserver.run_warm_batch(
            project_dir, jobs_for_fork, timeout, concurrency,
            _warm_up_args(linemap), _apply_in_place,
        )
        if statuses is None:
            statuses = forkserver.run_batch(
                project_dir, jobs_for_fork, timeout, _apply_in_child, concurrency
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
            if on_result is not None:
                on_result(results[index])

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
    project_dir, mutant, linemap, timeout, python, xdist_workers=0, cache=None,
    use_fork=False, flaky=(),
):
    if mutant.suppressed:
        return Result(mutant, "SKIPPED", 0, 0.0)

    selected = sorted(linemap.select_for(mutant))
    nearest = selected[0] if selected else None

    if set(flaky).intersection(selected):
        return Result(mutant, "SUSPICIOUS", len(selected), 0.0)

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


def run_session(
    project_dir, mutants, source_dir, timeout=30, cache=None, jobs=None,
    probes=1, on_result=None,
):
    """Coverage pass and mutant execution in a single warm process.

    The two phases run the same test suite, so running them separately meant
    executing it twice -- over half the wall clock on the benchmark workload.
    Here one forked host runs the suite under coverage, and the same host then
    forks a grandchild per mutant with every test module already imported.

    :param project_dir: project root.
    :param mutants: every mutant to consider, in report order.
    :param source_dir: directory to measure coverage of.
    :param timeout: seconds before one mutant is called TIMEOUT.
    :param cache: a :class:`~moonbuggy.cache.ResultCache`, or None.
    :param jobs: how many mutants to run concurrently.
    :param probes: extra unmutated suite runs used to detect flaky tests.
    :param on_result: called with each :class:`Result` as it is settled, so a
        run killed mid-flight has already emitted what it knew (M1.4.13).
    :returns: ``(linemap, results)``.
    :raises BaselineError: if the suite is already failing or collects nothing.
        Falls back to the separate cold path when the warm host cannot
        complete, so a host failure costs time rather than correctness.
    """
    from .coverage_pass import read_coverage_data, run_baseline_pass

    project_dir = Path(project_dir)
    if jobs is None:
        jobs = max(1, (os.cpu_count() or 2) - 1)

    if not forkserver.available():
        linemap, flaky = run_baseline_pass(project_dir, source_dir, probes)
        return linemap, run_mutants(
            project_dir, mutants, linemap, timeout, cache=cache, jobs=jobs,
            flaky=flaky, on_result=on_result,
        )

    forkserver.warm_up()
    with tempfile.TemporaryDirectory() as tmp:
        data_file = Path(tmp) / "coverage-data"
        os.environ["COVERAGE_FILE"] = str(data_file)
        cov_args = [
            "-q", "-p", "no:cacheprovider",
            f"--cov={source_dir}", "--cov-context=test", "--cov-report=",
        ]
        probe_args = ["-q", "-p", "no:cacheprovider", "-p", "no:cov"]

        state = {}
        profiler = profiling.active()

        def build_jobs(evidence):
            """Runs in the PARENT once the host's baseline runs are done."""
            # The host measured these itself, inside the process that did the
            # work; the parent was blocked on a pipe for the whole interval and
            # can only see the sum.
            profiler.add("warm-session startup", evidence.get("startup", 0.0))
            profiler.add("coverage pass", evidence.get("coverage_seconds", 0.0))
            profiler.add("coverage pass", evidence.get("probe_seconds", 0.0))

            with profiler.span("planning"):
                state["flaky"] = check_baseline(evidence["runs"])
                state["linemap"] = read_coverage_data(data_file, project_dir)
                state["plan"] = _plan(
                    project_dir, mutants, state["linemap"], cache, state["flaky"]
                )
                if on_result is not None:
                    for result in state["plan"]["results"].values():
                        on_result(result)
                return [
                    (mutant, selected) for _, mutant, selected in state["plan"]["to_run"]
                ]

        durations = {}

        def stream(index, status, test_seconds):
            # Recorded whether or not anyone is listening, so the final
            # in-order rewrite carries the same durations the streamed partial
            # file did. Two artifacts of the same run disagreeing about a
            # number is the kind of small dishonesty that costs trust in the
            # large ones.
            durations[index] = test_seconds
            if on_result is None:
                return
            _, mutant, selected = state["plan"]["to_run"][index]
            on_result(_result_for(mutant, status, selected, test_seconds))

        mutants_began = time.perf_counter()
        outcome = forkserver.run_warm_session(
            project_dir, cov_args, timeout, jobs, build_jobs, _apply_in_place,
            probe_args=probe_args, probes=probes, on_result=stream,
        )
        mutant_wall = time.perf_counter() - mutants_began

    if outcome is None:
        # The host died. Its baseline verdict died with it, so redo the whole
        # thing coldly rather than trusting a half-finished check.
        linemap, flaky = run_baseline_pass(project_dir, source_dir, probes)
        return linemap, run_mutants(
            project_dir, mutants, linemap, timeout, cache=cache, jobs=jobs,
            flaky=flaky, on_result=on_result,
        )

    _, statuses, child_seconds = outcome

    # The mutant phase's wall clock, split between getting a process ready and
    # running tests in it. Children overlap, so their reported durations sum to
    # more than the elapsed time; the split is proportional rather than
    # measured, and profiling.split says so.
    already_attributed = sum(
        profiler.totals.get(phase, 0.0)
        for phase in ("warm-session startup", "coverage pass", "planning")
    )
    remaining = max(mutant_wall - already_attributed, 0.0)
    profiler.split(
        "per-mutant fork",
        remaining,
        {
            "in-child test execution": child_seconds,
            "per-mutant fork": max(remaining - child_seconds, 0.0),
        },
    )
    profiler.note("mutants_run", len(statuses))

    return state["linemap"], _assemble(
        mutants, state["plan"], statuses, cache, durations
    )


def _result_for(mutant, status, selected, duration=0.0):
    return Result(
        mutant, status, len(selected), duration,
        nearest_test=sorted(selected)[0] if status == "SURVIVED" else None,
    )


def _plan(project_dir, mutants, linemap, cache, flaky=()):
    """Split mutants into already-answerable and needs-running, before forking.

    :param flaky: test node ids whose outcome varied between unmutated runs.
        A mutant selecting one of them cannot be given a confident status, so
        it is settled as SUSPICIOUS without being run at all (M1.4.3). Running
        it would produce a KILLED or SURVIVED that means nothing.
    """
    flaky = set(flaky)
    results = {}
    keys = {}
    to_run = []

    for index, mutant in enumerate(mutants):
        if mutant.suppressed:
            results[index] = Result(mutant, "SKIPPED", 0, 0.0)
            continue

        selected = sorted(linemap.select_for(mutant))

        if flaky.intersection(selected):
            # Deliberately not cached: the reason for this status is the state
            # of the suite, not the state of the source, so a later run with a
            # fixed test must not be served this answer.
            results[index] = Result(mutant, "SUSPICIOUS", len(selected), 0.0)
            continue

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

    return {"results": results, "keys": keys, "to_run": to_run}


def _assemble(mutants, plan, statuses, cache, durations=None):
    durations = durations or {}
    results = plan["results"]
    for job_index, ((index, mutant, selected), status) in enumerate(
        zip(plan["to_run"], statuses)
    ):
        results[index] = _result_for(
            mutant, status, selected, durations.get(job_index, 0.0)
        )

    if cache is not None:
        for index, result in results.items():
            if index in plan["keys"] and not result.from_cache:
                cache.put(plan["keys"][index], {
                    "status": result.status,
                    "tests_run": result.tests_run,
                    "nearest_test": result.nearest_test,
                })

    return [results[index] for index in range(len(mutants))]


def _warm_up_args(linemap):
    """Args for the warm host's priming run: collect and import everything once."""
    return ["-q", "-p", "no:cacheprovider", "-p", "no:cov", *sorted(linemap.all_tests())]


def _apply_in_place(mutant):
    """Mutate an already-imported module inside a warm grandchild.

    Raises if the module was never imported or the swap cannot be made, which
    propagates as a crashed grandchild and drops the whole batch to the cold
    path. Loud failure is the point: a mutation that quietly does not apply is
    reported SURVIVED and looks exactly like a real finding.
    """
    import sys
    from pathlib import Path

    from .codeswap import SwapFailed, apply_in_place

    target = str(Path(mutant.module).resolve())
    for module in list(sys.modules.values()):
        origin = getattr(module, "__file__", None)
        if origin and str(Path(origin).resolve()) == target:
            apply_in_place(module, target, mutant.line, mutant.mutated)
            return
    raise SwapFailed(f"{mutant.module} was not imported by the warm host")


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
