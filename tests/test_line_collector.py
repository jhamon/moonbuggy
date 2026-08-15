"""The sys.monitoring line->test collector (criterion D1, Spike B).

The line->test map is what makes coverage-guided selection possible: for a
mutant on line N, run only the tests that actually execute line N. Getting this
wrong in the direction of *missing* a covering test produces a false SURVIVED,
so the tests here lean on the cases where a naive implementation would drop a
test -- cross-file callers and module-level lines.
"""

import sys

import pytest

from moonbuggy.linemap import LineCollector

MODULE = '''\
CONSTANT = 10


def small(x):
    if x > CONSTANT:
        return "big"
    return "small"


def caller(x):
    return small(x)
'''


@pytest.fixture
def probe(tmp_path):
    path = tmp_path / "collector_probe.py"
    path.write_text(MODULE)
    sys.path.insert(0, str(tmp_path))
    yield path
    sys.path.remove(str(tmp_path))
    sys.modules.pop("collector_probe", None)


def test_records_the_line_a_test_executes(probe):
    import collector_probe

    collector = LineCollector({str(probe)})
    with collector.recording("test_small"):
        collector_probe.small(1)

    assert "test_small" in collector.tests_covering(str(probe), 7)


def test_does_not_record_lines_the_test_never_reaches(probe):
    import collector_probe

    collector = LineCollector({str(probe)})
    with collector.recording("test_small"):
        collector_probe.small(1)

    # Line 6 is the `return "big"` branch, never taken for x=1.
    assert collector.tests_covering(str(probe), 6) == set()


def test_attributes_lines_to_the_right_test(probe):
    import collector_probe

    collector = LineCollector({str(probe)})
    with collector.recording("test_big"):
        collector_probe.small(99)
    with collector.recording("test_small"):
        collector_probe.small(1)

    assert collector.tests_covering(str(probe), 6) == {"test_big"}
    assert collector.tests_covering(str(probe), 7) == {"test_small"}


def test_records_indirect_callers(probe):
    # The cross-file case in miniature: the test calls caller(), which calls
    # small(). Both lines must be attributed to the test, or a mutant on the
    # inner line runs zero tests and reports a false SURVIVED.
    import collector_probe

    collector = LineCollector({str(probe)})
    with collector.recording("test_caller"):
        collector_probe.caller(1)

    assert "test_caller" in collector.tests_covering(str(probe), 7)
    assert "test_caller" in collector.tests_covering(str(probe), 11)


def test_ignores_files_outside_the_target_set(probe):
    import collector_probe

    collector = LineCollector({str(probe)})
    with collector.recording("test_small"):
        collector_probe.small(1)
        "".join(["a", "b"])

    covered_files = {path for path, _ in collector.mapping}
    assert covered_files == {str(probe)}


def test_records_nothing_outside_a_recording_block(probe):
    import collector_probe

    collector = LineCollector({str(probe)})
    collector_probe.small(1)

    assert collector.mapping == {}


def test_session_holds_one_tool_id_across_many_tests(probe):
    # A real coverage pass claims the monitoring tool id once and attributes
    # per test inside it; per-test registration would charge that cost to every
    # test in the suite.
    import collector_probe

    collector = LineCollector({str(probe)})
    with collector.session():
        with collector.for_test("test_big"):
            collector_probe.small(99)
        with collector.for_test("test_small"):
            collector_probe.small(1)

    assert collector.tests_covering(str(probe), 6) == {"test_big"}
    assert collector.tests_covering(str(probe), 7) == {"test_small"}


def test_lines_outside_any_test_are_not_attributed(probe):
    # Inside a session but between tests, there is no current test. Recording
    # those lines against None would pollute the map with a phantom test id.
    import collector_probe

    collector = LineCollector({str(probe)})
    with collector.session():
        collector_probe.small(1)

    assert collector.tests_covering(str(probe), 7) == set()


def test_identical_functions_in_different_files_do_not_collide(tmp_path):
    """Regression: code objects compare and hash BY VALUE, not by identity.

    CPython's code comparison covers name, flags, first line number, bytecode
    and constants -- but NOT co_filename. Two byte-identical functions at the
    same line number in different files are therefore equal and hash equal, so
    any cache keyed on the code object silently merges them.

    Found by the Spike B benchmark, where ten structurally identical generated
    modules produced a map containing exactly one file. In a real project this
    is two copies of the same small helper: coverage for one gets attributed to
    the other, the wrong tests are selected for a mutant, and the result is a
    false SURVIVED -- the failure mode with no external symptom.
    """
    source = 'def helper(x):\n    return x + 1\n'
    first = tmp_path / "alpha.py"
    second = tmp_path / "beta.py"
    first.write_text(source)
    second.write_text(source)
    sys.path.insert(0, str(tmp_path))
    try:
        import alpha
        import beta

        collector = LineCollector({str(first), str(second)})
        with collector.session():
            with collector.for_test("test_alpha"):
                alpha.helper(1)
            with collector.for_test("test_beta"):
                beta.helper(1)

        assert collector.tests_covering(str(first), 2) == {"test_alpha"}
        assert collector.tests_covering(str(second), 2) == {"test_beta"}
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("alpha", None)
        sys.modules.pop("beta", None)


def test_two_collectors_can_run_in_sequence(probe):
    # The monitoring tool id must be released on exit, or the second collector
    # cannot register and the whole coverage pass dies on the second file.
    import collector_probe

    first = LineCollector({str(probe)})
    with first.recording("a"):
        collector_probe.small(1)

    second = LineCollector({str(probe)})
    with second.recording("b"):
        collector_probe.small(1)

    assert second.tests_covering(str(probe), 7) == {"b"}
