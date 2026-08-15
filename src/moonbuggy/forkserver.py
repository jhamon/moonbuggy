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

import os
import signal
import sys
import time

FORK_AVAILABLE = hasattr(os, "fork")

PYTEST_OK = 0
PYTEST_TESTS_FAILED = 1

# Exit code the child uses when pytest itself raised before returning a code.
CHILD_CRASHED = 70


def available():
    return FORK_AVAILABLE


def warm_up():
    """Import pytest in the parent so children inherit it already loaded.

    Deliberately imports nothing from the project under test -- see the module
    docstring for why that would be a correctness bug rather than a slow path.
    """
    import pytest  # noqa: F401


def run_in_fork(project_dir, mutant, selected, timeout, install_mutation):
    """Fork, apply the mutation in the child, run its tests. Returns a status."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()

    if pid == 0:
        os.close(read_fd)
        _child(project_dir, mutant, selected, install_mutation, write_fd)
        # _child never returns; os._exit is called inside it.

    os.close(write_fd)
    return _parent(pid, read_fd, timeout)


def _child(project_dir, mutant, selected, install_mutation, write_fd):
    code = CHILD_CRASHED
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
        code = pytest.main(
            ["-q", "-p", "no:cacheprovider", "-p", "no:cov", "-x", *selected]
        )
        code = int(code)
    except BaseException:
        code = CHILD_CRASHED
    finally:
        try:
            os.write(write_fd, bytes([min(code, 255)]))
        except OSError:
            pass
        # os._exit, not sys.exit: skips atexit handlers and buffer flushing that
        # belong to the parent's state, which this child only borrowed.
        os._exit(0)


def run_batch(project_dir, jobs, timeout, install_mutation, concurrency):
    """Run many mutants concurrently, one forked child each.

    Mutants are independent by construction -- each child gets its own address
    space and its own mutation, and none of them write to the project -- so
    they parallelise with no coordination at all. Serial forking left most of
    the machine idle.

    Returns statuses in the order the jobs were given, regardless of the order
    children finish.
    """
    statuses = [None] * len(jobs)
    pending = list(enumerate(jobs))
    running = {}

    while pending or running:
        while pending and len(running) < concurrency:
            index, (mutant, selected) = pending.pop(0)
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                _child(project_dir, mutant, selected, install_mutation, write_fd)
            os.close(write_fd)
            running[pid] = (index, read_fd, time.monotonic() + timeout)

        for pid, (index, read_fd, deadline) in list(running.items()):
            try:
                waited, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                waited = pid
            if waited == pid:
                statuses[index] = _status_from(read_fd)
                os.close(read_fd)
                del running[pid]
            elif time.monotonic() > deadline:
                _kill(pid)
                statuses[index] = "TIMEOUT"
                os.close(read_fd)
                del running[pid]

        if running and pending is not None:
            time.sleep(0.002)

    return statuses


def _parent(pid, read_fd, timeout):
    deadline = time.monotonic() + timeout
    try:
        while True:
            waited, raw_status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                return _status_from(read_fd)
            if time.monotonic() > deadline:
                _kill(pid)
                return "TIMEOUT"
            time.sleep(0.005)
    finally:
        os.close(read_fd)


def _status_from(read_fd):
    payload = os.read(read_fd, 1)
    if not payload:
        return "SUSPICIOUS"
    code = payload[0]
    if code == PYTEST_OK:
        return "SURVIVED"
    if code == PYTEST_TESTS_FAILED:
        return "KILLED"
    return "SUSPICIOUS"


def _kill(pid):
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
