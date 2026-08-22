"""`KILLED_BY_ERROR`: the mutant died, but no test actually checked anything.

A kill by failed assertion proves a test *checked* the behaviour the mutation
changed. A kill by `NameError` proves only that a test *executes* the line --
the mutation broke the code badly enough that touching it explodes. Reporting
both as `KILLED` was tolerable while every operator produced a program that
still ran; `statement_deletion` makes crash-kills the common case, and a kill
rate that cannot tell the two apart stops measuring anything.

Three layers are pinned here:

- the *classifier* -- which exceptions count as a test objecting;
- the *transport* -- that the answer reaches every runner through pytest's
  exit code, so the fork path, the warm path and the subprocess path cannot
  disagree;
- the *contract* -- that it is a kill for scoring and for the exit code, and
  a separate keyword for reading.
"""

import doctest
import subprocess
import sys
import textwrap

import pytest

from moonbuggy.humanreport import render_footer, score_text
from moonbuggy.killreason import FLAG, TESTS_ERRORED, KillReason
from moonbuggy.report import FINDING_STATUSES, KILL_STATUSES, STATUS_KEYWORDS


class FakeReport:
    """The parts of a pytest `TestReport` this plugin looks at."""

    def __init__(self, when="call", failed=True):
        self.when = when
        self.failed = failed


class FakeCall:
    """The parts of a pytest `CallInfo` this plugin looks at."""

    def __init__(self, exception):
        self.excinfo = None if exception is None else FakeExcInfo(exception)


class FakeExcInfo:
    def __init__(self, exception):
        self.type = exception


class FakeSession:
    def __init__(self):
        self.exitstatus = 1


def settle(*exceptions, when="call"):
    """Run one session's worth of failures through the recorder."""
    recorder = KillReason()
    for exception in exceptions:
        report = FakeReport(when=when)
        recorder.pytest_exception_interact(None, FakeCall(exception), report)
        recorder.pytest_runtest_logreport(report)
    return recorder


# --- the classifier ---------------------------------------------------------


def test_an_assertion_failure_is_an_ordinary_kill():
    assert not settle(AssertionError).errored


def test_pytest_fail_is_an_ordinary_kill():
    """`pytest.fail()` is the test speaking as deliberately as `assert` is --
    and so is a `pytest.raises` block whose expected exception never arrived,
    which raises the same class."""
    assert not settle(pytest.fail.Exception).errored


def test_an_uncaught_exception_is_a_crash_kill():
    assert settle(NameError).errored
    assert settle(AttributeError).errored
    assert settle(TypeError).errored


def test_a_doctest_mismatch_is_an_ordinary_kill():
    """A doctest example *is* the assertion. When its output does not match,
    the doctest checked the mutated behaviour and objected -- which is the
    thing mutation testing measures, and is not a crash.

    Regression: `doctest.DocTestFailure` is neither an `AssertionError` nor a
    pytest failure, so the first version of the classifier called every
    doctest-caught mutant a crash-kill. A project whose suite is mostly
    doctests would have had its kill quality reported as near-worthless.
    """
    assert not settle(doctest.DocTestFailure).errored


def test_a_doctest_whose_code_raised_is_a_crash_kill():
    """The other half of the pair, and the reason the two doctest exceptions
    are treated differently: the example never got to compare anything."""
    assert settle(doctest.UnexpectedException).errored


def test_a_failure_outside_the_call_phase_is_a_crash_kill():
    """A fixture that raised has not checked anything either."""
    assert settle(AssertionError, when="setup").errored


def test_one_real_assertion_makes_the_whole_kill_ordinary():
    """Without `-x` a mutation can fail several tests. One of them objecting
    is direct evidence that the suite checks the behaviour; the crashes
    beside it are a consequence, not the finding."""
    assert not settle(NameError, AssertionError).errored


def test_an_unclassified_failure_stays_an_ordinary_kill():
    """`call_and_report` calls `pytest_runtest_logreport` *before*
    `pytest_exception_interact`, so an unflagged report at logreport time has
    not been classified yet. Folding it in as an assertion there is the bug
    that made every crash-kill report `KILLED`."""
    recorder = KillReason()
    recorder.pytest_runtest_logreport(FakeReport())

    assert not recorder.test_errored
    assert not recorder.assertion_failed


def test_the_classification_is_stamped_on_the_report_for_xdist():
    """An xdist worker classifies; the controller only sees the report. The
    flag rides pytest's own report serialisation, which carries unknown
    attributes through."""
    report = FakeReport()
    KillReason().pytest_exception_interact(None, FakeCall(NameError), report)

    assert getattr(report, FLAG) is True


def test_a_controller_folds_in_a_report_a_worker_classified():
    recorder = KillReason()
    report = FakeReport()
    setattr(report, FLAG, True)
    recorder.pytest_runtest_logreport(report)

    assert recorder.errored


# --- the transport ----------------------------------------------------------


def test_the_exit_code_is_rewritten_only_for_a_crash_kill():
    session = FakeSession()
    settle(NameError).pytest_sessionfinish(session, 1)

    assert session.exitstatus == TESTS_ERRORED


def test_an_ordinary_kill_keeps_pytests_own_exit_code():
    session = FakeSession()
    settle(AssertionError).pytest_sessionfinish(session, 1)

    assert session.exitstatus == 1


def test_a_passing_session_is_never_rewritten():
    """`SURVIVED` is exit 0 and must stay exit 0 whatever happened in the
    session -- an errored-but-not-failed run is not a kill."""
    session = FakeSession()
    session.exitstatus = 0
    settle(NameError).pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0


def test_the_plugin_module_rewrites_a_real_pytest_run(tmp_path):
    """End to end through `-p moonbuggy.killreason`, which is how the
    subprocess runner registers it. A real pytest, a real `NameError`, and
    the exit code every runner reads."""
    (tmp_path / "test_crash.py").write_text(
        textwrap.dedent(
            """
            def test_crashes():
                undefined_name

            def test_asserts():
                assert 1 == 2
            """
        )
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-x",
        "-p",
        "no:cacheprovider",
        "-p",
        "moonbuggy.killreason",
    ]

    crash = subprocess.run(
        [*command, "test_crash.py::test_crashes"], cwd=tmp_path, capture_output=True
    )
    objection = subprocess.run(
        [*command, "test_crash.py::test_asserts"], cwd=tmp_path, capture_output=True
    )

    assert crash.returncode == TESTS_ERRORED
    assert objection.returncode == 1


# --- the contract -----------------------------------------------------------


def test_it_is_part_of_the_status_vocabulary():
    assert "KILLED_BY_ERROR" in STATUS_KEYWORDS


def test_it_counts_as_a_kill_for_the_score():
    """A crash-kill is still a kill: the mutation was noticed. Dropping it
    from the numerator would report a *lower* score for a suite that did
    notice, which is the opposite of the point."""
    assert {"KILLED", "KILLED_BY_ERROR"} == KILL_STATUSES
    assert score_text({"KILLED": 1, "KILLED_BY_ERROR": 1}) == "2/2 killed, 100%"


def test_it_is_not_a_finding():
    """Findings are what exit 1 and what the accepted-equivalents ledger can
    speak for. A kill is neither -- there is nothing for a human to explain
    about a mutant the suite caught."""
    assert "KILLED_BY_ERROR" not in FINDING_STATUSES


def test_the_footer_says_how_many_kills_were_crashes():
    counts = dict.fromkeys(STATUS_KEYWORDS, 0)
    counts.update({"KILLED": 3, "KILLED_BY_ERROR": 7})

    footer = render_footer(counts, 1.0, ".moonbuggy/results.jsonl")

    assert "10/10 killed, 100%" in footer
    assert "7 of 10 kills came from a test erroring out" in footer
    assert "exit 0 -- nothing survived" in footer


def test_the_footer_says_nothing_when_no_kill_was_a_crash():
    """The ordinary `default`-tier run. A line reporting zero of something is
    a line about a thing that did not happen."""
    counts = dict.fromkeys(STATUS_KEYWORDS, 0)
    counts.update({"KILLED": 3})

    assert "erroring out" not in render_footer(counts, 1.0, "results.jsonl")
