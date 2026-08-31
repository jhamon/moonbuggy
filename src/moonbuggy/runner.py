"""The fast path: apply each mutant in memory, run only its covering tests.

Both speed levers of the design meet here. Coverage-guided selection decides
WHICH tests run; the in-memory import hook decides HOW the mutation is
applied. Neither writes a mutated file to disk.

One process per mutant. That is not merely convenient -- it is what makes the
xdist story work without any cross-process state, since the mutant's identity
travels in the environment and every worker installs it independently. On the
warm path it is one host process plus one grandchild per mutant, which keeps
the same isolation.

`run_session` is the primary entry point and the one the CLI calls for a normal
run: it does the coverage pass and the mutant runs in a single warm process.
`run_mutants` is the fallback for when forking is unavailable or xdist workers
were asked for.

The whole reported status vocabulary is settled here: `SKIPPED` (suppressed),
`SUSPICIOUS` (a flaky test in the selection) and `NO_COVERAGE` (nothing selected)
without running anything, and `KILLED`, `KILLED_BY_ERROR`, `SURVIVED` and
`TIMEOUT` from the mutant's own process. See :data:`moonbuggy.forkserver.Status`
for what a process can decide and :data:`ResultStatus` for what can reach a
:class:`Result`.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, cast

from . import forkserver, profiling
from .baseline import BaselineError, probe_env
from .baseline import check as check_baseline
from .cache import ResultCache
from .forkserver import Job, Status, WarmSessionEvidence
from .inmemory import install
from .killreason import (
    ASSERTION_FAILED,
    EXECUTION_CRASH,
    FLAKY_PROBE,
    TEST_ERRORED,
    TESTS_ERRORED,
    KillReasonCode,
)
from .mutant import Mutant
from .plugin import MUTANT_ENV_VAR
from .profiling import Profiler

# Deferred to avoid importing coverage_pass (and, transitively, the coverage
# package) at module load for callers that never take the warm-session path --
# `run_session` already imports it lazily at runtime for the same reason. Only
# the type is needed here.
if TYPE_CHECKING:
    from .coverage_pass import LineMap

PYTEST_OK = 0
PYTEST_TESTS_FAILED = 1

# H12 established that a warm grandchild does not need assertion rewriting.
# The host was never asked the same question, and the answer is the same for a
# different reason: rewriting exists to build an introspected failure message,
# and moonbuggy reads exit codes and per-test outcomes and never shows a user
# an assertion message. A plain `assert` still raises `AssertionError` and
# still fails the test; the rewritten bytecode's entire product is discarded.
#
# Coverage is measured over the source directory, never the tests, so nothing
# about how the test files compile can reach the line->test map.
#
# Placed ahead of the caller's own `--pytest-arg` values, so a project that
# sets its own `--assert` still wins -- same ordering rule as H12.
_PLAIN_ASSERT = ("--assert=plain",)

# Almost always a forkserver.Status. SKIPPED and NO_COVERAGE are added to the
# union because `run_one` and `_plan` settle mutants to them directly -- a
# suppressed mutant and one no test reaches are both decided here, before any
# process starts, so forkserver never produces either. UNAPPLIED stays in the
# union too, and
# that is not an oversight: `_run_forked_batch` builds a Result straight from
# `run_warm_batch`'s statuses with no filtering, unlike `run_session`, which
# scrubs every UNAPPLIED via `_rerun_unapplied` before a Result is built (see
# the asymmetry documented on forkserver.Status). That gap is real and
# pre-existing; this alias names what can actually reach a Result rather than
# typing the bug away.
ResultStatus = Status | Literal["SKIPPED", "NO_COVERAGE"]


@dataclass(frozen=True)
class Result:
    """One mutant's outcome, before it becomes a report record."""

    mutant: Mutant
    status: ResultStatus
    tests_run: int
    duration: float
    nearest_test: str | None = None
    from_cache: bool = False
    killreason: KillReasonCode | None = None
    """Why this mutant was killed, or None when it was not. One of the
    :class:`~moonbuggy.killreason.KillReasonCode` enumeration
    (``assertion_failed``, ``test_errored``, ``execution_crash``,
    ``flaky_probe``), or ``None`` for any status where the reason does not
    apply -- survivors, timeouts, uncovered lines and skipped mutants. A JSONL
    consumer comparing two records compares this token directly; it is never
    free-text.

    Added with record schema 4. On a schema-3 record read from an older file
    it is ``None``, because no older version could have written it."""
    """Runtime metadata, deliberately kept OUT of the JSONL record: criterion F3
    requires a fully cached run's output to match a cold run's, and a field that
    differs by definition would defeat that check."""


class Plan(TypedDict):
    """Mutants split into already-settled and still-needing-a-process.

    See :func:`_plan`.
    """

    results: dict[int, Result]
    keys: dict[int, str]
    to_run: list[tuple[int, Mutant, list[str]]]


class RunSessionState(TypedDict, total=False):
    """Shared state threaded through `run_session`'s warm-host callbacks.

    `total=False` because it is built up field by field inside `build_jobs`
    rather than constructed all at once -- every field is present by the time
    anything reads it, but the dict genuinely does not have all three keys for
    part of `run_session`'s body.
    """

    flaky: set[str]
    linemap: "LineMap"
    plan: Plan


def run_mutants(
    project_dir: str | os.PathLike[str],
    mutants: list[Mutant],
    linemap: "LineMap",
    timeout: float = 30,
    python: str | None = None,
    xdist_workers: int = 0,
    cache: ResultCache | None = None,
    use_fork: bool | None = None,
    jobs: int | None = None,
    flaky: Iterable[str] = (),
    on_result: Callable[[Result], None] | None = None,
    extra_args: Iterable[str] = (),
) -> list[Result]:
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
            are settled SUSPICIOUS rather than run.
        on_result: called with each :class:`Result` as it is settled.
        extra_args: pytest arguments to add to every run.

    Returns:
        a list of :class:`Result`, one per mutant, in the input order. Each
        status is one of KILLED, KILLED_BY_ERROR, SURVIVED, TIMEOUT,
        SUSPICIOUS, SKIPPED or NO_COVERAGE -- plus UNAPPLIED on the warm-batch
        path, which unlike `run_session` does not scrub it (see
        :data:`ResultStatus`).
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
            project_dir,
            mutant,
            linemap,
            timeout,
            python,
            xdist_workers,
            cache,
            use_fork,
            flaky,
        )
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results


def _run_forked_batch(
    project_dir: str | os.PathLike[str],
    mutants: list[Mutant],
    linemap: "LineMap",
    timeout: float,
    cache: ResultCache | None,
    concurrency: int,
    flaky: Iterable[str] = (),
    on_result: Callable[[Result], None] | None = None,
) -> list[Result]:
    """Resolve cache hits and trivial cases first, then fork the rest in parallel."""
    plan = _plan(project_dir, mutants, linemap, cache, flaky)
    results, keys, to_run = plan["results"], plan["keys"], plan["to_run"]
    if on_result is not None:
        for result in results.values():
            on_result(result)

    if to_run:
        started = time.perf_counter()
        jobs_for_fork = [Job(mutant, selected) for _, mutant, selected in to_run]

        # Warm path first: one host imports the suite, grandchildren mutate in
        # place. Falls back to cold forks if the host dies or any mutation
        # cannot be applied in place -- never silently, since an unapplied
        # mutation reports a false SURVIVED.
        statuses = forkserver.run_warm_batch(
            project_dir,
            jobs_for_fork,
            timeout,
            concurrency,
            _warm_up_args(project_dir, linemap),
            _apply_in_place,
        )
        if statuses is None:
            statuses = forkserver.run_batch(
                project_dir, jobs_for_fork, timeout, _apply_in_child, concurrency
            )
        # Wall clock is shared across concurrent children, so per-mutant duration
        # is an average rather than a measurement. Recorded as such instead of
        # pretending to a precision forking does not allow.
        share = (time.perf_counter() - started) / len(to_run)
        for (index, mutant, selected), status in zip(to_run, statuses, strict=True):
            results[index] = Result(
                mutant,
                status,
                len(selected),
                share,
                nearest_test=sorted(selected)[0] if status == "SURVIVED" else None,
                killreason=_killreason_for(status),
            )
            if on_result is not None:
                on_result(results[index])

    if cache is not None:
        for index, result in results.items():
            # Suppressed mutants have no key: they never consult the cache, so
            # storing them would only add entries nothing ever reads.
            if index in keys and not result.from_cache:
                cache.put(
                    keys[index],
                    {
                        "status": result.status,
                        "tests_run": result.tests_run,
                        "nearest_test": result.nearest_test,
                    },
                )

    return [results[index] for index in range(len(mutants))]


def run_one(
    project_dir: str | os.PathLike[str],
    mutant: Mutant,
    linemap: "LineMap",
    timeout: float,
    python: str,
    xdist_workers: int = 0,
    cache: ResultCache | None = None,
    use_fork: bool = False,
    flaky: Iterable[str] = (),
    outcomes: str | os.PathLike[str] | None = None,
) -> Result:
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
        outcomes: a file for this run's per-test outcomes, or None. Only the
            subprocess path can produce them -- a forked child reports one
            exit-code byte and nothing else -- so asking for outcomes turns
            forking off, said here rather than left as a file that silently
            never appears.

    Returns:
        A :class:`Result`, whose status is one of KILLED, KILLED_BY_ERROR,
        SURVIVED, TIMEOUT, SUSPICIOUS, SKIPPED or NO_COVERAGE.
    """
    if outcomes is not None:
        use_fork = False
    if mutant.suppressed:
        return Result(mutant, "SKIPPED", 0, 0.0)

    selected = sorted(linemap.select_for(mutant))
    nearest = selected[0] if selected else None

    if set(flaky).intersection(selected):
        return Result(mutant, "SUSPICIOUS", len(selected), 0.0, killreason=FLAKY_PROBE)

    if cache is not None:
        key = cache.key_for(mutant, project_dir, selected)
        hit = cache.get(key)
        if hit is not None:
            return Result(
                mutant,
                # CacheRecord.status is plain str because the cache is JSON on
                # disk, not a value this process just computed -- ResultCache
                # never validates it against the status vocabulary. It only
                # ever holds what a previous run's `cache.put` wrote here
                # (both call sites below pass `result.status`), so trusting it
                # matches the code's existing behaviour rather than adding a
                # new runtime check.
                cast(ResultStatus, hit["status"]),
                tests_run=hit["tests_run"],
                duration=0.0,
                nearest_test=hit["nearest_test"],
                from_cache=True,
                killreason=_killreason_for(cast(ResultStatus, hit["status"])),
            )

    if not selected:
        # No test executes this line. Nothing can kill the mutant, so there is
        # nothing to run -- but it is still a finding (an untested line), not an
        # exclusion, so it is NO_COVERAGE rather than SKIPPED.
        #
        # It is not SURVIVED either, which is what it used to be. A survivor
        # means tests ran and none objected, so the fix is a stronger
        # assertion; this means no test was even selected, so the fix is to
        # write one -- or to find out why selection missed the test that does
        # cover it. Same exit code, different work, so a different word.
        # `_plan` settles the same case identically on the batch path.
        result = Result(mutant, "NO_COVERAGE", 0, 0.0, nearest_test=None)
    else:
        started = time.perf_counter()
        if use_fork:
            status = forkserver.run_in_fork(
                project_dir, mutant, selected, timeout, _apply_in_child
            )
        else:
            status = _run_pytest(
                project_dir,
                mutant,
                selected,
                timeout,
                python,
                xdist_workers,
                outcomes,
            )
        result = Result(
            mutant,
            status,
            tests_run=len(selected),
            duration=time.perf_counter() - started,
            nearest_test=nearest if status == "SURVIVED" else None,
            killreason=_killreason_for(status),
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


def _run_pytest(
    project_dir: str | os.PathLike[str],
    mutant: Mutant,
    selected: list[str],
    timeout: float,
    python: str,
    xdist_workers: int,
    outcomes: str | os.PathLike[str] | None = None,
) -> Status:
    command = [
        python,
        "-m",
        "pytest",
        *_base_args(project_dir),
        "-p",
        "moonbuggy.plugin",
        # Turns a "tests failed" exit code into TESTS_ERRORED when every
        # failure was a crash rather than an assertion. Registered here as
        # well as in the fork paths so all three runners answer the same
        # question the same way.
        "-p",
        "moonbuggy.killreason",
        *selected,
    ]
    if xdist_workers:
        command += ["-n", str(xdist_workers)]
    env = _env_for(project_dir, mutant)
    if outcomes is not None:
        # The same recorder the baseline pass uses. Reusing it means "which
        # tests failed" is answered by the mechanism that already answers
        # "which tests failed" for the red-baseline check, rather than by
        # parsing pytest's human output.
        command += ["-p", "moonbuggy.baseline"]
        env.update(probe_env(outcomes))

    try:
        proc = subprocess.run(
            command,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        # The mutant made something never terminate. Report it and carry on --
        # one hanging mutant must not take down the run.
        return "TIMEOUT"

    if proc.returncode == PYTEST_OK:
        return "SURVIVED"
    if proc.returncode == PYTEST_TESTS_FAILED:
        return "KILLED"
    if proc.returncode == TESTS_ERRORED:
        return "KILLED_BY_ERROR"
    # pytest could not complete: collection error, internal error, usage error,
    # or nothing collected. Not a clean kill, which is what SUSPICIOUS is for.
    return "SUSPICIOUS"


def run_session(
    project_dir: str | os.PathLike[str],
    mutants: list[Mutant],
    source_dir: str | os.PathLike[str],
    timeout: float = 30,
    cache: ResultCache | None = None,
    jobs: int | None = None,
    probes: int = 1,
    on_result: Callable[[Result], None] | None = None,
    extra_args: Iterable[str] = (),
) -> "tuple[LineMap, list[Result]]":
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
            mid-flight has already emitted what it knew.
        extra_args: pytest arguments to add to every run, baseline and mutant
            alike. A project whose real test command is not bare `pytest` --
            one that needs `--doctest-modules`, say -- is otherwise measured
            against a suite smaller than the one it actually runs, and every
            mutant its doctests would catch is reported as a survivor.

    Returns:
        ``(linemap, results)``. Each result's status is one of KILLED,
        KILLED_BY_ERROR, SURVIVED, TIMEOUT, SUSPICIOUS, SKIPPED or
        NO_COVERAGE; UNAPPLIED never reaches a caller here, because
        `_rerun_unapplied` re-runs those coldly first.

    Raises:
        BaselineError: if the suite is already failing or collects nothing. Falls back
            to the separate cold path when the warm host cannot complete, so a
            host failure costs time rather than correctness.
    """
    from .coverage_pass import prewarm_reader, read_coverage_data, run_baseline_pass

    project_dir = Path(project_dir)
    if jobs is None:
        # One per core, not one fewer. The core held back was for the process
        # doing the holding back -- but on this path the parent is blocked on
        # a pipe and the host is blocked in waitpid for the whole interval the
        # grandchildren are running, so the spare core was spare.
        jobs = os.cpu_count() or 2

    if not forkserver.available():
        linemap, flaky = run_baseline_pass(
            project_dir, source_dir, probes, extra_args=extra_args
        )
        return linemap, run_mutants(
            project_dir,
            mutants,
            linemap,
            timeout,
            cache=cache,
            jobs=jobs,
            flaky=flaky,
            on_result=on_result,
            extra_args=extra_args,
        )

    profiler = profiling.active()
    # Paid once here instead of once in the host and once again in this
    # process after it finishes. See prewarm_reader.
    prewarm_reader()
    # Deliberately NOT calling forkserver.warm_up() here. It imports pytest in
    # the parent so forked children inherit it, which is what the cold path
    # needs -- but the warm host imports pytest itself, and the parent on this
    # path never runs a test. See H3 in docs/development/perf-hypotheses.md.

    with tempfile.TemporaryDirectory() as tmp:
        data_file = Path(tmp) / "coverage-data"
        os.environ["COVERAGE_FILE"] = str(data_file)
        cov_args = [
            *_base_args(project_dir),
            f"--cov={source_dir}",
            "--cov-context=test",
            "--cov-report=",
            *_PLAIN_ASSERT,
            *extra_args,
        ]
        probe_args = [
            *_base_args(project_dir),
            "-p",
            "no:cov",
            *_PLAIN_ASSERT,
            *extra_args,
        ]

        state: RunSessionState = {}

        def build_jobs(evidence: WarmSessionEvidence) -> list[Job]:
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
                state["linemap"] = read_coverage_data(
                    data_file, project_dir, known_tests=evidence["runs"][0]
                )
                check_selection_is_runnable(project_dir, state["linemap"].all_tests())
                state["plan"] = _plan(
                    project_dir, mutants, state["linemap"], cache, state["flaky"]
                )
                if on_result is not None:
                    for result in state["plan"]["results"].values():
                        on_result(result)
                return [
                    Job(mutant, selected)
                    for _, mutant, selected in state["plan"]["to_run"]
                ]

        durations: dict[int, float] = {}

        def stream(index: int, status: Status, test_seconds: float) -> None:
            # UNAPPLIED is not a result, it is a request to try again coldly.
            # Emitting it would put a status in the JSONL that no reader has a
            # meaning for.
            if status == "UNAPPLIED":
                durations[index] = test_seconds
                return
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
            project_dir,
            cov_args,
            timeout,
            jobs,
            build_jobs,
            _apply_in_place,
            probe_args=probe_args,
            probes=probes,
            on_result=stream,
            extra_args=extra_args,
        )
        mutant_wall = time.perf_counter() - mutants_began

    if outcome is None:
        # The host died. Its baseline verdict died with it, so redo the whole
        # thing coldly rather than trusting a half-finished check.
        linemap, flaky = run_baseline_pass(
            project_dir, source_dir, probes, extra_args=extra_args
        )
        return linemap, run_mutants(
            project_dir,
            mutants,
            linemap,
            timeout,
            cache=cache,
            jobs=jobs,
            flaky=flaky,
            on_result=on_result,
            extra_args=extra_args,
        )

    # Named apart from the `jobs` parameter above (concurrency, an int): this
    # is the unpacked `WarmSessionOutcome.jobs`, the per-mutant `Job` list,
    # and the two meanings sharing one name is what mypy is objecting to here.
    run_jobs, statuses, child_seconds, child_wall_seconds = outcome
    statuses = _rerun_unapplied(
        project_dir, run_jobs, statuses, timeout, profiler, on_result, state, extra_args
    )

    # The mutant phase's wall clock, split between getting a process ready and
    # running tests in it. Children overlap, so both measured totals exceed the
    # elapsed time and neither can be used directly. Their RATIO is still
    # meaningful, so the real wall clock is divided in that ratio -- an
    # attribution rather than a measurement, which is what profiling.split
    # documents itself as doing.
    already_attributed = sum(
        profiler.totals.get(phase, 0.0)
        for phase in (
            "warm-session startup",
            "coverage pass",
            "flaky probe",
            "planning",
        )
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


def _rerun_unapplied(
    project_dir: str | os.PathLike[str],
    jobs: list[Job],
    statuses: list[Status],
    timeout: float,
    profiler: Profiler,
    on_result: Callable[[Result], None] | None,
    state: RunSessionState,
    extra_args: Iterable[str] = (),
) -> list[Status]:
    """Run coldly whatever the warm host could not mutate in place.

    The warm path swaps a mutation into an already-imported module, and that
    cannot always be done -- a decorator has replaced the function object, a
    module-level statement will not re-execute, a class body has already been
    consumed. `codeswap` refuses in those cases rather than guessing, which is
    right, but until this existed the refusal arrived as SUSPICIOUS and read as
    a finding about the user's code. One real-world suite produced 315 of them.

    So they are re-run the slow way instead: a cold fork per mutant, from the
    parent, which has never imported the module under test. That is the path
    the import hook was written for and it handles every one of these shapes.

    Args:
        project_dir: the project root.
        jobs: the ``(mutant, selected_tests)`` pairs the host was given.
        statuses: the statuses it returned, some possibly UNAPPLIED.
        timeout: seconds before one mutant is called TIMEOUT.
        profiler: the active profiler, for the extra phase.
        on_result: streamed-result callback, or None.
        state: the run's shared state, holding the plan.
        extra_args: pytest arguments every run shares.

    Returns:
        The statuses with every UNAPPLIED replaced by a real one.
    """
    retry = [index for index, status in enumerate(statuses) if status == "UNAPPLIED"]
    if not retry:
        return statuses

    profiler.note("rerun_cold", len(retry))
    with profiler.span("cold fallback"):
        forkserver.warm_up()
        cold = forkserver.run_batch(
            project_dir,
            [jobs[index] for index in retry],
            timeout,
            _apply_in_child,
            max(1, (os.cpu_count() or 2) - 1),
            extra_args,
        )

    statuses = list(statuses)
    for index, status in zip(retry, cold, strict=True):
        statuses[index] = status
        if on_result is not None:
            _, mutant, selected = state["plan"]["to_run"][index]
            on_result(_result_for(mutant, status, selected))
    return statuses


def _killreason_for(status: ResultStatus, flaky: bool = False) -> KillReasonCode | None:
    """The killreason that corresponds to a status, if any.

    Args:
        status: the verdict.
        flaky: True when the status was settled by the flakiness detector
            rather than by running the mutant, so SUSPICIOUS maps to
            ``flaky_probe`` instead of ``execution_crash``.

    Returns:
        One of the :mod:`moonbuggy.killreason` enumeration, or ``None`` for
        statuses where no reason applies.
    """
    if status == "KILLED":
        return ASSERTION_FAILED
    if status == "KILLED_BY_ERROR":
        return TEST_ERRORED
    if status == "SUSPICIOUS":
        return FLAKY_PROBE if flaky else EXECUTION_CRASH
    return None


def _result_for(
    mutant: Mutant, status: Status, selected: list[str], duration: float = 0.0
) -> Result:
    return Result(
        mutant,
        status,
        len(selected),
        duration,
        nearest_test=sorted(selected)[0] if status == "SURVIVED" else None,
        killreason=_killreason_for(status),
    )


def _plan(
    project_dir: str | os.PathLike[str],
    mutants: list[Mutant],
    linemap: "LineMap",
    cache: ResultCache | None,
    flaky: Iterable[str] = (),
) -> Plan:
    """Split mutants into already-answerable and needs-running, before forking.

    Args:
        project_dir: the project root.
        mutants: every mutant to consider, in report order.
        linemap: the line to covering-tests map.
        cache: a :class:`~moonbuggy.cache.ResultCache`, or None.
        flaky: test node ids whose outcome varied between unmutated runs. A mutant
            selecting one of them cannot be given a confident status, so it is
            settled as SUSPICIOUS without being run at all. Running it
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
            results[index] = Result(
                mutant,
                "SUSPICIOUS",
                len(selected),
                0.0,
                killreason=FLAKY_PROBE,
            )
            continue

        if cache is not None:
            keys[index] = cache.key_for(mutant, project_dir, selected)
            hit = cache.get(keys[index])
            if hit is not None:
                results[index] = Result(
                    mutant,
                    # See the matching cast in `run_one` above.
                    cast(ResultStatus, hit["status"]),
                    hit["tests_run"],
                    0.0,
                    nearest_test=hit["nearest_test"],
                    from_cache=True,
                    killreason=_killreason_for(cast(ResultStatus, hit["status"])),
                )
                continue

        if not selected:
            # The batch path's copy of `run_one`'s no-coverage case; see the
            # reasoning there. A status emitted by only one of the two would be
            # a verdict that depended on `--jobs`.
            results[index] = Result(mutant, "NO_COVERAGE", 0, 0.0, nearest_test=None)
        else:
            to_run.append((index, mutant, selected))

    return {"results": results, "keys": keys, "to_run": to_run}


def _assemble(
    mutants: list[Mutant],
    plan: Plan,
    statuses: list[Status],
    cache: ResultCache | None,
    durations: dict[int, float] | None = None,
) -> list[Result]:
    durations = durations or {}
    results = plan["results"]
    for job_index, ((index, mutant, selected), status) in enumerate(
        zip(plan["to_run"], statuses, strict=True)
    ):
        results[index] = _result_for(
            mutant, status, selected, durations.get(job_index, 0.0)
        )

    if cache is not None:
        for index, result in results.items():
            if index in plan["keys"] and not result.from_cache:
                cache.put(
                    plan["keys"][index],
                    {
                        "status": result.status,
                        "tests_run": result.tests_run,
                        "nearest_test": result.nearest_test,
                    },
                )

    return [results[index] for index in range(len(mutants))]


def _base_args(project_dir: str | os.PathLike[str]) -> list[str]:
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


def check_selection_is_runnable(
    project_dir: str | os.PathLike[str], selected: Collection[str]
) -> None:
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
        node_id
        for node_id in selected
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


def _warm_up_args(project_dir: str | os.PathLike[str], linemap: "LineMap") -> list[str]:
    """Args for the warm host's priming run: collect and import everything once."""
    return [*_base_args(project_dir), "-p", "no:cov", *sorted(linemap.all_tests())]


def _apply_in_place(mutant: Mutant) -> None:
    """Mutate an already-imported module inside a warm grandchild.

    Raises if the module was never imported or the swap cannot be made. The
    grandchild catches that deliberately, exits `COULD_NOT_APPLY`, and the
    parent reports UNAPPLIED for that one mutant and re-runs just it on the
    cold path -- no other mutant in the batch is affected. Loud failure is the
    point: a mutation that quietly does not apply is reported SURVIVED and
    looks exactly like a real finding.
    """
    from pathlib import Path

    from .codeswap import SwapFailed, apply_in_place, module_at

    target = str(Path(mutant.module).resolve())
    module = module_at(target)
    if module is None:
        raise SwapFailed(f"{mutant.module} was not imported by the warm host")
    apply_in_place(module, target, mutant.line, mutant.mutated)


def _apply_in_child(mutant: Mutant) -> None:
    """Install the mutation inside a forked child.

    The path is resolved relative to the child's cwd, which forkserver has
    already set to the project root.
    """
    install(str(Path(mutant.module).resolve()), mutant.line, mutant.mutated)


def _env_for(project_dir: str | os.PathLike[str], mutant: Mutant) -> dict[str, str]:
    project_dir = Path(project_dir)
    env = dict(os.environ)
    env[MUTANT_ENV_VAR] = json.dumps(
        {
            "path": str((project_dir / mutant.module).resolve()),
            "line": mutant.line,
            "mutated": mutant.mutated,
        }
    )
    return env
