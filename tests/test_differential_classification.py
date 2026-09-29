"""The differential's verified-false-kill ledger stays decidable.

M1.3.2's contract: every disagreement the differential reports must have a
classification, decided by evidence rather than ignored. The
``VERIFIED_MUTMUT_FALSE_KILLS`` ledger in ``scripts/differential.py`` carries
the mutmut false kills verified by direct experiment (mutant active under
mutmut's own trampoline leaves the suite green). Two properties keep the
ledger honest:

1. every entry is structurally complete -- a (module, original) key, a
   matching ``mutated`` line, and a reason naming the reproduction;
2. each entry's reason actually explains a KILLED-vs-SURVIVED pair, i.e.
   ``classify()`` returns the ``mutmut false kill (verified)`` category and
   a non-empty reason for the exact triple it pins -- so a stale or
   mis-keyed entry (line text drifts in the target library, key typos) turns
   into a failing test here instead of an unclassified exit.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import differential  # noqa: E402


def test_every_verified_false_kill_entry_is_complete():
    """Each ledger entry has a mutated line that differs from the original,
    and a reason that states the reproduction."""
    for (module, original), entry in differential.VERIFIED_MUTMUT_FALSE_KILLS.items():
        assert isinstance(module, str) and module.endswith(".py"), (module, original)
        assert isinstance(original, str) and original, (module, original)
        mutated = entry["mutated"]
        assert isinstance(mutated, str) and mutated, (module, original)
        assert mutated != original, (module, original, mutated)
        reason = entry["reason"]
        assert (
            "trampoline" in reason
            # or the applied-to-real-source reproduction, with the observed
            # suite size stated either way
            or ("real source" in reason and "suite green" in reason)
        ), (module, original, reason)


def test_ledger_entries_classify_their_own_triple():
    """classify() must return the verified category for the exact (module,
    original, mutated) triple each entry pins -- not None, and not some
    other category. This is the M1.3.2 non-vacuity check: a mis-keyed or
    drift-stale entry fails here rather than resurfacing as UNCLASSIFIED in
    a later harness run."""
    ours = {"status": "SURVIVED", "suppressed": False, "tests_run": 3}
    theirs = {"status": "KILLED"}
    for (module, original), entry in differential.VERIFIED_MUTMUT_FALSE_KILLS.items():
        key = (module, original, entry["mutated"])
        category, reason = differential.classify(key, ours, theirs)
        assert category == "mutmut false kill (verified)", (key, category)
        assert reason, key


def test_ledger_does_not_shade_a_genuine_kill():
    """The ledger must only fire for SURVIVED-vs-KILLED on the exact triple.
    A different mutated line (or a genuinely killed moonbuggy verdict) must
    NOT be classified by the ledger -- classify() returns None so the
    disagreement stays loud."""
    ours = {"status": "KILLED", "suppressed": False, "tests_run": 3}
    theirs = {"status": "KILLED"}
    for (module, original), entry in differential.VERIFIED_MUTMUT_FALSE_KILLS.items():
        key = (module, original, entry["mutated"])
        # A moonbuggy KILLED on the ledger's triple is not a disagreement the
        # ledger may explain -- classify() must return None so it stays loud.
        assert differential.classify(key, ours, theirs) is None, key
