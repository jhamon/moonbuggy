"""The verdict-trace record is a contract, pinned by a golden file.

``moonbuggy run <id> --trace-json`` emits one ``Verification.trace()`` mapping
per mutant, and the same line is appended to ``traces.jsonl``. Its shape is the
``TRACE_SCHEMA`` contract (version 1, frozen by ``src/moonbuggy/verify.py``):
the top-level identity of the mutant, the ``selection`` that consulted it, and
the ``verdict`` with the evidence that produced it. This test freezes the shape
the way ``test_survivor_export.py`` freezes the findings feed and
``test_harness_output_schema.py`` freezes the numbers pipe: the golden document
is built by calling ``Verification.trace()`` itself and compared byte-for-byte
against a committed fixture, so any key that is added, renamed, re-typed, or
re-ordered fails here rather than drifting past a consumer.

The completeness rules -- *which* evidence each of the seven statuses carries --
are the property test's job (``test_trace_properties.py``); this file pins the
exact key set and serialization, which a property test cannot.
"""

import json
from pathlib import Path

from moonbuggy.killreason import KillReasonCode
from moonbuggy.mutant import Mutant
from moonbuggy.runner import Result
from moonbuggy.verify import TRACE_SCHEMA, Verification

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "sample_project.trace.json"

# The version the golden carries; bump ONLY in lockstep with TRACE_SCHEMA in
# src/moonbuggy/verify.py, which is what a reader keys off.
SCHEMA_VERSION = 1

# The canonical key set, frozen here for review rather than trusted to match
# the golden file silently. A field promoted into this set is a version bump.
TRACE_FIELDS = frozenset(
    {
        "trace_schema",
        "id",
        "file",
        "line",
        "operator",
        "original",
        "mutated",
        "selection",
        "verdict",
    }
)
SELECTION_FIELDS = frozenset({"selected", "failed"})
VERDICT_FIELDS = frozenset({"status", "killreason", "assert", "crash"})


def _golden_trace() -> dict[str, object]:
    """Build the reference trace by calling ``Verification.trace()`` itself.

    The fixture is a photograph of the real emitter, not a hand-written
    lookalike: the same method the CLI prints and ``traces.jsonl`` persists
    produces every byte of it. If ``trace()`` changes, this function changes
    with it and the committed golden comparison names the diff.

    Returns:
        A conforming trace-schema-1 verdict-trace record for the ordinary
        traceable kill: a KILLED verdict naming the assertion that died.
    """
    mutant = Mutant(
        id="app/pricing.py:14:comparison_swap:0",
        module="app/pricing.py",
        line=14,
        operator="comparison_swap",
        original="if quantity > BULK:",
        mutated="if quantity >= BULK:",
    )
    selected = (
        "tests/test_pricing.py::test_bulk",
        "tests/test_pricing.py::test_single",
    )
    failed = ("tests/test_pricing.py::test_bulk",)
    result = Result(
        mutant,
        "KILLED",
        tests_run=2,
        duration=0.0314,
        nearest_test=selected[0],
        killreason=KillReasonCode.ASSERTION_FAILED,
    )
    return Verification(result, selected, failed).trace()


def test_the_trace_schema_version_is_frozen():
    """The golden's ``trace_schema`` const and the module's pin agree."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["trace_schema"] == SCHEMA_VERSION
    assert TRACE_SCHEMA == SCHEMA_VERSION
    assert _golden_trace()["trace_schema"] == SCHEMA_VERSION


def test_the_golden_trace_matches_the_emitter_byte_for_byte():
    """The committed fixture is exactly what ``trace()`` serializes today."""
    golden_text = GOLDEN.read_text(encoding="utf-8")
    emitted = json.dumps(_golden_trace(), sort_keys=True, indent=2) + "\n"
    assert emitted == golden_text


def test_the_key_set_is_frozen():
    """The trace's key set is the frozen one, at every nesting level."""
    trace = _golden_trace()
    assert set(trace) == TRACE_FIELDS
    assert set(trace["selection"]) == SELECTION_FIELDS  # type: ignore[arg-type]
    assert set(trace["verdict"]) == VERDICT_FIELDS  # type: ignore[arg-type]


def test_the_golden_round_trips_through_json():
    """Machine-readable by construction: serialize, parse, compare."""
    trace = _golden_trace()
    assert json.loads(json.dumps(trace)) == trace
