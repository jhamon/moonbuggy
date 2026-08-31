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

from .killreason import TESTS_ERRORED, KillReason
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
#
# KILLED_BY_ERROR is a kill like KILLED -- the mutation was noticed -- but the
# tests errored out rather than objecting. `moonbuggy.killreason` explains why
# the two are different findings about the suite.
Status = Literal[
    "SURVIVED",
    "KILLED",
    "KILLED_BY_ERROR",
    "TIMEOUT",
    "SUSPICIOUS",
    "UNAPPLIED",
]


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

        from .killreason import KillReason

        # -p no:cov: the mutant run needs no coverage instrumentation, and
        # pytest-cov registers hooks on every session. -x: one failure is
        # already a kill, so there is nothing to learn from the rest.
        began = time.perf_counter()
        code = pytest.main(_mutant_args(selected, extra_args), plugins=[KillReason()])
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


def prebuild_mutant_config(extra_args: Iterable[str] = ()) -> object | None:
    """Build the pytest `Config` every mutant run needs, once, in the warm host.

    `pytest.main` is two things: `_prepareconfig`, which parses the arguments,
    registers plugins, walks the entry points and loads the initial conftests;
    and then the session that actually collects and runs. Profiling one warm
    grandchild put the first at **4.4ms of an 11ms run** -- and its answer is
    identical for every mutant, because the only thing that differs between
    mutants is which node ids to run.

    So it is built here, before the first fork, and each grandchild inherits a
    copy and points it at its own node ids. The move is the same as the other
    prebuilds: hoist a per-mutant constant into the host.

    **Why nothing is shared between mutants.** Sharing a `Config` between
    mutants *in one process*
    would be the deferred hypothesis, and its failure mode -- state from one
    mutant surviving into the next -- is the thing the whole design exists to
    prevent. Nothing is shared between mutants here. Each grandchild is a
    separate process that gets its own copy-on-write copy of a config built
    before any mutation existed, uses it exactly once, and exits. Two mutants
    can no more see each other's config than they can see each other's
    `sys.modules`.

    Built with no node ids: the ids are per-mutant, and everything expensive
    about the config is not.

    Args:
        extra_args: pytest arguments every mutant run shares.

    Returns:
        the `Config`, or None if it could not be built -- in which case each
        grandchild falls back to a plain `pytest.main`, which is slower and
        identical in every other respect.
    """
    try:
        from _pytest.config import _prepareconfig

        return _prepareconfig(
            _mutant_args((), extra_args, False), [_SELECTED_ONLY, _KILL_REASON]
        )
    except BaseException:
        # Private pytest API. If a future version moves it, the fallback is a
        # slower run rather than a wrong one, so this must not be fatal.
        return None


def precollect(config: object, node_ids: Iterable[str]) -> object | None:
    """Collect every test any mutant can select, once, in the warm host.

    With the prebuilds landed, `cProfile` put `perform_collect` at **4ms of a
    6.3ms warm grandchild** -- the largest thing left in the process that
    repeats. And its input is the same for every mutant: the union of the node
    ids selection can ask for is known before the first fork, and collecting
    that union once is collecting a superset of every mutant's own selection.

    So the host builds the `Session`, runs `pytest_sessionstart` and collects.
    Each grandchild inherits it, keeps the items its own node ids name, and
    runs those -- 6.3ms to 2.3ms in the micro-benchmark, for 5-12ms paid once.

    **Why nothing is shared between mutants.** The collection happens before
    any mutation exists. Each grandchild gets its own copy-on-write copy of
    it, filters it, runs it once and exits; no two mutants share a process, so
    neither can see the other's items any more than it can see the other's
    `sys.modules`. What makes that safe *here specifically* is that codeswap
    replaces a function's `__code__` in place rather than rebinding the name,
    so an item collected before the swap still reaches the mutated code --
    the same property that already makes the host's own imports safe to
    inherit.

    Args:
        config: the config from :func:`prebuild_mutant_config`.
        node_ids: every test node id any mutant selects.

    Returns:
        the collected `Session`, or None to fall back to collecting inside
            each grandchild. None on any failure, including a collection error:
            a session that could not collect cleanly must not be the one every
            mutant is judged against.
    """
    ids = sorted(set(node_ids))
    if not ids:
        return None
    try:
        from _pytest.main import Session

        config.args = ids  # type: ignore[attr-defined]  # a pytest Config
        config.option.file_or_dir = ids  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
        session = Session.from_config(config)  # type: ignore[arg-type]  # pytest's typeshed omits this runtime attribute
        session.exitstatus = 0
        # Both of these ran once per grandchild before and now run once here.
        # The mirror-image `pytest_sessionfinish` stays in the grandchild, so
        # a plugin still sees exactly one finish per process that runs tests.
        config._do_configure()  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
        config.hook.pytest_sessionstart(session=session)  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
        session.perform_collect()
    except BaseException:
        return None
    if session.testsfailed or session.shouldstop or not session.items:
        # A collection error here would otherwise be inherited by every
        # grandchild at once, turning one broken import into a whole run of
        # confident wrong statuses.
        return None
    return session


def _run_precollected(config: object, session: object, selected: Iterable[str]) -> int:
    """Run the pre-collected items this mutant's node ids name, and no others.

    Replaces `pytest_cmdline_main` rather than calling it, because the whole
    point is to skip the collection `_main` would do. The exit codes are
    computed the way `_main` computes them, so a caller reading the code
    cannot tell which path produced it.
    """
    from _pytest.config import ExitCode

    # `session.Failed`, not `_pytest.outcomes.Failed`: `-x` stopping the loop
    # raises `_pytest.main.Failed`, and the two are unrelated classes with the
    # same name. Catching the wrong one turned every mutant its tests actually
    # killed into a crashed grandchild -- reported SUSPICIOUS, on exactly the
    # shape whose tests can fail. Taken off the session rather than imported,
    # so it cannot drift from the class the loop raises.
    failed = type(session).Failed  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute

    _KILL_REASON.reset()
    wanted = set(selected)
    session.items = [i for i in session.items if i.nodeid in wanted]  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
    session.testscollected = len(session.items)  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
    try:
        try:
            config.hook.pytest_runtestloop(session=session)  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
        except failed:
            # `-x` stopping the loop. A kill, not a crash.
            session.exitstatus = ExitCode.TESTS_FAILED  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
        else:
            if session.testsfailed:  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
                session.exitstatus = ExitCode.TESTS_FAILED  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
            elif session.testscollected == 0:  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
                session.exitstatus = ExitCode.NO_TESTS_COLLECTED  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
            else:
                session.exitstatus = ExitCode.OK  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
        config.hook.pytest_sessionfinish(  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
            session=session,
            exitstatus=session.exitstatus,  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
        )
        return int(session.exitstatus)  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
    finally:
        # Still the unraisable-exception plugin's chance to speak. See
        # `_run_prebuilt` for why that is not optional.
        config._ensure_unconfigure()  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute


class _SelectedOnly:
    """Stop collection descending into files no selected node id names.

    `Dir.collect` scans its whole directory and asks
    :func:`pytest_collect_file` about every entry, so a mutant selecting two
    tests in one file still built a `Module` node for all forty files in the
    suite. `Session.collect` then discards the thirty-nine it was not asked
    for. Profiling the warm grandchild put collection at **55% of
    what was left**, almost all of it that discarding.

    `pytest_ignore_collect` is the supported way to say so up front. The set
    it skips is exactly the set `Session.collect` was going to throw away, so
    what is collected -- and therefore what runs -- does not change.

    Deliberately conservative in both directions. A path is skipped only when
    it is positively known to hold no selected test: anything not resolvable,
    and every directory on the way to a selected file, collects as before. And
    `None` rather than `False` is returned for those, so this only ever adds
    an opinion where it has one and never overrules another plugin's.

    One instance is registered in the prebuilt config, before the fork; each
    grandchild sets it to its own ids in its own copy. Nothing crosses
    between mutants, for the same reason the config itself does not.
    """

    def __init__(self) -> None:
        self._files: frozenset[str] = frozenset()
        self._dirs: frozenset[str] = frozenset()

    def select(self, node_ids: Iterable[str]) -> None:
        """Restrict collection to the files naming these node ids."""
        from pathlib import Path

        files: set[str] = set()
        dirs: set[str] = set()
        for node_id in node_ids:
            relative = node_id.split("::")[0]
            if not relative:
                # A bare "::test_x" names no file, so there is nothing to
                # restrict to and everything must stay collectable.
                self._files = self._dirs = frozenset()
                return
            path = Path(relative)
            if not path.is_absolute():
                path = Path(os.getcwd()) / path
            # Real paths on both sides of the comparison: pytest's come from
            # scanning the directory, a node id's is built from the cwd, and
            # on a machine where the project sits under a symlink (/tmp on
            # macOS) those two spellings differ.
            resolved = os.path.realpath(path)
            files.add(resolved)
            dirs.update(str(parent) for parent in Path(resolved).parents)
        self._files = frozenset(files)
        self._dirs = frozenset(dirs)

    def pytest_ignore_collect(self, collection_path: object) -> bool | None:
        """True for a path that cannot hold a selected test, else None."""
        if not self._files:
            return None
        path = os.path.realpath(str(collection_path))
        if path in self._files or path in self._dirs:
            return None
        return True


# Registered in the prebuilt config; pointed at each grandchild's own ids by
# `_run_prebuilt`, in that grandchild's own copy of the process.
_SELECTED_ONLY = _SelectedOnly()

# Also registered in the prebuilt config, and reset by each grandchild in its
# own copy for the same reason: the host builds the config before any mutation
# exists, so whatever it left in this object belongs to no mutant.
_KILL_REASON = KillReason()


def _run_prebuilt(config: object, selected: Iterable[str]) -> int:
    """Run one mutant's tests using a config built before the fork.

    `config.args` is what `Session.perform_collect` collects from, and it is
    the only part of the config that differs between mutants. `file_or_dir` is
    the parsed option it was derived from, kept in step so anything reading
    the option rather than the attribute sees the same answer.
    """
    ids = list(selected)
    _SELECTED_ONLY.select(ids)
    _KILL_REASON.reset()
    config.args = ids  # type: ignore[attr-defined]  # a pytest Config, not typed here
    config.option.file_or_dir = ids  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
    try:
        return int(config.hook.pytest_cmdline_main(config=config))  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute
    finally:
        # Still run the unconfigure hooks, so the unraisable-exception plugin
        # gets its say. A mutant that manifests only as an unraisable
        # exception would otherwise be reported SURVIVED -- the failure mode
        # H2 was rejected for.
        config._ensure_unconfigure()  # type: ignore[attr-defined]  # pytest's typeshed omits this runtime attribute


def _mutant_args(
    selected: Iterable[str],
    extra_args: Iterable[str] = (),
    rewrite_asserts: bool = True,
) -> list[str]:
    """pytest arguments for one mutant's run, inside an already-chdir'd child.

    `--rootdir` is pinned to the cwd, which the child has already set to the
    project root. Without it, pytest can infer a rootdir above the project and
    then fail to resolve the very node ids the coverage map recorded -- which
    is not a hypothetical, it is what three of the five real-world suites did.

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
        probes: how many probe runs to make.
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

        # Everything reachable at this point was inherited from the parent --
        # moonbuggy's own modules, pytest, coverage -- and none of it is
        # garbage. Freezing it here, rather than only before the forks, keeps
        # the collections the coverage run itself triggers from walking it.
        _freeze_heap()

        # The probes are unmutated runs whose only job is to disagree with the
        # coverage run, so nothing about them depends on it. Started here, in
        # a sibling process, they run alongside it instead of after it.
        probe_pid, probe_read = _start_probe_child(probe_args or cov_args, probes)

        runs: list[dict[str, str]] = []
        recorder = OutcomeRecorder()
        coverage_began = time.perf_counter()
        pytest.main(cov_args, plugins=[recorder])
        coverage_seconds = time.perf_counter() - coverage_began
        runs.append(dict(recorder.outcomes))

        # Only whatever the probe has not already finished is on the critical
        # path, which is what this interval measures. It is the probe's cost
        # to the run, not the probe's duration.
        probe_began = time.perf_counter()
        runs += _collect_probe_runs(
            probe_pid, probe_read, probe_args or cov_args, probes
        )
        probe_seconds = time.perf_counter() - probe_began

        evidence: WarmSessionEvidence = {
            "runs": runs,
            "startup": startup,
            "coverage_seconds": coverage_seconds,
            "probe_seconds": probe_seconds,
        }
        payload = pickle.dumps(evidence)
        os.write(status_write, len(payload).to_bytes(8, "big"))
        os.write(status_write, payload)

        # H23. From here until the jobs arrive, the parent is reading the
        # coverage data and planning -- 3.5ms to 21ms in which this process
        # used to do nothing at all. Neither of the next two things needs the
        # jobs, so they happen in that window instead of after it.
        #
        # The node ids come from the recorder rather than from the parent's
        # line map, and the two cannot disagree in the direction that matters:
        # the map's tests are the coverage contexts unioned with exactly these
        # outcomes, so this is a superset of anything selection can ask for,
        # and collecting a superset is what H28 needs.
        mutant_config = prebuild_mutant_config(extra_args)
        mutant_session = (
            None
            if mutant_config is None
            else precollect(
                mutant_config,
                (
                    node
                    for node in runs[0]
                    if "::" in node and not node.endswith("::<collection>")
                ),
            )
        )

        size = int.from_bytes(_read_exactly(jobs_read, 8), "big")
        jobs: list[Job] = pickle.loads(_read_exactly(jobs_read, size))

        # Read every module under mutation once, here, so the grandchildren
        # inherit the text instead of each opening the file for itself. The
        # host is the only place this can pay off -- see srcio._SOURCE_CACHE.
        from pathlib import Path

        from .codeswap import index_modules
        from .srcio import prewarm

        modules = {mutant.module for mutant, _ in jobs}
        prewarm(modules)
        # Same argument, for finding the module to swap: see index_modules.
        # The paths are spelled exactly as `runner._apply_in_place` will ask
        # for them -- resolved, relative to the project root this host has
        # already chdir'd to -- because a spelling mismatch here is an index
        # miss, and an index miss is a `sys.modules` rescan per grandchild.
        index_modules({str(Path(module).resolve()) for module in modules})
        # The config and the collection were built above, while the parent was
        # still planning -- see H23. Only these two needed the jobs.
        #
        # Last, so the frozen generation includes everything above.
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

        _fork_grandchildren(
            jobs,
            timeout,
            concurrency,
            apply_swap,
            emit,
            extra_args,
            mutant_config,
            mutant_session,
        )
    except BaseException:
        pass
    finally:
        os._exit(0)


def _run_probes(args: list[str], probes: int) -> list[dict[str, str]]:
    """`probes` unmutated runs of the suite, each recording its own outcomes."""
    import pytest

    from .baseline import OutcomeRecorder

    runs = []
    for _ in range(probes):
        recorder = OutcomeRecorder()
        pytest.main(args, plugins=[recorder])
        runs.append(dict(recorder.outcomes))
    return runs


def _start_probe_child(args: list[str], probes: int) -> tuple[int, int]:
    """Fork a sibling of the coverage run to do the flakiness probes.

    The probe exists to catch a test whose outcome varies between two
    unmutated runs. Nothing about it depends on the coverage run:
    it needs no instrumentation, reads none of the coverage run's output, and
    its own output is one ``{node_id: outcome}`` mapping per run. Running it
    after the coverage run therefore put a full extra suite execution on the
    critical path for no reason other than that both wanted the same process
    -- 6.8-8.6% of wall clock in the profile, on every shape.

    Here it gets its own process and runs alongside. It pays for that with a
    cold-ish start: forked before the coverage run, it imports and collects
    the test modules itself rather than inheriting them. That is more total
    work and less wall clock, which is the trade worth making on a machine
    with cores to spare.

    Correctness is unchanged and arguably strengthened. The probe compares
    outcomes across separate runs, and separate *processes* is a strictly
    weaker assumption about shared state than the same process was.

    Args:
        args: pytest arguments for one probe run.
        probes: how many probe runs to make; 0 forks nothing.

    Returns:
        ``(pid, read_fd)``, or ``(0, -1)`` when there is nothing to probe or
        the fork failed -- in which case the caller runs the probes inline.
    """
    if probes < 1:
        return 0, -1
    try:
        read_fd, write_fd = os.pipe()
        pid = os.fork()
    except OSError:
        return 0, -1

    if pid == 0:
        os.close(read_fd)
        try:
            payload = pickle.dumps(_run_probes(args, probes))
            os.write(write_fd, len(payload).to_bytes(8, "big"))
            os.write(write_fd, payload)
        except BaseException:
            pass
        finally:
            os._exit(0)

    os.close(write_fd)
    return pid, read_fd


def _collect_probe_runs(
    pid: int, read_fd: int, args: list[str], probes: int
) -> list[dict[str, str]]:
    """Read what the probe child observed, or run the probes here if it failed.

    The fallback is not decoration. A probe that silently produced nothing
    would mean no test was ever compared against itself, and the flakiness
    guarantee would be quietly gone -- a flaky test would then be reported as
    a mutant's SURVIVED or KILLED depending on the day. Losing the child costs
    the wall clock this optimisation was saving, and nothing else.
    """
    if pid == 0:
        return _run_probes(args, probes)
    try:
        size = int.from_bytes(_read_exactly(read_fd, 8), "big")
        runs: list[dict[str, str]] = pickle.loads(_read_exactly(read_fd, size))
    except (EOFError, OSError, pickle.UnpicklingError):
        runs = _run_probes(args, probes)
    finally:
        os.close(read_fd)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)
    return runs


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
    5: "KILLED_BY_ERROR",
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


def _operator_dispatch_rank() -> dict[str, int]:
    """Map each operator name to its cost rank, cheapest first.

    The C2 "failing-fast" ordering key: dispatch cheap operators before
    expensive ones so that, on a project where operator cost actually varies,
    the cheap high-signal mutants are the first to occupy the concurrency pool
    and the first to report. Cost is the same three coarse buckets
    `moonbuggy operators` reports, so the ordering is opinionated but honest:
    exactly the ordering the tool already advertises, applied to scheduling.

    Built once -- the operator set is fixed for the life of the process. An
    empty dict means "no ordering": dispatch falls back to job order, which is
    the prior behaviour and is identical in every output artifact either way,
    because statuses are keyed by job index, never by dispatch position.

    Returns:
        operator name -> cost rank, where a smaller rank is cheaper. An
        operator missing from the map (it cannot happen inside one version,
        since generation and dispatch read the same registry) sorts last.
    """
    try:
        from .operators import COSTS, describe_operators

        return {info.name: COSTS.index(info.cost) for info in describe_operators()}
    except Exception:
        # No operator metadata -> a stable no-op ordering. An ordering that
        # could not be built must not be able to misorder a real dispatch.
        return {}


def _cheap_first_order(jobs: list[Job], cost_rank: dict[str, int]) -> list[int]:
    """Job indices in dispatch order: cheap operators first, ties stable.

    Args:
        jobs: the queued jobs.
        cost_rank: operator name -> rank from :func:`_operator_dispatch_rank`.

    Returns:
        the job indices in dispatch order. Equal-cost jobs keep their caller
        order (stable), and a job whose operator is absent from `cost_rank`
        sorts last rather than first -- the conservative direction, since this
        ordering only ever decides which concurrency slot runs an isolated
        process. An empty `cost_rank` returns identity (job order).
    """
    if not cost_rank:
        return list(range(len(jobs)))
    worst = len(cost_rank)
    return sorted(
        range(len(jobs)),
        key=lambda i: cost_rank.get(jobs[i].mutant.operator, worst),
    )


def _fork_grandchildren(
    jobs: list[Job],
    timeout: float,
    concurrency: int,
    apply_swap: Callable[[Mutant], None],
    emit: Callable[[int, Status, float, float], None] | None = None,
    extra_args: Iterable[str] = (),
    config: object | None = None,
    session: object | None = None,
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
        config: a pytest `Config` built once in the caller, for each grandchild
            to run its own copy of. None means build one per grandchild, the
            way `pytest.main` does. See :func:`prebuild_mutant_config`.
        session: a `Session` whose collection was performed once in the caller,
            for each grandchild to filter to its own node ids. None means
            collect per grandchild. See :func:`precollect`.

    Returns:
        the statuses, in job order. Dispatch order is cheap operators first
        (:func:`_operator_dispatch_rank`); because each grandchild is an
        isolated process and statuses are keyed by job index, the schedule is
        invisible to every result artifact.
    """
    statuses: list[Status | None] = [None] * len(jobs)
    # C2 failing-fast ordering: cheap operators first. Only which concurrency
    # slot runs which *isolated* grandchild changes -- statuses are keyed by
    # the original index below, so no record can move, and the parent's result
    # stream is reassembled by index regardless of schedule.
    pending = [
        (index, jobs[index])
        for index in _cheap_first_order(jobs, _operator_dispatch_rank())
    ]
    running: dict[int, tuple[int, int, float, float]] = {}

    while pending or running:
        while pending and len(running) < concurrency:
            index, (mutant, selected) = pending.pop(0)
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                _grandchild(
                    mutant, selected, apply_swap, write_fd, extra_args, config, session
                )
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
    config: object | None = None,
    session: object | None = None,
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
        if config is None:
            _KILL_REASON.reset()
            code = int(
                pytest.main(
                    _mutant_args(selected, extra_args, False),
                    plugins=[_KILL_REASON],
                )
            )
        elif session is None:
            code = int(_run_prebuilt(config, selected))
        else:
            code = int(_run_precollected(config, session, selected))
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
    if code == TESTS_ERRORED:
        return "KILLED_BY_ERROR", seconds
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
