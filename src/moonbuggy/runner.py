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
from .baseline import BaselineError
from .baseline import check as check_baseline
from .inmemory import install
from .plugin import MUTANT_ENV_VAR

PYTEST_OK = 0
PYTEST_TESTS_FAILED = 1


@dataclass(frozen=True)
class Result:
    """One mutant's outcome, before it becomes a report record."""

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

    Args:
        project_dir: the project root.
        mutants: every mutant to consider, in report order.
        linemap: the line to covering-tests map.
        timeout: seconds before one mutant is called TIMEOUT.
        python: interpreter to use for the subprocess path.
        xdist_workers: pytest-xdist workers within each mutant's run; asking
            for any opts out of the warm session, which needs one process.
        cache: a :class:`~moonbuggy.cache.ResultCache`, or None.
        use_fork: force the fork path on or off; None picks the fast one.
        jobs: how many mutants to run concurrently.
        flaky: test node ids whose outcome is not reproducible; mutants selecting one
            are settled SUSPICIOUS rather than run (M1.4.3).
        on_result: called with each :class:`Result` as it is settled.

    Returns:
        a list of :class:`Result`, one per mutant, in the input order.
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
            _warm_up_args(project_dir, linemap), _apply_in_place,
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
    """Run one mutant against its selected tests.

    The serial path, used when forking is unavailable or when xdist workers
    were requested. `run_mutants` is the entry point that chooses between
    this and the parallel one.

    Args:
        project_dir: the project root.
        mutant: the mutant to run.
        linemap: the line to covering-tests map.
        timeout: seconds before this mutant is called TIMEOUT.
        python: interpreter to use for the subprocess path.
        xdist_workers: pytest-xdist workers within this mutant's run.
        cache: a :class:`~moonbuggy.cache.ResultCache`, or None.
        use_fork: whether to fork rather than spawn a subprocess.
        flaky: test node ids whose outcome is not reproducible.

    Returns:
        A :class:`Result`.
    """
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
        python, "-m", "pytest", *_base_args(project_dir),
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

    Args:
        project_dir: project root.
        mutants: every mutant to consider, in report order.
        source_dir: directory to measure coverage of.
        timeout: seconds before one mutant is called TIMEOUT.
        cache: a :class:`~moonbuggy.cache.ResultCache`, or None.
        jobs: how many mutants to run concurrently.
        probes: extra unmutated suite runs used to detect flaky tests.
        on_result: called with each :class:`Result` as it is settled, so a run killed
            mid-flight has already emitted what it knew (M1.4.13).

    Returns:
        ``(linemap, results)``.

    Raises:
        BaselineError: if the suite is already failing or collects nothing. Falls back
            to the separate cold path when the warm host cannot complete, so a
            host failure costs time rather than correctness.
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

    profiler = profiling.active()
    # Deliberately NOT calling forkserver.warm_up() here. It imports pytest in
    # the parent so forked children inherit it, which is what the cold path
    # needs -- but the warm host imports pytest itself, and the parent on this
    # path never runs a test. See H3 in docs/perf-hypotheses.md.

    with tempfile.TemporaryDirectory() as tmp:
        data_file = Path(tmp) / "coverage-data"
        os.environ["COVERAGE_FILE"] = str(data_file)
        cov_args = [
            *_base_args(project_dir),
            f"--cov={source_dir}", "--cov-context=test", "--cov-report=",
        ]
        probe_args = [*_base_args(project_dir), "-p", "no:cov"]

        state = {}

        def build_jobs(evidence):
            """Runs in the PARENT once the host's baseline runs are done."""
            # The host measured these itself, inside the process that did the
            # work; the parent was blocked on a pipe for the whole interval and
            # can only see the sum.
            profiler.add("warm-session startup", evidence.get("startup", 0.0))
            profiler.add("coverage pass", evidence.get("coverage_seconds", 0.0))
            # The probe gets its own bucket rather than being folded into the
            # coverage pass. It is the price of the M1.4.3 flakiness guarantee,
            # and a phase whose cost is a deliberate trade should be visible
            # as itself when the trade is revisited.
            profiler.add("flaky probe", evidence.get("probe_seconds", 0.0))

            with profiler.span("planning"):
                state["flaky"] = check_baseline(evidence["runs"])
                state["linemap"] = read_coverage_data(data_file, project_dir)
                check_selection_is_runnable(
                    project_dir, state["linemap"].all_tests()
                )
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

    _, statuses, child_seconds, child_wall_seconds = outcome

    # The mutant phase's wall clock, split between getting a process ready and
    # running tests in it. Children overlap, so both measured totals exceed the
    # elapsed time and neither can be used directly. Their RATIO is still
    # meaningful, so the real wall clock is divided in that ratio -- an
    # attribution rather than a measurement, which is what profiling.split
    # documents itself as doing.
    already_attributed = sum(
        profiler.totals.get(phase, 0.0)
        for phase in ("warm-session startup", "coverage pass", "flaky probe", "planning")
    )
    remaining = max(mutant_wall - already_attributed, 0.0)
    profiler.split(
        "per-mutant fork",
        remaining,
        {
            "in-child test execution": child_seconds,
            "per-mutant fork": max(child_wall_seconds - child_seconds, 0.0),
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

    Args:
        project_dir: the project root.
        mutants: every mutant to consider, in report order.
        linemap: the line to covering-tests map.
        cache: a :class:`~moonbuggy.cache.ResultCache`, or None.
        flaky: test node ids whose outcome varied between unmutated runs. A mutant
            selecting one of them cannot be given a confident status, so it is
            settled as SUSPICIOUS without being run at all (M1.4.3). Running it
            would produce a KILLED or SURVIVED that means nothing.

    Returns:
        A dict with `results` (index to settled Result), `keys` (index to
        cache key) and `to_run` (the mutants that still need a process).
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


def _base_args(project_dir):
    """pytest arguments every moonbuggy-driven run shares.

    `--rootdir` is the important one, and it is here rather than at each call
    site because leaving it off any single run reintroduces the whole bug.

    pytest derives node ids from its rootdir, and infers rootdir by walking
    upwards for a config file. A project checked out inside another project
    that has one -- a monorepo, a vendored dependency -- therefore gets node
    ids relative to the OUTER directory. moonbuggy records those ids in the
    coverage map and hands them back to pytest from the project root, where
    they do not resolve, and every mutant becomes SUSPICIOUS with no
    explanation. Found by running against five real libraries; see
    tests/test_rootdir.py.

    Pinning rootdir to the project under mutation makes every id relative to
    the directory moonbuggy actually runs from.
    """
    return ["-q", "-p", "no:cacheprovider", "--rootdir", str(project_dir)]


def check_selection_is_runnable(project_dir, selected):
    """Verify pytest can resolve the tests selection is about to ask for.

    Args:
        project_dir: the project root, which is also pytest's working
            directory for every run moonbuggy makes.
        selected: pytest node ids from the coverage map.

    Returns:
        None. The check either passes silently or raises.

    Raises:
        BaselineError: if any node id names a file that does not exist,
            which means the whole map is expressed in the wrong frame of
            reference and no result from it would mean anything.
    """
    project_dir = Path(project_dir)
    missing = sorted(
        node_id for node_id in selected
        if not (project_dir / node_id.split("::")[0]).exists()
    )
    if not missing:
        return None

    raise BaselineError(
        f"{len(missing)} of {len(selected)} selected tests cannot be found from "
        f"{project_dir}. The first is:\n  {missing[0]}\n"
        "This means pytest's rootdir is not the directory moonbuggy is running "
        "in, so the node ids in the coverage map are relative to somewhere "
        "else -- usually an enclosing project with its own pytest config. "
        "Running moonbuggy from that outer directory, or passing --project to "
        "point at it, resolves it. No mutation results were produced, because "
        "results from an unusable test selection would all be SUSPICIOUS and "
        "would read as a property of your code."
    )


def _warm_up_args(project_dir, linemap):
    """Args for the warm host's priming run: collect and import everything once."""
    return [*_base_args(project_dir), "-p", "no:cov", *sorted(linemap.all_tests())]


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
