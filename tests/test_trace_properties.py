"""Property test: verdict-trace completeness (`Verification.trace()`).

The trace is the persisted verdict-trace audit log's per-mutant record,
consumed by `moonbuggy run <id> --trace-json`. Its contract is completeness:
**every verdict carries the exact evidence that produced it** -- a KILLED names
the assertion that died, a SURVIVED identifies the surviving mutation, a
NO_COVERAGE says nothing ran, and a SUSPICIOUS names its cause. Hypothesis
picks the verdict/failed-test combinations so no status's evidence shape is
pinned only by the examples its author thought of.

Fast, in-process: it checks the trace *shape*, not the runner. The end-to-end
behaviour -- a real coverage pass and a real pytest subprocess -- lives in
`test_cli.py` under `pytest.mark.slow`.
"""

import json

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from moonbuggy.killreason import KillReasonCode
from moonbuggy.mutant import Mutant
from moonbuggy.runner import Result
from moonbuggy.verify import TRACE_SCHEMA, Verification

MODULE = """\
BULK = 10


def is_bulk(quantity):
    return quantity >= BULK
"""

STATUSES = [
    "KILLED",
    "KILLED_BY_ERROR",
    "SURVIVED",
    "NO_COVERAGE",
    "TIMEOUT",
    "SUSPICIOUS",
    "SKIPPED",
]

NODE_IDS = ["t.py::a", "t.py::b"]


def _verification(status, failed, selected):
    """A Verification as the runner would build it for this outcome."""
    mutant = Mutant(
        id="shipping.py:5:comparison_swap:0",
        module="shipping.py",
        line=5,
        operator="comparison_swap",
        original="return quantity >= BULK",
        mutated="return quantity > BULK",
    )
    # The killreason the runner stamps, per `runner._killreason_for` and the
    # two SUSPICIOUS causes: a run-path verdict carries its reason, a
    # SUSPICIOUS carries execution_crash or flaky_probe, and statuses that
    # say nothing about the mutation carry None.
    killreason = None
    if status == "KILLED":
        killreason = KillReasonCode.ASSERTION_FAILED
    elif status == "KILLED_BY_ERROR":
        killreason = KillReasonCode.TEST_ERRORED
    elif status == "SUSPICIOUS":
        killreason = KillReasonCode.EXECUTION_CRASH
    result = Result(
        mutant,
        status,
        tests_run=len(selected),
        duration=0.5,
        nearest_test=selected[0] if status == "SURVIVED" and selected else None,
        killreason=killreason,
    )
    return Verification(result, tuple(selected), tuple(failed))


status = st.sampled_from(STATUSES)
failed = st.lists(st.sampled_from(NODE_IDS), max_size=2)


@settings(max_examples=300, deadline=None)
@given(status=status, failed=failed)
def test_every_verdict_carries_exactly_the_evidence_that_produced_it(status, failed):
    # Unreachable in the real runner: a KILLED verdict means pytest exited 1,
    # which means at least one selected test failed. Exclude, don't weaken.
    assume(not (status in ("KILLED", "KILLED_BY_ERROR") and not failed))
    # And the runner's settling rules: NO_COVERAGE and SKIPPED run nothing, so
    # they have no selection and no failures.
    if status in ("NO_COVERAGE", "SKIPPED"):
        selected: list[str] = []
    else:
        selected = NODE_IDS

    trace = Verification(
        _verification(status, failed, selected).result, tuple(selected), tuple(failed)
    ).trace()

    # Machine-readable by construction: round-trips through JSON.
    assert json.loads(json.dumps(trace)) == trace
    assert trace["trace_schema"] == TRACE_SCHEMA

    ev = trace["verdict"]
    assert set(ev) == {"status", "killreason", "assert", "crash"}
    assert ev["status"] == status

    # KILLED / KILLED_BY_ERROR name the exact tests that objected -- the
    # traceable claim a reviewer checks first.
    if status in ("KILLED", "KILLED_BY_ERROR"):
        assert ev["assert"] == sorted(failed)
        assert ev["crash"] is None
    else:
        assert ev["assert"] == []

    # SURVIVED: no test objected, so the evidence is the surviving mutation
    # itself, carried in the trace's own `original`/`mutated` fields.
    if status == "SURVIVED":
        assert trace["original"] == "return quantity >= BULK"
        assert trace["mutated"] == "return quantity > BULK"
        assert ev["crash"] is None

    # NO_COVERAGE says why nothing ran: empty selection, no asserted deaths.
    if status == "NO_COVERAGE":
        assert trace["selection"]["selected"] == []
        assert ev["crash"] is None

    # SUSPICIOUS names its cause. At --flaky-probe 0 the flaky set is empty
    # by construction, so only the execution-crash path can produce it; the
    # killreason token distinguishes the two and the trace carries it.
    if status == "SUSPICIOUS":
        assert ev["killreason"] in ("execution_crash", "flaky_probe")
        if ev["killreason"] == "execution_crash":
            assert ev["crash"] is not None

    # Statuses that say nothing about the mutation carry no fake evidence.
    if status in ("TIMEOUT", "SKIPPED"):
        assert ev["killreason"] is None
        assert ev["crash"] is None
