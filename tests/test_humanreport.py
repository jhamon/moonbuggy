"""The human report: pure rendering, no terminal involved.

Every function here is string-in string-out with width and palette passed in,
which is what lets the alignment, truncation, and encoding cases be ordinary
unit tests.
"""

from moonbuggy.humanreport import changed_span, ruler


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
