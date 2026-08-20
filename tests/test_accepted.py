"""The accepted-equivalents ledger: matching, drift, and the run tally.

The two design questions the ledger had to answer before its format was
written down are both here, each with the test that pins the answer:

- **Drift.** An acceptance whose line has been edited must not be honoured.
- **Id stability.** A line inserted above shifts every id below it, and the
  ledger must not lose its entries to an unrelated edit.
"""

import pytest

from moonbuggy.accepted import (
    AcceptError,
    Entry,
    entry_for,
    fingerprint,
    load,
    resolve,
    save,
    tally,
)
from moonbuggy.mutant import Mutant


def mutant(
    module="lib.py", line=3, operator="comparison_swap", index=0, mutated="a < b"
):
    return Mutant(
        id=f"{module}:{line}:{operator}:{index}",
        module=module,
        line=line,
        operator=operator,
        original="a > b",
        mutated=mutated,
    )


def record(mutant, status="SURVIVED"):
    return {
        "id": mutant.id,
        "status": status,
        "file": mutant.module,
        "line": mutant.line,
        "operator": mutant.operator,
        "category": mutant.operator,
        "nearest_test": None,
        "tests_run": 1,
        "duration": 0.1,
        "module_level": False,
        "suppressed": False,
        "original": mutant.original,
        "mutated": mutant.mutated,
        "diff": f"- {mutant.original}\n+ {mutant.mutated}",
    }


def accepted_entry(m, reason="equivalent"):
    return entry_for(
        m.id,
        m.module,
        m.operator,
        m.original,
        m.mutated,
        reason=reason,
        at="2026-01-01",
    )


def test_the_fingerprint_is_content_not_position():
    # Two mutants at different lines of different files, same mutation: the
    # fingerprint cannot be what distinguishes them, because it is the thing
    # that has to survive a line number moving.
    here = fingerprint("comparison_swap", "a > b", "a < b")
    there = fingerprint("comparison_swap", "a > b", "a < b")
    assert here == there
    assert here != fingerprint("comparison_swap", "a > b", "a <= b")
    assert here != fingerprint("comparison_swap", "a >= b", "a < b")
    assert here != fingerprint("boundary_shift", "a > b", "a < b")


def test_a_ledger_round_trips_through_toml(tmp_path):
    path = tmp_path / "accepted.toml"
    entry = accepted_entry(mutant(), reason='both "branches" return\tthe same value')
    save(path, [entry])

    assert load(path) == (entry,)
    assert "moonbuggy" in path.read_text()


def test_a_missing_ledger_is_an_empty_one_not_an_error(tmp_path):
    assert load(tmp_path / "nothing.toml") == ()


def test_a_malformed_ledger_is_an_actionable_error(tmp_path):
    path = tmp_path / "accepted.toml"
    path.write_text("[[accepted]\nid = ")
    with pytest.raises(AcceptError) as error:
        load(path)
    assert str(path) in str(error.value)


def test_a_ledger_with_two_entries_for_one_id_is_refused(tmp_path):
    # The shape a bad merge leaves behind. Two reasons for one mutant means
    # nobody can say which decision is in force, and guessing is how the wrong
    # one gets honoured.
    path = tmp_path / "accepted.toml"
    save(path, [accepted_entry(mutant()), accepted_entry(mutant(), reason="other")])
    with pytest.raises(AcceptError) as error:
        load(path)
    assert "lib.py:3:comparison_swap:0" in str(error.value)


def test_an_unchanged_mutant_is_accepted():
    m = mutant()
    resolution = resolve([accepted_entry(m)], [m])
    assert resolution.live[m.id].reason == "equivalent"
    assert resolution.stale == ()
    assert resolution.relocated == {}


def test_an_edited_line_makes_the_acceptance_stale_not_honoured():
    # Design question 1. The id still resolves, but the code under it is not
    # the code somebody reviewed, so honouring the old decision would let a
    # real regression through behind it.
    old = mutant()
    edited = mutant(mutated="a <= b")
    resolution = resolve([accepted_entry(old)], [edited])

    assert resolution.live == {}
    assert [entry.id for entry in resolution.stale] == [old.id]


def test_a_stale_acceptance_counts_as_unexplained():
    old = mutant()
    edited = mutant(mutated="a <= b")
    resolution = resolve([accepted_entry(old)], [edited])
    result = tally([record(edited)], resolution, path="accepted.toml", gating=True)

    assert result.accepted == ()
    assert result.unexplained == (edited.id,)
    assert result.summary()["stale"] == 1


def test_an_id_shifted_by_an_insertion_above_keeps_its_acceptance():
    # Design question 2. The line moved from 3 to 8 because something was
    # inserted above it; the mutation is character for character the one that
    # was reviewed. A ledger that lost the entry here would be worse than no
    # ledger, because the loss is silent.
    old = mutant(line=3)
    moved = mutant(line=8)
    resolution = resolve([accepted_entry(old)], [moved])

    assert resolution.live[moved.id].id == old.id
    assert resolution.relocated == {old.id: moved.id}
    assert resolution.stale == ()


def test_a_relocation_never_steals_an_exactly_matched_mutant():
    # Two identical mutations in one file, one of them exactly matching its
    # entry. The other entry must not claim the mutant already spoken for.
    here = mutant(line=3)
    there = mutant(line=8)
    resolution = resolve([accepted_entry(here), accepted_entry(there)], [here, there])

    assert set(resolution.live) == {here.id, there.id}
    assert resolution.live[here.id].id == here.id
    assert resolution.live[there.id].id == there.id


def test_an_ambiguous_relocation_is_refused_rather_than_guessed():
    # One entry, two identical candidates, neither with a matching id.
    # Equivalence is a judgement about a line in its context, so picking one
    # at random would honour a decision nobody made about it.
    entry = accepted_entry(mutant(line=3))
    resolution = resolve([entry], [mutant(line=8), mutant(line=12)])

    assert resolution.live == {}
    assert [e.id for e in resolution.ambiguous] == [entry.id]


def test_a_mutant_not_in_this_run_is_orphaned_not_stale():
    # A diff-scoped run generates a handful of mutants; every other acceptance
    # is simply not this run's business, and reporting them as drift would
    # make `--since` unusable with a ledger.
    entry = accepted_entry(mutant(module="other.py"))
    resolution = resolve([entry], [mutant()])

    assert resolution.stale == ()
    assert [e.id for e in resolution.orphaned] == [entry.id]


def test_only_findings_are_counted_as_accepted():
    # An accepted mutant that the suite has since learned to kill is KILLED.
    # Counting it as accepted would report a stale decision as live work.
    m = mutant()
    resolution = resolve([accepted_entry(m)], [m])
    result = tally(
        [record(m, "KILLED")], resolution, path="accepted.toml", gating=False
    )

    assert result.accepted == ()
    assert result.unexplained == ()


def test_the_tally_separates_accepted_findings_from_unexplained_ones():
    accepted = mutant(line=3)
    unexplained = mutant(line=9)
    uncovered = mutant(line=20)
    resolution = resolve([accepted_entry(accepted)], [accepted, unexplained, uncovered])
    result = tally(
        [
            record(accepted),
            record(unexplained),
            record(uncovered, "NO_COVERAGE"),
        ],
        resolution,
        path=".moonbuggy/accepted.toml",
        gating=True,
    )

    assert result.accepted == (accepted.id,)
    assert set(result.unexplained) == {unexplained.id, uncovered.id}
    assert result.summary() == {
        "accepted": 1,
        "unexplained": 2,
        "stale": 0,
        "ambiguous": 0,
        "orphaned": 0,
        "relocated": 0,
        "ledger": ".moonbuggy/accepted.toml",
        "fail_on_unexplained": True,
    }


def test_the_reason_reaches_the_run_as_a_lookup_by_current_id():
    old = mutant(line=3)
    moved = mutant(line=8)
    resolution = resolve([accepted_entry(old, reason="unreachable branch")], [moved])

    assert resolution.reasons() == {moved.id: "unreachable branch"}


def test_an_entry_carries_the_fingerprint_of_the_mutation_it_was_made_for():
    m = mutant()
    entry = accepted_entry(m)
    assert isinstance(entry, Entry)
    assert entry.fingerprint == fingerprint(m.operator, m.original, m.mutated)
    assert entry.file == m.module
