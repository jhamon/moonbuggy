"""The human report: pure rendering, no terminal involved.

Every function here is string-in string-out with width and palette passed in,
which is what lets the alignment, truncation, and encoding cases be ordinary
unit tests.
"""

from moonbuggy.diffscope import DiffScope
from moonbuggy.humanreport import (
    changed_span,
    coverage_sentence,
    render_footer,
    render_group,
    render_report,
    ruler,
    score_text,
    window,
)
from moonbuggy.terminal import Palette, display_width, palette_for, visible_width

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
    # Two identical lines still have to produce a caret somewhere rather than a
    # zero-length span, so the ruler is never a blank line.
    assert changed_span("a", "a") == (0, 1)


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
    # The status keywords are the vocabulary of results.txt and of every grep a
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


def test_a_no_coverage_record_still_says_why_nothing_caught_it():
    # The sentence follows the finding, not the keyword: NO_COVERAGE is where
    # "no test runs this line at all" ended up when it stopped being SURVIVED.
    assert coverage_sentence(
        rec(status="NO_COVERAGE", tests_run=0, nearest_test=None)
    ) == ["no test runs this line at all"]


def test_uncovered_lines_get_their_own_section():
    records = [
        rec(),
        rec(status="NO_COVERAGE", file="sample/restock.py", line=15, tests_run=0),
        rec(status="NO_COVERAGE", file="sample/restock.py", line=21, tests_run=0),
    ]
    report = render_report(
        records,
        palette=PLAIN,
        files=2,
        elapsed=1.0,
        timeout=30.0,
        artifact=".moonbuggy/results.jsonl",
    )

    # Counted in lines, not mutants: two mutants on one line are one gap.
    assert "2 lines no test reaches" in report
    assert report.index("2 lines no test reaches") > report.index("SURVIVED")
    assert "sample/restock.py:15" in report


def test_one_uncovered_line_is_singular():
    report = render_report(
        [rec(status="NO_COVERAGE", tests_run=0, nearest_test=None)],
        palette=PLAIN,
        files=1,
        elapsed=1.0,
        timeout=30.0,
        artifact=".moonbuggy/results.jsonl",
    )
    assert "1 line no test reaches" in report


def test_uncovered_lines_are_a_finding_and_keep_the_exit_code():
    # The whole point of the rename is that it must not loosen a CI gate: a run
    # with no survivors but an unreached line still exits 1.
    report = render_report(
        [rec(status="NO_COVERAGE", tests_run=0, nearest_test=None)],
        palette=PLAIN,
        files=1,
        elapsed=1.0,
        timeout=30.0,
        artifact=".moonbuggy/results.jsonl",
    )
    assert report.splitlines()[-1] == "exit 1 -- lines no test reaches"


def test_the_footer_counts_uncovered_lines_separately():
    counts = {
        "KILLED": 3,
        "SURVIVED": 1,
        "NO_COVERAGE": 2,
        "TIMEOUT": 0,
        "SKIPPED": 0,
        "SUSPICIOUS": 0,
    }
    tally = render_footer(counts, 1.0, ".moonbuggy/results.jsonl").splitlines()[0]

    assert "1 survived" in tally
    assert "2 no_coverage" in tally
    assert tally.index("survived") < tally.index("no_coverage") < tally.index("killed")


def test_the_footer_names_both_findings_when_there_are_both():
    counts = {
        "KILLED": 0,
        "SURVIVED": 1,
        "NO_COVERAGE": 1,
        "TIMEOUT": 0,
        "SKIPPED": 0,
        "SUSPICIOUS": 0,
    }
    footer = render_footer(counts, 1.0, ".moonbuggy/results.jsonl")

    assert footer.splitlines()[-1] == "exit 1 -- survivors, and lines no test reaches"


def test_a_whitespace_only_mutation_says_so():
    lines = render_group([rec(original="x = 1", mutated="x = 1 ")], PLAIN, timeout=30.0)
    assert "    (differs only in trailing whitespace)" in lines


def test_score_shows_its_denominator():
    counts = {"KILLED": 15, "SURVIVED": 5, "TIMEOUT": 1, "SKIPPED": 1, "SUSPICIOUS": 0}
    assert score_text(counts) == "15/21 killed, 71%"


def test_score_is_not_a_number_when_everything_was_skipped():
    counts = {"KILLED": 0, "SURVIVED": 0, "TIMEOUT": 0, "SKIPPED": 3, "SUSPICIOUS": 0}
    assert score_text(counts) == "n/a"


def test_render_footer_names_whatever_artifact_it_is_given():
    # `render_footer` has no filesystem knowledge of its own, so it must print
    # exactly the string the caller hands it -- not the pre-branch hardcoded
    # `.moonbuggy/results.jsonl`, which is wrong the moment `--output-dir` or
    # `--project` moves the artifact elsewhere.
    counts = {"KILLED": 1, "SURVIVED": 0, "TIMEOUT": 0, "SKIPPED": 0, "SUSPICIOUS": 0}
    footer = render_footer(counts, 1.0, "custom-out/results.jsonl")
    lines = footer.splitlines()
    assert lines[1] == "Full records: custom-out/results.jsonl"


def test_render_report_footer_carries_the_given_artifact():
    # `render_report` must forward its `artifact` argument to `render_footer`
    # rather than letting the footer fall back to a hardcoded path.
    report = render_report(
        [rec(status="KILLED")],
        palette=PLAIN,
        files=1,
        elapsed=1.0,
        timeout=30.0,
        artifact="custom-out/results.jsonl",
    )
    assert "Full records: custom-out/results.jsonl" in report.splitlines()


def test_the_last_line_states_the_exit_code():
    # The reader's terminal comes to rest on the final line, and anyone wiring
    # this into a pre-commit hook needs the list connected to the red.
    report = render_report(
        [rec()],
        palette=PLAIN,
        files=1,
        elapsed=9.4,
        timeout=30.0,
        artifact=".moonbuggy/results.jsonl",
    )
    assert report.splitlines()[-1] == "exit 1 -- survivors"


def test_a_clean_run_says_so():
    report = render_report(
        [rec(status="KILLED")],
        palette=PLAIN,
        files=1,
        elapsed=1.0,
        timeout=30.0,
        artifact=".moonbuggy/results.jsonl",
    )
    assert report.splitlines()[-1] == "exit 0 -- nothing survived"


def test_killed_mutants_appear_only_as_counts():
    report = render_report(
        [rec(status="KILLED")],
        palette=PLAIN,
        files=1,
        elapsed=1.0,
        timeout=30.0,
        artifact=".moonbuggy/results.jsonl",
    )
    assert "KILLED  comparison_swap" not in report
    assert "1 killed" in report


def test_timeouts_move_below_the_survivors():
    records = [
        rec(),
        rec(status="TIMEOUT", file="sample/loops.py", line=12, nearest_test=None),
    ]
    report = render_report(
        records,
        palette=PLAIN,
        files=2,
        elapsed=1.0,
        timeout=30.0,
        artifact=".moonbuggy/results.jsonl",
    )
    assert report.index("Problems with the run") > report.index("SURVIVED")


def test_suspicious_collapses_to_one_line():
    # humanize in the project's own OSS data is 84 SUSPICIOUS against 16
    # SURVIVED. Rendered in full the finding drowns in the plumbing.
    records = [rec(status="SUSPICIOUS", nearest_test=None) for _ in range(84)]
    report = render_report(
        records,
        palette=PLAIN,
        files=1,
        elapsed=1.0,
        timeout=30.0,
        artifact=".moonbuggy/results.jsonl",
    )
    assert "84 mutants could not be answered confidently (SUSPICIOUS)." in report
    assert report.count("SUSPICIOUS  comparison_swap") == 0


def test_groups_are_ordered_by_file_then_line():
    records = [
        rec(file="b.py", line=1),
        rec(file="a.py", line=9),
        rec(file="a.py", line=2),
    ]
    report = render_report(
        records,
        palette=PLAIN,
        files=2,
        elapsed=1.0,
        timeout=30.0,
        artifact=".moonbuggy/results.jsonl",
    )
    assert report.index("a.py:2") < report.index("a.py:9") < report.index("b.py:1")


def test_a_long_source_line_is_fitted_to_the_report_width():
    # A source line far past the budget is the real regression guard: before
    # `width` existed there was no windowing logic at all to pin down. Only the
    # diff lines are fitted -- a deep path:line header and the footer's score
    # are worth more whole than short, so they soft wrap.
    long_line = "x = " + "a" * 300
    report = render_report(
        [rec(original=long_line, mutated=long_line + "1")],
        palette=PLAIN,
        files=1,
        elapsed=9.4,
        timeout=30.0,
        width=72,
        artifact=".moonbuggy/results.jsonl",
    )
    lines = report.splitlines()
    diff_lines = [line for line in lines if line.lstrip()[:2] in {"- ", "+ "}]
    assert len(diff_lines) == 2
    assert max(len(line) for line in diff_lines) <= 72


def test_a_deep_path_keeps_its_line_number_at_a_narrow_width():
    # `path:line` is the token a terminal makes clickable and `$EDITOR +N`
    # consumes. Truncating it costs the reader the only thing the header is for.
    deep = "src/company/services/billing/adapters/legacy_gateway.py"
    report = render_report(
        [rec(file=deep, line=417)],
        palette=PLAIN,
        files=1,
        elapsed=1.0,
        timeout=30.0,
        width=40,
        artifact=".moonbuggy/results.jsonl",
    )
    assert f"{deep}:417" in report.splitlines()


def test_a_short_line_is_returned_unchanged():
    assert window("return 0", 7, 8, 74) == ("return 0", 7, 8)


def test_a_long_line_is_windowed_around_the_change():
    # Never tail-truncated: the change may be at column 300.
    text = "x = " + "a" * 300 + " + CHANGED"
    start = text.index("CHANGED")
    got, new_start, new_end = window(text, start, start + 7, 40)
    assert "CHANGED" in got
    assert got.startswith("...")
    assert got[new_start:new_end] == "CHANGED"


def test_the_window_never_exceeds_its_budget():
    text = "y = " + "b" * 500
    got, _, _ = window(text, 100, 104, 40)
    assert len(got) <= 40


def test_a_narrow_terminal_keeps_the_location_and_the_score_whole():
    # Width fits the diff windows, and nothing else. A clipped header loses the
    # line number that makes `path:line` actionable, and a clipped footer loses
    # the denominator that makes the score mean anything -- so both soft wrap.
    report = render_report(
        [rec()],
        palette=PLAIN,
        files=1,
        elapsed=1.0,
        timeout=30.0,
        width=40,
        artifact=".moonbuggy/results.jsonl",
    )
    lines = report.splitlines()
    assert "sample/inventory.py:9" in lines
    assert "1 survived, 0 killed in 1.0s -- 0/1 killed, 0%" in lines
    diff_lines = [line for line in lines if line.lstrip()[:2] in {"- ", "+ "}]
    assert diff_lines
    assert max(len(line) for line in diff_lines) <= 40


def test_window_never_exceeds_its_budget_with_wide_characters():
    # Character-count slicing halves a CJK run's real width, so the old
    # implementation returned text nearly 1.5x its budget here.
    text = "x = " + "中" * 100 + " + CHANGED"
    start = text.index("CHANGED")
    got, new_start, new_end = window(text, start, start + 7, 40)
    assert display_width(got) <= 40
    assert got[new_start:new_end] == "CHANGED"


def test_a_narrow_coloured_terminal_never_leaks_the_reset():
    # Pins the real invariant: width logic must never measure or slice a
    # string that already contains escape sequences. _clip used to re-cut a
    # palette-wrapped code line by raw character count -- which counts each
    # escape byte as a cell -- severing the trailing reset and leaking colour
    # into every line printed after it.
    report = render_report(
        [rec()],
        palette=palette_for(8),
        files=1,
        elapsed=1.0,
        timeout=30.0,
        width=40,
        artifact=".moonbuggy/results.jsonl",
    )
    coloured = [line for line in report.splitlines() if "\x1b" in line]
    assert coloured
    for line in coloured:
        assert visible_width(line) <= 40
        assert line.endswith("\x1b[0m")


def test_the_minus_line_windows_around_the_same_span_as_the_plus_line():
    # The "-" line carries no ruler of its own, so it is windowed around the
    # span computed with changed_span's arguments reversed -- the location in
    # `original` that differs from `mutated` -- to keep both lines showing
    # the same visual neighbourhood even when narrow. Padded with real word
    # boundaries so changed_span's widen-to-token step has somewhere to stop;
    # an unbroken run would widen to the whole line.
    pad = "pad " * 40
    text = pad + "OLD" + " " + pad
    replacement = pad + "NEW" + " " + pad
    lines = render_group(
        [rec(original=text, mutated=replacement)], PLAIN, timeout=30.0, width=40
    )
    minus_line = next(line for line in lines if line.lstrip().startswith("- "))
    assert "OLD" in minus_line


SCOPE = DiffScope(
    ref="origin/main",
    merge_base="1a2b3c4d5e6f7a8b9c0d",
    ranges={"lib.py": ((10, 12),)},
)


def test_a_full_run_footer_is_unchanged_by_the_scope_argument():
    # The scope line exists only when there is a scope. A full run's footer
    # keeps its three lines and its line numbering, which is what the tests
    # above index into.
    counts = {"KILLED": 1, "SURVIVED": 0, "TIMEOUT": 0, "SKIPPED": 0, "SUSPICIOUS": 0}

    assert render_footer(counts, 1.0, "out.jsonl") == render_footer(
        counts, 1.0, "out.jsonl", scope=None
    )


def test_a_diff_scoped_footer_says_so_and_names_the_ref():
    # The point of the line: "1/1 killed, 100%" on the line above it is a claim
    # about three changed lines, not about the codebase.
    counts = {"KILLED": 1, "SURVIVED": 0, "TIMEOUT": 0, "SKIPPED": 0, "SUSPICIOUS": 0}

    lines = render_footer(counts, 1.0, "out.jsonl", scope=SCOPE).splitlines()

    assert len(lines) == 4
    assert lines[0].endswith("1/1 killed, 100%")
    assert "origin/main" in lines[1]
    assert "1a2b3c4" in lines[1]
    assert lines[2] == "Full records: out.jsonl"


def test_a_diff_scoped_report_says_so_in_its_header_too():
    # A reader who stops after the first line has still been told the run was
    # partial.
    report = render_report(
        [rec(status="KILLED")],
        palette=PLAIN,
        files=1,
        elapsed=1.0,
        timeout=30.0,
        artifact="out.jsonl",
        scope=SCOPE,
    )
    lines = report.splitlines()

    assert (
        lines[0] == "moonbuggy  1 mutant across 1 file  (diff-scoped since origin/main)"
    )
    assert any(
        "Diff-scoped: only lines changed since origin/main" in ln for ln in lines
    )
