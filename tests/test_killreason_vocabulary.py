"""The killreason vocabulary is a versioned contract — pinned here as tests.

These tests enforce that the `KillReasonCode` enum is the single source of truth
for every killreason token consumed by the JSONL schema and the human trace. Each
test below maps to a clause of the frozen contract in `docs/contracts/killreason-v1.md`.
"""

from moonbuggy.killreason import (
    _KILLREASONS,
    ASSERTION_FAILED,
    EXECUTION_CRASH,
    FLAKY_PROBE,
    TEST_ERRORED,
    KillReasonCode,
)
from moonbuggy.report import STATUS_KEYWORDS

# ---------------------------------------------------------------------------
# Enum shape — exactly four members, each with a code and a human label
# ---------------------------------------------------------------------------


def test_killreason_enum_has_exactly_four_members():
    """The frozen vocabulary is exactly four tokens; adding one is a version bump."""
    members = set(KillReasonCode)
    assert members == {
        KillReasonCode.ASSERTION_FAILED,
        KillReasonCode.TEST_ERRORED,
        KillReasonCode.EXECUTION_CRASH,
        KillReasonCode.FLAKY_PROBE,
    }, f"Unexpected members: {members - set(KillReasonCode)}"


def test_every_member_has_a_code():
    """Each member's `.code` is the machine-readable string consumed by JSONL."""
    for member in KillReasonCode:
        assert isinstance(member.code, str)
        assert member.code, f"{member} has an empty code"


def test_every_member_has_a_human_label():
    """Each member's `.label` is the human-readable form for documentation."""
    for member in KillReasonCode:
        assert isinstance(member.label, str)
        assert member.label, f"{member} has an empty label"


def test_code_and_label_are_never_identical():
    """The code is machine-facing (snake_case); the label is human-facing (spaces)."""
    for member in KillReasonCode:
        assert member.code != member.label, (
            f"{member}: code and label are identical — "
            "one is machine-facing, the other human-facing"
        )


def test_every_code_is_a_single_lowercase_snake_case_token():
    """JSONL consumers split on these tokens; they are never free-text."""
    for member in KillReasonCode:
        assert member.code == member.code.lower()
        assert " " not in member.code
        assert member.code.isascii()


# ---------------------------------------------------------------------------
# The enum IS the module-level constants (same underlying value)
# ---------------------------------------------------------------------------


def test_module_constants_match_enum_values():
    """The existing bare-string constants are aliases for the enum members' codes."""
    assert KillReasonCode.ASSERTION_FAILED.code == ASSERTION_FAILED
    assert KillReasonCode.TEST_ERRORED.code == TEST_ERRORED
    assert KillReasonCode.EXECUTION_CRASH.code == EXECUTION_CRASH
    assert KillReasonCode.FLAKY_PROBE.code == FLAKY_PROBE


# ---------------------------------------------------------------------------
# _KILLREASONS is exactly the set of codes (one source of truth)
# ---------------------------------------------------------------------------


def test_killreasons_frozenset_is_exactly_the_enum_codes():
    """No killreason token exists outside the enum. _KILLREASONS derives from it."""
    codes = frozenset(member.code for member in KillReasonCode)
    assert codes == _KILLREASONS


# ---------------------------------------------------------------------------
# Mapping: each killreason code → the correct status keyword
# ---------------------------------------------------------------------------

_KILLREASON_STATUS_MAP = {
    KillReasonCode.ASSERTION_FAILED: "KILLED",
    KillReasonCode.TEST_ERRORED: "KILLED_BY_ERROR",
    KillReasonCode.EXECUTION_CRASH: "SUSPICIOUS",
    KillReasonCode.FLAKY_PROBE: "SUSPICIOUS",
}

_NON_KILL_STATUSES = frozenset({"SURVIVED", "NO_COVERAGE", "TIMEOUT", "SKIPPED"})


def test_every_killreason_maps_to_a_valid_public_status():
    for reason, status in _KILLREASON_STATUS_MAP.items():
        assert status in STATUS_KEYWORDS, (
            f"{reason} maps to {status}, which is not in STATUS_KEYWORDS"
        )


def test_non_kill_statuses_map_to_no_killreason():
    """Survivors, timeouts, uncovered and skipped mutants have no reason — the
    field is None because no reason applies, not because it wasn't recorded."""
    for status in _NON_KILL_STATUSES:
        assert status in STATUS_KEYWORDS


def test_every_status_is_accounted_for():
    """Every public status is either in the killreason map or the no-reason set."""
    accounted = frozenset(_KILLREASON_STATUS_MAP.values()) | _NON_KILL_STATUSES
    assert accounted == STATUS_KEYWORDS, (
        f"Missing from accounting: {STATUS_KEYWORDS - accounted}"
    )


# ---------------------------------------------------------------------------
# SUSPICIOUS's dual cause: the two SUSPICIOUS killreasons are distinct
# ---------------------------------------------------------------------------


def test_suspicious_has_exactly_two_distinct_killreasons():
    """SUSPICIOUS comes from either an execution crash or a flaky probe.
    They are distinguished by mechanism, never collapsed."""
    suspicious_reasons = {
        r for r, s in _KILLREASON_STATUS_MAP.items() if s == "SUSPICIOUS"
    }
    assert suspicious_reasons == {
        KillReasonCode.EXECUTION_CRASH,
        KillReasonCode.FLAKY_PROBE,
    }


# ---------------------------------------------------------------------------
# Frozen human labels (contract)
# ---------------------------------------------------------------------------


def test_human_labels_are_frozen():
    """The human labels are part of the versioned contract. Changing one is a
    version bump — agents parsing the human trace depend on them."""
    assert KillReasonCode.ASSERTION_FAILED.label == "assertion failed"
    assert KillReasonCode.TEST_ERRORED.label == "test errored"
    assert KillReasonCode.EXECUTION_CRASH.label == "execution crash"
    assert KillReasonCode.FLAKY_PROBE.label == "flaky probe"
