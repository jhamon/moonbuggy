"""The verdict-space invariants, made executable.

These are the constraints the QA seat holds sacred, pinned as tests rather than
prose so that "review like code" is mechanical: a change that breaks one of
these fails CI, it does not get argued about in a room.

Each invariant below maps to a clause of the versioned-contract procedure:
``contracts/CONTRACT-PROCEDURE.md`` Phase 2 ("QA clause"), and to the
deliverable register row D3 (the oracle-agreement gate) in TRACKING.md.

The four invariants:

1. **Vocabulary closure.** Every verdict a Result can carry is one of the seven
   public statuses in ``STATUS_KEYWORDS`` -- no free-text fallback. ``UNAPPLIED``
   is internal decision state (a request to retry coldly), not a verdict.

2. **SUSPICIOUS has exactly two causes, and they never collapse.** The
   flakiness detector (a selected test's outcome varied across unmutated runs)
   and the execution-crash path (pytest could not complete -- collection error,
   internal error, nothing collected). They are distinguished by mechanism, not
   merged into one "something odd happened."

3. **zero probes => no flakiness detector.** With ``--flaky-probe 0`` exactly one
   unmutated run happens, and flakiness *by construction* requires a second run
   to disagree with. So a SUSPICIOUS verdict at probe=0 can only come from the
   execution-crash path -- which is why the warm-session ``sys.modules``
   poisoning is a moonbuggy false positive, not genuine flakiness, and must not
   be "fixed" by hiding it behind a flaky classification.

4. **The machine record and the human line never disagree.** The status a JSONL
   record carries is the same status a plaintext line opens with, because both
   derive from one Result -- so a grepper and a ``jq`` reader describe the same
   mutant identically.
"""

import sys
from types import SimpleNamespace
from typing import cast

import moonbuggy.runner as runner_mod
from moonbuggy.baseline import classify
from moonbuggy.forkserver import CHILD_CRASHED, PYTEST_OK
from moonbuggy.killreason import PYTEST_TESTS_FAILED, TESTS_ERRORED
from moonbuggy.mutant import Mutant
from moonbuggy.report import (
    FINDING_STATUSES,
    KILL_STATUSES,
    STATUS_KEYWORDS,
    Record,
    render_line,
)

# ---------------------------------------------------------------------------
# Invariant 1 -- vocabulary closure (no free-text fallback)
# ---------------------------------------------------------------------------

# The seven public statuses, exactly. Order is irrelevant to the contract but
# spelled out so a rename fails loudly rather than silently widening the set.
_PUBLIC_STATUSES = frozenset(
    {
        "KILLED",
        "KILLED_BY_ERROR",
        "SURVIVED",
        "NO_COVERAGE",
        "TIMEOUT",
        "SUSPICIOUS",
        "SKIPPED",
    }
)


def test_status_vocabulary_is_exactly_the_closed_public_set():
    assert STATUS_KEYWORDS == _PUBLIC_STATUSES


def test_unapplied_is_internal_decision_state_not_a_verdict():
    # UNAPPLIED means "the warm grandchild could not swap the mutation in,
    # retry coldly" -- it is a request to the runner, never a status a caller
    # or a report reader should see as an answer about a mutant.
    assert "UNAPPLIED" not in STATUS_KEYWORDS


def test_kill_and_finding_partitions_are_exact_and_disjoint():
    # KILL_STATUSES + FINDING_STATUSES are the only two "verdict about the
    # mutation" families; SUSPICIOUS/TIMEOUT/SKIPPED are deliberately in
    # neither because neither is a claim the mutation went noticed or unnoticed.
    kill_and_finding = KILL_STATUSES | FINDING_STATUSES
    non_claim = {"TIMEOUT", "SUSPICIOUS", "SKIPPED"}
    assert kill_and_finding | non_claim == STATUS_KEYWORDS
    assert KILL_STATUSES.isdisjoint(FINDING_STATUSES)
    assert frozenset({"KILLED", "KILLED_BY_ERROR"}) == KILL_STATUSES
    assert frozenset({"SURVIVED", "NO_COVERAGE"}) == FINDING_STATUSES


# ---------------------------------------------------------------------------
# Invariant 2 & 3 -- SUSPICIOUS has exactly two causes, and probe=0 kills one
# ---------------------------------------------------------------------------


# The three exit codes that mean "pytest completed and reported an answer."
# Anything else -- pytest's own usage/collection/internal error codes (2-5) and
# NO_TESTS_COLLECTED (5) -- reaches the crash fallthrough, which is SUSPICIOUS.
# None of these may collide with forkserver's CHILD_CRASHED (70) or the
# killreason TESTS_ERRORED (72), or no runner can say which layer produced a
# number.
def test_completion_codes_do_not_collide_across_layers():
    codes = {PYTEST_OK, PYTEST_TESTS_FAILED, TESTS_ERRORED, CHILD_CRASHED}
    assert len(codes) == 4
    assert PYTEST_OK == 0
    assert PYTEST_TESTS_FAILED == 1
    assert TESTS_ERRORED == 72
    assert CHILD_CRASHED == 70


class _FakeProc:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _classify_via_crash_path(monkeypatch, tmp_path, returncode: int) -> str:
    fake_mutant = SimpleNamespace(module="m.py", line=1, mutated="1")
    calls: dict[str, int] = {}

    def fake_run(*args, **kwargs):
        return _FakeProc(calls["returncode"])

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
    calls["returncode"] = returncode
    return runner_mod._run_pytest(
        tmp_path, cast(Mutant, fake_mutant), [], 1.0, sys.executable, 0
    )


def test_crash_path_maps_completion_codes_to_the_three_clean_verdicts(
    monkeypatch, tmp_path
):
    assert _classify_via_crash_path(monkeypatch, tmp_path, PYTEST_OK) == "SURVIVED"
    assert (
        _classify_via_crash_path(monkeypatch, tmp_path, PYTEST_TESTS_FAILED) == "KILLED"
    )
    assert (
        _classify_via_crash_path(monkeypatch, tmp_path, TESTS_ERRORED)
        == "KILLED_BY_ERROR"
    )


def test_crash_path_is_the_only_non_flaky_source_of_suspicious(monkeypatch, tmp_path):
    # pytest could not complete -- collection error (2), internal error (3),
    # usage error (4), nothing collected (5) -- is a crash, and SUSPICIOUS is
    # the honest answer for it. This is the second, distinct cause.
    for code in (2, 3, 4, 5):
        assert _classify_via_crash_path(monkeypatch, tmp_path, code) == "SUSPICIOUS", (
            code
        )


def test_flakiness_requires_a_second_run_to_exist():
    # With a single unmutated run nothing can disagree with itself, so the
    # flaky set is empty. This is the *structural* reason probe=0 can never
    # produce a flaky SUSPICIOUS: one run is all probe=0 gathers.
    assert classify([{"test_a": "passed"}]) == (set(), set())
    # Empty input is also clean.
    assert classify([]) == (set(), set())


def test_flakiness_is_only_detected_when_outcomes_disagree():
    # Two runs that disagree mark the test flaky (and *not* consistently
    # failing).
    failing, flaky = classify([{"test_a": "passed"}, {"test_a": "failed"}])
    assert flaky == {"test_a"}
    assert failing == set()
    # Two runs that agree on pass are stable -- no flake, no failure.
    assert classify([{"test_a": "passed"}, {"test_a": "passed"}]) == (
        set(),
        set(),
    )


# ---------------------------------------------------------------------------
# Invariant 4 -- machine record and human line can never disagree
# ---------------------------------------------------------------------------


def test_machine_record_status_and_human_line_keyword_are_the_same_token():
    # The renderer opens every line with the record's own status keyword; it
    # never paraphrases, so `grep SURVIVED` and `jq .status` select the same
    # mutants. Exercising every status guards against a renderer edit that
    # special-cases one of them.
    for status in STATUS_KEYWORDS:
        record = {
            "status": status,
            "id": "m.py:1:c:0",
            "file": "m.py",
            "line": 1,
            "operator": "c",
            "category": "c",
            "nearest_test": None,
            "tests_run": 0,
            "duration": 0.0,
            "module_level": False,
            "suppressed": False,
            "logging_call": False,
            "original": "x",
            "mutated": "y",
            "diff": "- x\\n+ y",
            "killreason": None,
        }
        line = render_line(cast(Record, record))
        assert line.split()[0] == status
        assert line.split()[0] == record["status"]
