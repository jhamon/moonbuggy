"""The human report: pure rendering, no terminal involved.

Every function here is string-in string-out with width and palette passed in,
which is what lets the alignment, truncation, and encoding cases be ordinary
unit tests.
"""

from moonbuggy.humanreport import (
    changed_span,
    coverage_sentence,
    render_group,
    render_report,
    ruler,
    score_text,
)
from moonbuggy.terminal import Palette

PLAIN = Palette()


def rec(**over):
    """A Record with sane defaults, overridden per test."""
    base = {
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
        "diff": "",
    }
    base.update(over)
    return base


def test_span_snaps_outward_to_the_whole_token():
    # Raw common-prefix/suffix finds only the "=", because the ">" is common to
    # both. A caret under the "=" alone understates the change.
    original = "return stock > 0 and not discontinued"
    mutated = "return stock >= 0 and not discontinued"
    start, end = changed_span(original, mutated)
    assert mutated[start:end] == ">="


def test_span_covers_a_whole_augmented_assignment():
    start, end = changed_span("n -= 1", "n += 1")
    assert "n += 1"[start:end] == "+="


def test_span_finds_a_changed_literal():
    start, end = changed_span("return 0", "return 1")
    assert "return 1"[start:end] == "1"


def test_span_survives_an_overlapping_prefix_and_suffix():
    # "x = 11" against "x = 1": prefix 5 and suffix 1 sum past the shorter
    # length. Without clamping this is a negative-length span.
    start, end = changed_span("x = 11", "x = 1")
    assert 0 <= start <= end <= len("x = 1")


def test_span_is_never_empty():
    start, end = changed_span("a", "a")
    assert end >= start


def test_ruler_sits_under_the_span():
    mutated = "return stock >= 0 and not discontinued"
    start, end = changed_span("return stock > 0 and not discontinued", mutated)
    line = f"    + {mutated}"
    assert ruler(mutated, start, end, 6) == " " * line.index(">=") + "^^"


def test_ruler_counts_cells_not_characters():
    # Two double-width characters before the span push the carets four cells.
    mutated = "日本 = 1"
    start, end = changed_span("日本 = 0", mutated)
    assert ruler(mutated, start, end, 0) == " " * 7 + "^"


def test_group_prints_the_original_line_once():
    lines = render_group(
        [
            rec(),
            rec(
                operator="constant_int",
                mutated="return stock > 1 and not discontinued",
            ),
        ],
        PLAIN,
        timeout=30.0,
    )
    assert sum(1 for line in lines if line.lstrip().startswith("- ")) == 1
    assert sum(1 for line in lines if line.lstrip().startswith("+ ")) == 2


def test_group_header_is_a_clickable_path_and_line():
    # Contiguous path:line at column 0 is what terminals and $EDITOR +N act on.
    assert render_group([rec()], PLAIN, timeout=30.0)[0] == "sample/inventory.py:9"


def test_status_is_a_word_not_a_symbol():
    # The five keywords are the vocabulary of results.txt and of every grep a
    # user writes; a parallel set of glyphs would not transfer.
    assert "  SURVIVED  comparison_swap" in render_group([rec()], PLAIN, timeout=30.0)


def test_timeout_says_how_long_it_waited():
    lines = render_group(
        [rec(status="TIMEOUT", nearest_test=None)], PLAIN, timeout=30.0
    )
    assert "  TIMEOUT  comparison_swap  (timed out after 30s)" in lines


def test_timeout_reports_the_configured_budget():
    # A Record carries no timeout of its own, so the note must come from the
    # caller's --timeout rather than a hardcoded 30s -- otherwise a run
    # configured with a different budget would print a false number.
    lines = render_group([rec(status="TIMEOUT", nearest_test=None)], PLAIN, timeout=5.0)
    assert "  TIMEOUT  comparison_swap  (timed out after 5s)" in lines


def test_coverage_sentence_pluralises():
    assert coverage_sentence(rec(tests_run=1))[0].startswith("1 test runs")
    assert coverage_sentence(rec(tests_run=2))[0].startswith("2 tests run")


def test_an_unexercised_line_says_so_rather_than_naming_a_test():
    # tests_run=0 is a different finding: the action is write a test or delete
    # the code, and there is no nearest test to read.
    assert coverage_sentence(rec(tests_run=0, nearest_test=None)) == [
        "no test runs this line at all"
    ]


def test_a_module_level_mutant_explains_its_widened_selection():
    sentence = coverage_sentence(rec(module_level=True, tests_run=14))
    assert sentence == ["runs at import time; every test in the suite ran"]


def test_the_node_id_gets_its_own_line_and_is_never_truncated():
    long_id = "tests/t.py::TestClass::test_thing[a-very-long-parametrised-id]"
    lines = coverage_sentence(rec(nearest_test=long_id))
    assert long_id in lines


def test_only_survivors_get_a_coverage_sentence():
    assert coverage_sentence(rec(status="TIMEOUT", nearest_test=None)) == []


def test_a_whitespace_only_mutation_says_so():
    lines = render_group([rec(original="x = 1", mutated="x = 1 ")], PLAIN, timeout=30.0)
    assert "    (differs only in trailing whitespace)" in lines


def test_score_shows_its_denominator():
    counts = {"KILLED": 15, "SURVIVED": 5, "TIMEOUT": 1, "SKIPPED": 1, "SUSPICIOUS": 0}
    assert score_text(counts) == "15/21 killed, 71%"


def test_score_is_not_a_number_when_everything_was_skipped():
    counts = {"KILLED": 0, "SURVIVED": 0, "TIMEOUT": 0, "SKIPPED": 3, "SUSPICIOUS": 0}
    assert score_text(counts) == "n/a"


def test_the_last_line_states_the_exit_code():
    # The reader's terminal comes to rest on the final line, and anyone wiring
    # this into a pre-commit hook needs the list connected to the red.
    report = render_report([rec()], palette=PLAIN, files=1, elapsed=9.4, timeout=30.0)
    assert report.splitlines()[-1] == "exit 1 -- survivors"


def test_a_clean_run_says_so():
    report = render_report(
        [rec(status="KILLED")], palette=PLAIN, files=1, elapsed=1.0, timeout=30.0
    )
    assert report.splitlines()[-1] == "exit 0 -- nothing survived"


def test_killed_mutants_appear_only_as_counts():
    report = render_report(
        [rec(status="KILLED")], palette=PLAIN, files=1, elapsed=1.0, timeout=30.0
    )
    assert "KILLED  comparison_swap" not in report
    assert "1 killed" in report


def test_timeouts_move_below_the_survivors():
    records = [
        rec(),
        rec(status="TIMEOUT", file="sample/loops.py", line=12, nearest_test=None),
    ]
    report = render_report(records, palette=PLAIN, files=2, elapsed=1.0, timeout=30.0)
    assert report.index("Problems with the run") > report.index("SURVIVED")


def test_suspicious_collapses_to_one_line():
    # humanize in the project's own OSS data is 84 SUSPICIOUS against 16
    # SURVIVED. Rendered in full the finding drowns in the plumbing.
    records = [rec(status="SUSPICIOUS", nearest_test=None) for _ in range(84)]
    report = render_report(records, palette=PLAIN, files=1, elapsed=1.0, timeout=30.0)
    assert "84 mutants could not be answered confidently (SUSPICIOUS)." in report
    assert report.count("SUSPICIOUS  comparison_swap") == 0


def test_groups_are_ordered_by_file_then_line():
    records = [
        rec(file="b.py", line=1),
        rec(file="a.py", line=9),
        rec(file="a.py", line=2),
    ]
    report = render_report(records, palette=PLAIN, files=2, elapsed=1.0, timeout=30.0)
    assert report.index("a.py:2") < report.index("a.py:9") < report.index("b.py:1")


def test_no_line_exceeds_the_report_width():
    report = render_report([rec()], palette=PLAIN, files=1, elapsed=9.4, timeout=30.0)
    # The node id is the one deliberate exception; it is a paste target.
    body = [line for line in report.splitlines() if "::" not in line]
    assert max(len(line) for line in body) <= 100
