"""Why a mutant died: a failed assertion, or a test that blew up.

A kill is not a kill. When a mutation makes the code raise `NameError` at the
first line that touches it, every test near it errors out -- and the tool
reports the same `KILLED` it reports for a test that computed the wrong answer
and said so. The two are different findings about the suite:

- a **failed assertion** proves a test *checked* the behaviour the mutation
  changed. That is the thing mutation testing measures.
- an **errored test** proves only that a test *executes* the line. The mutant
  broke the code badly enough that touching it explodes; nothing was checked.

Under the `default` tier the distinction is a curiosity, because most mutations
there produce a program that still runs and merely computes something else.
Under `statement_deletion` it is the difference between a meaningful kill rate
and a meaningless one: delete a binding and everything downstream raises. A
deep-tier run without this would report a *higher* score for a suite that
checks less.

HOW THE ANSWER TRAVELS
======================

Every path that runs a mutant's tests -- a forked child, a warm grandchild, a
`python -m pytest` subprocess -- reports back through pytest's exit code and
nothing else. So this plugin puts the answer there, by rewriting
`session.exitstatus` from `TESTS_FAILED` to :data:`TESTS_ERRORED` in
`pytest_sessionfinish`. Each runner then learns one new code rather than a new
channel, and the three of them cannot drift apart by reading different things.

The classification itself is made from `call.excinfo` in
`pytest_exception_interact`, which is exact -- the exception class, not a
rendered string -- and stamped onto the report rather than kept in an
attribute of this object. That is what makes it survive pytest-xdist: the
worker classifies, `TestReport` carries unknown attributes through its own
JSON round trip, and the controller reads the flag off the deserialised report
in `pytest_runtest_logreport`.
"""

from enum import StrEnum
from typing import Any

# pytest is imported lazily, inside the two functions that need it. This
# module is reachable from `runner` and so from the CLI's import graph, and
# `import pytest` costs about a tenth of a second -- which `moonbuggy
# operators` should not pay to print a list.

# pytest's own "tests failed" exit code, spelled out rather than imported for
# the same reason.
PYTEST_TESTS_FAILED = 1

# The exit code this plugin substitutes for pytest's TESTS_FAILED when every
# failure was an error rather than an assertion. Above pytest's own codes
# (0-5) and clear of `forkserver.CHILD_CRASHED` (70) and `COULD_NOT_APPLY`
# (71), so no runner has to guess which layer produced a number.
TESTS_ERRORED = 72

# The attribute the classification travels on. Named rather than anonymous
# because it crosses pytest's own report serialisation, where a collision with
# a field pytest or another plugin owns would be silent.
FLAG = "moonbuggy_errored"


# ── Kill reason enumeration ─────────────────────────────────────────────────
# The stable vocabulary for the JSONL `killreason` field. Each member is a
# machine-readable token consumed by human triage, the JSONL schema, and agent
# workflows. The tokens are never free-text; a parser comparing two records
# compares these values directly.
#
# This enum is a versioned contract: adding or removing a member is a breaking
# change that requires a schema version bump. The current vocabulary is frozen
# per docs/contracts/killreason-v1.md.
#
# These sit in this module because the reason taxonomy is owned here:
# every reason the classifier can produce is defined here, and every consumer
# of the JSONL schema reads these exact tokens.


class KillReasonCode(StrEnum):
    """Stable per-kill reason — one token per verdict cause.

    Each member is its own string value (``StrEnum``), so it compares equal to
    the string token it carries and serialises to that token in JSON. The
    ``.code`` property is an explicit alias for the string value, and
    ``.label`` is the human-readable form for documentation and the human trace.
    """

    ASSERTION_FAILED = "assertion_failed"
    """A selected test's assertion failed under the mutation -- the test checked
    the mutated behaviour and objected."""

    TEST_ERRORED = "test_errored"
    """A selected test errored out under the mutation -- the test executed the
    line but did not check its result."""

    EXECUTION_CRASH = "execution_crash"
    """Pytest could not complete: collection error, internal error, usage error,
    or nothing collected. Not a statement about the mutation."""

    FLAKY_PROBE = "flaky_probe"
    """Test outcomes disagreed across unmutated runs -- a selected test behaved
    inconsistently, so no confident verdict is possible."""

    @property
    def code(self) -> str:
        """Machine-readable code, identical to the enum value (the JSONL token)."""
        return self.value

    @property
    def label(self) -> str:
        """Human-readable label, derived from the human trace."""
        _LABELS = {
            "assertion_failed": "assertion failed",
            "test_errored": "test errored",
            "execution_crash": "execution crash",
            "flaky_probe": "flaky probe",
        }
        return _LABELS[self.value]


# Module-level constants — aliases for the enum members so existing imports
# keep working. Each constant IS the corresponding KillReasonCode member, which
# IS a str (StrEnum), so every comparison, serialisation, and format-string use
# that worked before continues to work unchanged.
ASSERTION_FAILED = KillReasonCode.ASSERTION_FAILED
TEST_ERRORED = KillReasonCode.TEST_ERRORED
EXECUTION_CRASH = KillReasonCode.EXECUTION_CRASH
FLAKY_PROBE = KillReasonCode.FLAKY_PROBE

# Every killreason that is a real reason (rather than None). Seeded from the
# enum so the schema doc and any future validator have one source of truth.
_KILLREASONS = frozenset(KillReasonCode)


class KillReason:
    """Records whether this session's failures were assertions or errors.

    One instance per process that runs one mutant's tests. It carries no state
    between mutants because no two mutants ever share a process -- see
    `forkserver`'s module docstring for why that is load-bearing rather than
    incidental.
    """

    def __init__(self) -> None:
        self.assertion_failed = False
        self.test_errored = False

    def reset(self) -> None:
        """Forget this session's verdict.

        The warm host builds one config before the first fork and every
        grandchild inherits a copy of this object with it, so each one starts
        by clearing whatever the host happened to leave in it.
        """
        self.assertion_failed = False
        self.test_errored = False

    @property
    def errored(self) -> bool:
        """Whether this session's kill was an error rather than an assertion.

        One assertion failure anywhere is enough to call the kill ordinary:
        it is direct evidence that a test checked the mutated behaviour, and
        the errors beside it are then a consequence rather than the finding.
        """
        return self.test_errored and not self.assertion_failed

    def pytest_exception_interact(self, node: object, call: Any, report: Any) -> None:
        """Classify one failure, stamp its report, and fold it in.

        This is where the classification happens in whichever process actually
        ran the test -- the ordinary case, and the xdist *worker*.

        Args:
            node: the item or collector that failed. Unused.
            call: the `CallInfo`, carrying the `ExceptionInfo` this reads.
            report: the report to stamp, so the answer survives xdist.
        """
        if hasattr(report, FLAG):
            return
        errored = _is_error(call, report)
        setattr(report, FLAG, errored)
        self._fold(errored)

    def pytest_runtest_logreport(self, report: Any) -> None:
        """Fold in a report that already carries a classification.

        Only an xdist controller reaches this with a flag set: in a single
        process `call_and_report` calls `pytest_runtest_logreport` *before*
        `pytest_exception_interact`, so an unflagged report here has not been
        classified yet rather than been classified as an assertion. Folding it
        in anyway is how the first version of this got every crash-kill wrong.

        Args:
            report: one test report, possibly deserialised from a worker.
        """
        if report.failed and hasattr(report, FLAG):
            self._fold(bool(getattr(report, FLAG)))

    def _fold(self, errored: bool) -> None:
        """Record one classified failure."""
        if errored:
            self.test_errored = True
        else:
            self.assertion_failed = True

    def pytest_sessionfinish(self, session: Any, exitstatus: Any) -> None:
        """Rewrite the exit code when the kill was an error.

        Args:
            session: the pytest session, whose `exitstatus` is what every
                caller of `pytest.main` (and of `pytest_cmdline_main`) ends up
                returning.
            exitstatus: the code pytest settled on, before this.
        """
        if int(exitstatus) == PYTEST_TESTS_FAILED and self.errored:
            session.exitstatus = TESTS_ERRORED


def _is_error(call: Any, report: Any) -> bool:
    """Whether this failure is a test blowing up rather than a test objecting.

    Args:
        call: the `CallInfo` for the phase that failed.
        report: the report being classified.

    Returns:
        True for an uncaught exception, or for any failure outside the call
        phase -- a fixture that raised has not checked anything either. False
        for the exceptions a test raises on purpose to object, which includes
        a doctest whose output did not match.
    """
    if getattr(report, "when", "call") != "call":
        return True
    excinfo = getattr(call, "excinfo", None)
    if excinfo is None:
        # No exception to look at. Nothing here can improve on "killed".
        return False
    import doctest

    import pytest

    # The exception classes that mean a test made a judgement and it went
    # against the code. `AssertionError` is the plain `assert`, rewritten or
    # not. `pytest.fail.Exception` -- `_pytest.outcomes.Failed` -- is
    # `pytest.fail()` and, importantly, a `pytest.raises` block whose expected
    # exception never arrived. `doctest.DocTestFailure` is a doctest whose
    # output did not match: the example is the assertion, and the mismatch is
    # the doctest objecting. All three are the test speaking, so all three are
    # ordinary kills.
    #
    # `doctest.UnexpectedException` is deliberately NOT here, and the pair is
    # the whole distinction this module draws: a doctest that printed the
    # wrong answer checked something, and a doctest whose code raised did not.
    deliberate = (AssertionError, pytest.fail.Exception, doctest.DocTestFailure)
    if issubclass(excinfo.type, _multiple_doctest_failures()):
        # `--doctest-continue-on-failure` collects a file's failures into one
        # wrapper. It is an ordinary kill only if every failure inside it is,
        # so a mismatch sitting beside a raised exception counts as an error.
        failures = getattr(getattr(excinfo, "value", None), "failures", None) or ()
        return not failures or not all(
            isinstance(f, doctest.DocTestFailure) for f in failures
        )
    return not issubclass(excinfo.type, deliberate)


def _multiple_doctest_failures() -> tuple[type[BaseException], ...]:
    """The wrapper `--doctest-continue-on-failure` raises, if this pytest has it.

    Returns:
        A one-tuple of `_pytest.doctest.MultipleDoctestFailures`, or an empty
        tuple where it does not exist -- it is private to pytest, so its
        absence is a version difference rather than a broken install.
    """
    try:
        from _pytest.doctest import MultipleDoctestFailures
    except ImportError:  # pragma: no cover - depends on the pytest version
        return ()
    return (MultipleDoctestFailures,)


# The module-level instance, for the subprocess path -- `-p moonbuggy.killreason`
# registers this module, and pytest finds the hooks below on it.
_ACTIVE = KillReason()


def pytest_exception_interact(node: object, call: Any, report: Any) -> None:
    """Module-level hook, delegating to the process's :data:`_ACTIVE` recorder.

    Args:
        node: the failing item or collector.
        call: the `CallInfo` for the failing phase.
        report: the report to stamp.
    """
    _ACTIVE.pytest_exception_interact(node, call, report)


def pytest_runtest_logreport(report: Any) -> None:
    """Module-level hook, delegating to the process's :data:`_ACTIVE` recorder.

    Args:
        report: one test report.
    """
    _ACTIVE.pytest_runtest_logreport(report)


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    """Module-level hook, delegating to the process's :data:`_ACTIVE` recorder.

    Args:
        session: the pytest session.
        exitstatus: the code pytest settled on.
    """
    _ACTIVE.pytest_sessionfinish(session, exitstatus)
