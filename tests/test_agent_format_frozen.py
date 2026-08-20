"""The agent format is a contract, pinned byte for byte.

Section 5.1's premise is that the reader is an agent grepping output. Every
line begins with a status keyword and carries key=value tokens, so
`grep SURVIVED` works with no knowledge of the schema. The human reporter must
not have moved any of it. This test exists because "we were careful" is not a
mechanism.

The vocabulary itself is allowed to grow -- `NO_COVERAGE` was added to it, and
that is a documented breaking change for anyone whose `grep SURVIVED` used to
catch uncovered lines. What is frozen is the *shape*: the leading bare keyword,
the token order, and the bytes of a line whose status has not changed.
"""

from moonbuggy.report import plaintext_from_records, render_line

GOLDEN = (
    "SURVIVED  sample/inventory.py:9 comparison_swap line=9 "
    "nearest_test=tests/test_inventory.py::test_discontinued tests_run=2 "
    "id=sample/inventory.py:9:comparison_swap:0"
)

RECORD = {
    "id": "sample/inventory.py:9:comparison_swap:0",
    "status": "SURVIVED",
    "file": "sample/inventory.py",
    "line": 9,
    "operator": "comparison_swap",
    "category": "comparison_swap",
    "nearest_test": "tests/test_inventory.py::test_discontinued",
    "tests_run": 2,
    "duration": 0.1,
    "module_level": False,
    "suppressed": False,
    "original": "return stock > 0 and not discontinued",
    "mutated": "return stock >= 0 and not discontinued",
    "diff": "- return stock > 0\n+ return stock >= 0",
}


def test_the_agent_line_is_unchanged():
    assert render_line(RECORD) == GOLDEN


def test_the_line_still_starts_with_a_bare_grep_keyword():
    assert render_line(RECORD).split()[0] == "SURVIVED"


def test_the_plaintext_view_is_one_line_per_record():
    text = plaintext_from_records([RECORD, RECORD])
    assert len(text.splitlines()) == 2
    assert "\n" not in render_line(RECORD)


def test_a_no_coverage_line_keeps_the_frozen_shape():
    # The keyword is longer than the status column, so it overflows it rather
    # than widening the column -- widening would shift every other status's
    # line and break the golden above. Single-space separation still leaves the
    # same tokens in the same order for a whitespace-splitting parser.
    line = render_line({**RECORD, "status": "NO_COVERAGE", "nearest_test": None})

    assert line.split()[0] == "NO_COVERAGE"
    assert line.split()[1] == "sample/inventory.py:9"
    assert "nearest_test=-" in line


def test_the_new_operand_fields_do_not_leak_into_the_line():
    # They exist for the human reporter. The agent line stays as it was.
    assert "original=" not in render_line(RECORD)
    assert "return stock" not in render_line(RECORD)
