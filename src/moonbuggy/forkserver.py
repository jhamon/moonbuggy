"""Run each mutant in a forked child instead of a fresh interpreter.

Why this exists: the first comparative benchmark had moonbuggy 12x SLOWER than
mutmut on a workload where coverage-guided selection should have won easily
(0.82s for 108 mutants against our 10.13s for 84). Selection was working
perfectly -- it was the per-mutant `python -m pytest` subprocess that cost
everything. Interpreter startup plus importing pytest plus collection ran to
roughly 120ms per mutant, and the selected tests themselves took a fraction of
that. We were measuring process creation, not mutation testing.

Forking solves it because the expensive work is done once. The parent imports
pytest and does nothing else; each child inherits that memory, applies its
mutation, and runs only its own tests. A fork is a few milliseconds.

The parent must NEVER import the module under test. If it did, every child
would inherit an already-imported unmutated module, install() would evict it
from sys.modules but the parent's copy would still be there for the next fork,
and mutations would silently do nothing -- a false SURVIVED, the same failure
mode as the xdist bug from a third direction.

POSIX only. Windows has no fork, so runner.py keeps the subprocess path as a
fallback rather than this being the only way to run.
"""

import contextlib
import os
import pickle
import signal
import time
from collections.abc import Callable, Iterable
from typing import Literal, NamedTuple, TypedDict, cast

from .mutant import Mutant

FORK_AVAILABLE = hasattr(os, "fork")

PYTEST_OK = 0
PYTEST_TESTS_FAILED = 1

# Exit code the child uses when pytest itself raised before returning a code.
CHILD_CRASHED = 70

# The statuses a mutant run can settle to. Every one of these is decided in a
# child and reported back to a parent -- the whole reason this module exists
# is that the decision is made in a process this one did not create by import,
# so it has to travel back as data rather than being read off a return value
# in the same frame.
#
# UNAPPLIED is meant to stay internal: it means the warm host's grandchild
# could not swap the mutation into an already-imported module, and the
# caller is supposed to retry that job coldly rather than report it.
# `run_warm_session`'s caller does exactly that (runner._rerun_unapplied
# scrubs every UNAPPLIED before building a Result). `run_warm_batch`'s
# caller (runner._run_forked_batch) does not -- it builds a Result straight
# from the returned statuses with no filtering -- so UNAPPLIED can reach a
# Result on that path today. Pre-existing gap, not introduced or fixed here.
Status = Literal["SURVIVED", "KILLED", "TIMEOUT", "SUSPICIOUS", "UNAPPLIED"]


class Job(NamedTuple):
    """One mutant queued to run, paired with the tests selected for it.

    Direction: parent -> child. Every fork-per-mutant path (`run_batch`,
    `run_warm_batch`'s warm host, `_fork_grandchildren`) receives a list of
    these as a plain in-process argument -- the child inherits it via `fork`,
    so nothing is actually serialised. `run_warm_session` is the one path
    where a `Job` list really does cross a pipe: `build_jobs` returns it in
    the parent, and it is pickled to the warm host.

    Named because every call site destructures it as `mutant, selected =
    job` -- the two fields already have names in the reader's head, just not
    in the code before this.
    """

    mutant: Mutant
    selected: list[str]


class WarmSessionEvidence(TypedDict):
    """What the warm host learned from running the suite, sent back once.

    Direction: child (the warm host) -> parent. Pickled across the status
    pipe by `_warm_session_host` right after its coverage (and probe) runs
    finish, and unpickled in `run_warm_session` before `build_jobs` is called
    with it as the parent's only evidence about what happened in the child.
    """

    runs: list[dict[str, str]]
    """One ``{node_id: outcome}`` mapping per unmutated run: the coverage-pass
    run first, then one per flakiness probe. Same shape ``baseline.classify``
    consumes."""
    startup: float
    coverage_seconds: float
    probe_seconds: float


class WarmSessionOutcome(NamedTuple):
    """What `run_warm_session` hands back once every job has settled.

    Not itself pickled -- assembled in the parent from data that already
    crossed the pipe (the evidence above, and one streamed frame per job) --
    but named for the same reason as `Job`: `run_session` destructures it
    positionally as `jobs, statuses, child_seconds, child_wall_seconds =
    outcome`, and those four names belong on the type, not just at the call
    site.
    """

    jobs: list[Job]
    statuses: list[Status]
    child_seconds: float
    child_wall_seconds: float


def available() -> bool:
    """Whether this platform can fork. False on Windows, where the subprocess
    path in runner.py is used instead."""
    return FORK_AVAILABLE


def warm_up() -> None:
    """Import pytest in the parent so children inherit it already loaded.

    Deliberately imports nothing from the project under test -- see the module
    docstring for why that would be a correctness bug rather than a slow path.
    """
    import pytest

    # Referenced so the import is not "unused" -- its value is not needed,
    # only the side effect of pytest being loaded into sys.modules.
    _ = pytest


def run_in_fork(
    project_dir: str | os.PathLike[str],
    mutant: Mutant,
    selected: Iterable[str],
    timeout: float,
    install_mutation: Callable[[Mutant], None],
) -> Status:
    """Fork, apply the mutation in the child, run its tests. Returns a status."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()

    if pid == 0:
        os.close(read_fd)
        _child(project_dir, mutant, selected, install_mutation, write_fd)
        # _child never returns; os._exit is called inside it.

    os.close(write_fd)
    return _parent(pid, read_fd, timeout)


def _child(
    project_dir: str | os.PathLike[str],
    mutant: Mutant,
    selected: Iterable[str],
    install_mutation: Callable[[Mutant], None],
    write_fd: int,
    extra_args: Iterable[str] = (),
) -> None:
    code = CHILD_CRASHED
    micros = 0
    try:
        os.chdir(project_dir)
        # Silence the child. Its output is not the report, and interleaving
        # dozens of pytest runs would make the real output unreadable.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)

        install_mutation(mutant)

        import pytest

        # -p no:cov: the mutant run needs no coverage instrumentation, and
        # pytest-cov registers hooks on every session. -x: one failure is
        # already a kill, so there is nothing to learn from the rest.
        began = time.perf_counter()
        code = pytest.main(_mutant_args(selected, extra_args))
        micros = int((time.perf_counter() - began) * 1_000_000)
        code = int(code)
    except BaseException:
        code = CHILD_CRASHED
    finally:
        with contextlib.suppress(OSError):
            os.write(write_fd, _child_payload(code, micros))
        # os._exit, not sys.exit: skips atexit handlers and buffer flushing that
        # belong to the parent's state, which this child only borrowed.
        os._exit(0)


def _mutant_args(
    selected: Iterable[str],
    extra_args: Iterable[str] = (),
    rewrite_asserts: bool = True,
) -> list[str]:
    """pytest arguments for one mutant's run, inside an already-chdir'd child.

    `--rootdir` is pinned to the cwd, which the child has already set to the
    project root. Without it, pytest can infer a rootdir above the project and
    then fail to resolve the very node ids the coverage map recorded -- which
    is not a hypothetical, it is what three of the five M4 libraries did.

    `-p no:cov`: the mutant run needs no coverage instrumentation, and
    pytest-cov registers hooks on every session. `-x`: one failure is already a
    kill, so there is nothing to learn from the rest.

    `extra_args` carries whatever the project's own test command needs. It is
    not optional decoration: a project run with `--doctest-modules` has doctest
    node ids in its coverage map, and pytest cannot select one without the flag
    that creates it. Omitting it here made every such mutant exit with a usage
    error and be reported SUSPICIOUS -- 315 of 434 on boltons.

    `rewrite_asserts=False` adds `--assert=plain`, and only the warm
    grandchild passes it. Deciding which installed plugins to mark for
    assertion rewriting means walking the file list of every installed
    distribution, which profiling put at 26% of a warm `pytest.main` -- work
    whose answer is the same for every mutant in the run.

    Skipping it costs the warm grandchild nothing, for two independent
    reasons: the host imported and rewrote every test module during the
    coverage pass, so the mutant run imports none of them again; and the
    host's rewrite hook is still in the grandchild's `sys.meta_path`, so
    anything that *did* import late would be rewritten by it anyway. What the
    flag turns off is this session installing a second hook and recomputing
    the same answer.

    It goes ahead of `extra_args` so a project that sets its own
    `--assert` still wins.
    """
    import os as _os

    return [
        "-q",
        "-p",
        "no:cacheprovider",
        "--rootdir",
        _os.getcwd(),
        "-p",
        "no:cov",
        "-x",
        *([] if rewrite_asserts else ["--assert=plain"]),
        *extra_args,
        *selected,
    ]


def run_pytest_in_fork(
    cwd: str | os.PathLike[str],
    args: list[str],
    env_updates: dict[str, str],
    timeout: float,
) -> int | None:
    """Run pytest.main in a forked child. Returns its exit code, or None on timeout.

    Used for the coverage pass. That pass was a `python -m pytest` subprocess and
    measured at 0.297s of an 0.88s run -- a third of the total, most of it
    interpreter startup that the parent has already paid. Forking from a parent
    that has pytest imported skips it.

    Safe despite the child importing the whole project: the child gets its own
    address space, so nothing it imports reaches the parent, and the parent stays
    clean for the mutation forks that follow.
    """
    read_fd, write_fd = os.pipe()
    pid = os.fork()

    if pid == 0:
        os.close(read_fd)
        code = CHILD_CRASHED
        try:
            os.chdir(cwd)
            os.environ.update(env_updates)
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)

            import pytest

            code = int(pytest.main(args))
        except BaseException:
            code = CHILD_CRASHED
        finally:
            with contextlib.suppress(OSError):
                os.write(write_fd, bytes([min(code, 255)]))
            os._exit(0)

    os.close(write_fd)
    deadline = time.monotonic() + timeout
    try:
        while True:
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                payload = os.read(read_fd, 1)
                return payload[0] if payload else CHILD_CRASHED
            if time.monotonic() > deadline:
                _kill(pid)
                return None
            time.sleep(0.005)
    finally:
        os.close(read_fd)


def run_warm_session(
    project_dir: str | os.PathLike[str],
    cov_args: list[str],
    timeout: float,
    concurrency: int,
    build_jobs: Callable[[WarmSessionEvidence], list[Job]],
    apply_swap: Callable[[Mutant], None],
    probe_args: list[str] | None = None,
    probes: int = 0,
    on_result: Callable[[int, Status, float], None] | None = None,
    extra_args: Iterable[str] = (),
) -> WarmSessionOutcome | None:
    """One suite run that both builds the coverage map and warms the process.

    Previously these were two separate full runs of the test suite: a coverage
    pass in one fork, then a priming run inside the warm host. On the benchmark
    workload that was 0.275s and 0.15s of an 0.79s total -- over half the run,
    spent executing the same tests twice.

    They are the same work, so this does it once. The host runs the suite under
    coverage, sends back what it observed, and waits. The parent reads the
    coverage data, checks the baseline is green, builds the line->test map,
    decides which tests each mutant needs, and sends the jobs back. The host
    then forks a grandchild per mutant from a process where every test module is
    already imported.

    Args:
        project_dir: project root; the host chdirs here.
        cov_args: pytest arguments for the instrumented baseline run.
        timeout: seconds before one mutant is called TIMEOUT.
        concurrency: how many grandchildren run at once.
        build_jobs: called in the PARENT with the baseline evidence; returns the
            ``(mutant, selected_tests)`` jobs to run. May raise, in which case
            the host is torn down and the exception propagates.
        apply_swap: called in each grandchild to apply its mutation.
        probe_args: pytest arguments for the extra unmutated probe runs.
        probes: how many probe runs to make (M1.4.3).
        on_result: called as ``(index, status, test_seconds)`` the moment each mutant
            finishes, so a run killed mid-flight has already reported what it knew.
        extra_args: pytest arguments every run shares, including each mutant's.

    Returns:
        A :class:`WarmSessionOutcome`, or None if the host could not complete --
            so the caller falls back rather than reporting results it did not
            actually produce.
    """
    jobs_read, jobs_write = os.pipe()
    jobs_write_closed = False
    status_read, status_write = os.pipe()
    pid = os.fork()

    if pid == 0:
        os.close(jobs_write)
        os.close(status_read)
        _warm_session_host(
            project_dir,
            cov_args,
            timeout,
            concurrency,
            apply_swap,
            jobs_read,
            status_write,
            probe_args,
            probes,
            extra_args,
        )

    os.close(jobs_read)
    os.close(status_write)
    try:
        try:
            size = int.from_bytes(_read_exactly(status_read, 8), "big")
            baseline: WarmSessionEvidence = pickle.loads(
                _read_exactly(status_read, size)
            )
        except (EOFError, OSError, pickle.UnpicklingError):
            return None

        jobs = build_jobs(baseline)
        payload = pickle.dumps(jobs)
        os.write(jobs_write, len(payload).to_bytes(8, "big"))
        os.write(jobs_write, payload)
        os.close(jobs_write)
        jobs_write_closed = True

        statuses: list[Status | None] = [None] * len(jobs)
        child_seconds = 0.0
        child_wall_seconds = 0.0
        done = 0
        while done < len(jobs):
            try:
                frame = _read_exactly(status_read, _FRAME_SIZE)
            except EOFError:
                return None
            index = int.from_bytes(frame[:4], "big")
            status = _STATUS_BY_CODE.get(frame[4], "SUSPICIOUS")
            test_seconds = int.from_bytes(frame[5:9], "big") / 1_000_000
            child_wall = int.from_bytes(frame[9:13], "big") / 1_000_000
            statuses[index] = status
            child_seconds += test_seconds
            child_wall_seconds += child_wall
            done += 1
            if on_result is not None:
                on_result(index, status, test_seconds)
        # Every index in range(len(jobs)) was assigned exactly once by the loop
        # above, so no `None` placeholder survives -- safe to narrow the type.
        return WarmSessionOutcome(
            jobs, cast("list[Status]", statuses), child_seconds, child_wall_seconds
        )
    finally:
        if not jobs_write_closed:
            os.close(jobs_write)
        os.close(status_read)
        _kill(pid)


def _warm_session_host(
    project_dir: str | os.PathLike[str],
    cov_args: list[str],
    timeout: float,
    concurrency: int,
    apply_swap: Callable[[Mutant], None],
    jobs_read: int,
    status_write: int,
    probe_args: list[str] | None,
    probes: int,
    extra_args: Iterable[str] = (),
) -> None:
    try:
        os.chdir(project_dir)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)

        began = time.perf_counter()
        import pytest

        from .baseline import OutcomeRecorder

        # Everything up to here is the host becoming ready to run anything:
        # the fork, the chdir, and importing pytest if the parent had not
        # already. Reported separately because "warm-session startup" is one of
        # the phases M2.1.1 names, and it is the one a reader most expects to
        # be large and most often is not.
        startup = time.perf_counter() - began

        runs: list[dict[str, str]] = []
        recorder = OutcomeRecorder()
        coverage_began = time.perf_counter()
        pytest.main(cov_args, plugins=[recorder])
        coverage_seconds = time.perf_counter() - coverage_began
        runs.append(dict(recorder.outcomes))

        # Extra unmutated runs, from the same warm process. Their only purpose
        # is to disagree with the first one: a test whose outcome varies here
        # cannot be trusted to report on a mutation either (M1.4.3).
        for _ in range(probes):
            probe = OutcomeRecorder()
            pytest.main(probe_args or cov_args, plugins=[probe])
            runs.append(dict(probe.outcomes))

        evidence: WarmSessionEvidence = {
            "runs": runs,
            "startup": startup,
            "coverage_seconds": coverage_seconds,
            "probe_seconds": time.perf_counter() - coverage_began - coverage_seconds,
        }
        payload = pickle.dumps(evidence)
        os.write(status_write, len(payload).to_bytes(8, "big"))
        os.write(status_write, payload)

        size = int.from_bytes(_read_exactly(jobs_read, 8), "big")
        jobs: list[Job] = pickle.loads(_read_exactly(jobs_read, size))

        # Read every module under mutation once, here, so the grandchildren
        # inherit the text instead of each opening the file for itself. The
        # host is the only place this can pay off -- see srcio._SOURCE_CACHE.
        from .codeswap import index_modules
        from .srcio import prewarm

        prewarm({mutant.module for mutant, _ in jobs})
        # Same argument, for finding the module to swap: see index_modules.
        index_modules()
        _freeze_heap()

        def emit(
            index: int, status: Status, test_seconds: float, child_wall: float
        ) -> None:
            os.write(
                status_write,
                index.to_bytes(4, "big")
                + bytes([_CODE_BY_STATUS[status]])
                + _micros(test_seconds)
                + _micros(child_wall),
            )

        _fork_grandchildren(jobs, timeout, concurrency, apply_swap, emit, extra_args)
    except BaseException:
        pass
    finally:
        os._exit(0)


def _freeze_heap() -> None:
    """Move everything imported so far out of the garbage collector's reach.

    `pytest.main` calls `gc.collect()` twice on its way out, from the
    unraisable-exception plugin's unconfigure hook. In a warm host that has
    imported pytest, coverage and the entire suite, a collection walks ~25000
    tracked objects and costs about 1.1ms -- and every grandchild pays it,
    for objects it inherited and cannot have changed.

    `gc.freeze()` moves the current heap into a permanent generation that
    collection skips. Objects the mutant's own tests allocate are still
    tracked and still collected, so the unraisable plugin keeps working on
    the only garbage that can say anything about this mutant. Called after
    the priming run and the two prewarms, so it captures as much as possible,
    and before the first fork, so every grandchild inherits the frozen heap.

    Not free of consequence, and the consequence is bounded: a reference cycle
    created *before* this point will never be collected in a grandchild, so a
    `__del__` on one of those will not run. Those are the host's own
    infrastructure objects -- pytest's, coverage's, the suite's at import time
    -- none of which the mutation has touched.

    Measured on the slow-tests workload: 18.2ms to 13.2ms per mutant run.
    """
    import gc

    gc.freeze()


def _read_exactly(fd: int, size: int) -> bytes:
    chunks = b""
    while len(chunks) < size:
        chunk = os.read(fd, size - len(chunks))
        if not chunk:
            raise EOFError("warm host pipe closed early")
        chunks += chunk
    return chunks


def run_warm_batch(
    project_dir: str | os.PathLike[str],
    jobs: list[Job],
    timeout: float,
    concurrency: int,
    warm_args: list[str],
    apply_swap: Callable[[Mutant], None],
) -> list[Status] | None:
    """Run mutants from a process that has already imported the test suite.

    The expensive part of a mutant run is not the tests -- it is importing the
    test modules, rewriting their asserts and collecting them, which a
    fork-per-mutant pays every single time (~90ms against ~12ms for pytest.main
    in a process where that work is done).

    So this forks ONE warm host, has it run the suite once to get everything
    imported, and then forks a grandchild per mutant from that warm state. Each
    grandchild mutates in place with codeswap and runs only its own tests.

    The warm host is a child, never the parent: it imports the whole project,
    and the parent has to stay clean so the import-hook fallback path still
    works for anything codeswap cannot handle.
    """
    read_fd, write_fd = os.pipe()
    pid = os.fork()

    if pid == 0:
        os.close(read_fd)
        _warm_host(
            project_dir, jobs, timeout, concurrency, warm_args, apply_swap, write_fd
        )

    os.close(write_fd)
    deadline = time.monotonic() + timeout * max(1, len(jobs))
    collected = b""
    try:
        while len(collected) < len(jobs):
            chunk = os.read(read_fd, len(jobs) - len(collected))
            if not chunk:
                break
            collected += chunk
            if time.monotonic() > deadline:
                break
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)
    finally:
        os.close(read_fd)

    if len(collected) < len(jobs):
        return None  # Warm host died; caller falls back to the cold path.
    return [_STATUS_BY_CODE.get(code, "SUSPICIOUS") for code in collected]


_STATUS_BY_CODE: dict[int, Status] = {
    0: "SURVIVED",
    1: "KILLED",
    2: "TIMEOUT",
    3: "SUSPICIOUS",
    # Not a status the user ever sees. It means the grandchild could not apply
    # its mutation in place, so nothing was measured and the mutant has to be
    # re-run on the cold path. Reporting SUSPICIOUS instead -- which is what
    # happened before this existed -- turns a fixable internal limitation into
    # a finding about the user's code. The M4 hunt produced 315 of them on one
    # library that way.
    4: "UNAPPLIED",
}
_CODE_BY_STATUS: dict[Status, int] = {v: k for k, v in _STATUS_BY_CODE.items()}

# One streamed result: a 4-byte job index, a 1-byte status code, 4 bytes of
# in-child test microseconds, and 4 bytes of total child microseconds as the
# host saw them. Both times are needed to separate "running the tests" from
# "getting a process ready to run them": children overlap, so the difference
# per child is the only way to attribute the two without double counting.
# Indexed rather than positional because grandchildren finish out of order, and
# 4 bytes for the index rather than 2 because a large project really can
# exceed 65535 mutants.
_FRAME_SIZE = 13


def _warm_host(
    project_dir: str | os.PathLike[str],
    jobs: list[Job],
    timeout: float,
    concurrency: int,
    warm_args: list[str],
    apply_swap: Callable[[Mutant], None],
    write_fd: int,
) -> None:
    try:
        os.chdir(project_dir)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)

        import pytest

        # One full run to import every test module, rewrite its asserts and
        # populate pytest's caches. Every grandchild inherits all of it.
        pytest.main(warm_args)

        from .codeswap import index_modules

        index_modules()
        _freeze_heap()

        statuses = _fork_grandchildren(jobs, timeout, concurrency, apply_swap)
        os.write(write_fd, bytes(_CODE_BY_STATUS[s] for s in statuses))
    except BaseException:
        pass
    finally:
        os._exit(0)


def _fork_grandchildren(
    jobs: list[Job],
    timeout: float,
    concurrency: int,
    apply_swap: Callable[[Mutant], None],
    emit: Callable[[int, Status, float, float], None] | None = None,
    extra_args: Iterable[str] = (),
) -> list[Status]:
    """Fork one grandchild per job, at most `concurrency` at a time.

    Args:
        jobs: the ``(mutant, selected_tests)`` pairs to run.
        timeout: seconds before one grandchild is called TIMEOUT.
        concurrency: how many grandchildren run at once.
        apply_swap: called in each grandchild to apply its mutation.
        emit: if given, called as ``(index, status, test_seconds, child_wall)`` as each
            grandchild is reaped, so results can be streamed rather than
            batched at the end. `child_wall` is measured here rather than in
            the child, because it has to include the fork itself.
        extra_args: pytest arguments every grandchild's run shares.

    Returns:
        the statuses, in job order.
    """
    statuses: list[Status | None] = [None] * len(jobs)
    pending = list(enumerate(jobs))
    running: dict[int, tuple[int, int, float, float]] = {}

    while pending or running:
        while pending and len(running) < concurrency:
            index, (mutant, selected) = pending.pop(0)
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                _grandchild(mutant, selected, apply_swap, write_fd, extra_args)
            os.close(write_fd)
            running[pid] = (
                index,
                read_fd,
                time.monotonic() + timeout,
                time.monotonic(),
            )

        for pid, (index, read_fd, deadline, forked_at) in list(running.items()):
            try:
                waited, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                waited = pid
            if waited == pid:
                statuses[index], test_seconds = _status_from(read_fd)
                os.close(read_fd)
                del running[pid]
                if emit is not None:
                    emit(
                        index,
                        # statuses[index] was just assigned above, on this
                        # same line's left side -- never None here.
                        cast(Status, statuses[index]),
                        test_seconds,
                        time.monotonic() - forked_at,
                    )
            elif time.monotonic() > deadline:
                _kill(pid)
                statuses[index] = "TIMEOUT"
                os.close(read_fd)
                del running[pid]
                if emit is not None:
                    emit(index, "TIMEOUT", float(timeout), time.monotonic() - forked_at)

        if running:
            time.sleep(0.002)

    # Every index in range(len(jobs)) is assigned exactly once, above, before
    # the loop that reads `running` can exit -- safe to narrow the type.
    return cast("list[Status]", statuses)


# Exit code for "the mutation could not be applied in this process".
COULD_NOT_APPLY = 71


def _grandchild(
    mutant: Mutant,
    selected: Iterable[str],
    apply_swap: Callable[[Mutant], None],
    write_fd: int,
    extra_args: Iterable[str] = (),
) -> None:
    code = CHILD_CRASHED
    micros = 0
    try:
        try:
            apply_swap(mutant)
        except BaseException:
            # Distinguished from every other failure on purpose: this one is
            # recoverable by running the mutant coldly, and the parent can only
            # know to do that if the child says which failure it was.
            code = COULD_NOT_APPLY
            raise
        import pytest

        began = time.perf_counter()
        code = int(pytest.main(_mutant_args(selected, extra_args, False)))
        # Measured inside the child so the parent can separate the cost of
        # running the tests from the cost of getting a process ready to run
        # them (criterion M2.1.1). Without this split, "per-mutant fork" and
        # "in-child test execution" are one indivisible bucket, which is
        # exactly the bucket the optimisation question is about.
        micros = int((time.perf_counter() - began) * 1_000_000)
    except BaseException:
        if code != COULD_NOT_APPLY:
            code = CHILD_CRASHED
    finally:
        with contextlib.suppress(OSError):
            os.write(write_fd, _child_payload(code, micros))
        os._exit(0)


# One child result: the pytest exit code plus how long pytest.main took, in
# microseconds. Four bytes covers 71 minutes, well past any per-mutant timeout.
_CHILD_PAYLOAD_SIZE = 5


def _child_payload(code: int, micros: int) -> bytes:
    return bytes([min(code, 255)]) + _micros(micros / 1_000_000)


def _micros(seconds: float) -> bytes:
    """Seconds as 4 big-endian bytes of microseconds, saturating at 71 minutes."""
    return min(max(int(seconds * 1_000_000), 0), 0xFFFFFFFF).to_bytes(4, "big")


def run_batch(
    project_dir: str | os.PathLike[str],
    jobs: list[Job],
    timeout: float,
    install_mutation: Callable[[Mutant], None],
    concurrency: int,
    extra_args: Iterable[str] = (),
) -> list[Status]:
    """Run many mutants concurrently, one forked child each.

    Mutants are independent by construction -- each child gets its own address
    space and its own mutation, and none of them write to the project -- so
    they parallelise with no coordination at all. Serial forking left most of
    the machine idle.

    Returns statuses in the order the jobs were given, regardless of the order
    children finish.
    """
    statuses: list[Status | None] = [None] * len(jobs)
    pending = list(enumerate(jobs))
    running: dict[int, tuple[int, int, float]] = {}

    while pending or running:
        while pending and len(running) < concurrency:
            index, (mutant, selected) = pending.pop(0)
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                _child(
                    project_dir,
                    mutant,
                    selected,
                    install_mutation,
                    write_fd,
                    extra_args,
                )
            os.close(write_fd)
            running[pid] = (index, read_fd, time.monotonic() + timeout)

        for pid, (index, read_fd, deadline) in list(running.items()):
            try:
                waited, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                waited = pid
            if waited == pid:
                statuses[index], _ = _status_from(read_fd)
                os.close(read_fd)
                del running[pid]
            elif time.monotonic() > deadline:
                _kill(pid)
                statuses[index] = "TIMEOUT"
                os.close(read_fd)
                del running[pid]

        if running and pending is not None:
            time.sleep(0.002)

    # Every index in range(len(jobs)) is assigned exactly once, above, before
    # the loop that reads `running` can exit -- safe to narrow the type.
    return cast("list[Status]", statuses)


def _parent(pid: int, read_fd: int, timeout: float) -> Status:
    deadline = time.monotonic() + timeout
    try:
        while True:
            waited, raw_status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                status, _ = _status_from(read_fd)
                return status
            if time.monotonic() > deadline:
                _kill(pid)
                return "TIMEOUT"
            time.sleep(0.005)
    finally:
        os.close(read_fd)


def _status_from(read_fd: int) -> tuple[Status, float]:
    """Read one child's result. Returns (status, in-child test seconds).

    An empty payload means the child never reached its own `finally` -- a test
    that called `os._exit`, or a signal. There is no exit code to read and no
    honest confident status to give, which is what SUSPICIOUS is for.
    """
    payload = os.read(read_fd, _CHILD_PAYLOAD_SIZE)
    if not payload:
        return "SUSPICIOUS", 0.0

    code = payload[0]
    seconds = (
        int.from_bytes(payload[1:5], "big") / 1_000_000
        if len(payload) >= _CHILD_PAYLOAD_SIZE
        else 0.0
    )
    if code == PYTEST_OK:
        return "SURVIVED", seconds
    if code == PYTEST_TESTS_FAILED:
        return "KILLED", seconds
    if code == COULD_NOT_APPLY:
        return "UNAPPLIED", seconds
    return "SUSPICIOUS", seconds


def _kill(pid: int) -> None:
    """Terminate a hung child and reap it, so a timeout leaves no zombie."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        for _ in range(100):
            try:
                if os.waitpid(pid, os.WNOHANG)[0] == pid:
                    return
            except ChildProcessError:
                return
            time.sleep(0.01)
