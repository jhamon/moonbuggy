"""Terminal measurement, environment resolution, and the live progress region.

These are the parts of the human reporter that depend on the outside world.
Keeping them here, behind pure functions, is what lets humanreport.py be
tested as string-in string-out with no pty.
"""

from moonbuggy.terminal import char_width, display_width, sanitise


def test_ascii_is_one_cell_per_character():
    assert display_width("return 0") == 8


def test_east_asian_wide_is_two_cells():
    assert display_width("日本") == 4


def test_combining_marks_occupy_no_cell():
    # Must stay decomposed -- "e" + U+0301 COMBINING ACUTE ACCENT -- not the
    # precomposed "é" (U+00E9), which is a single Ll code point and would not
    # exercise the combining-mark branch at all.
    assert display_width("é") == 1


def test_combining_mark_alone_is_zero_width():
    # Direct pin of char_width's zero-width branch, independent of any base
    # character it might be combined with.
    assert char_width("́") == 0


def test_ambiguous_width_follows_the_locale():
    arrow = "→"  # East Asian Ambiguous
    assert display_width(arrow) == 1
    assert display_width(arrow, ambiguous_wide=True) == 2


def test_tabs_expand_to_eight_column_stops():
    # A raw tab would expand from the terminal's column, not the file's, so
    # the rendered indent would not match the source's.
    assert sanitise("a\tb") == "a       b"


def test_escape_sequences_in_source_are_defanged():
    # A source file holding an ESC would otherwise replay it into the reader's
    # terminal when the report prints the line.
    assert sanitise("x = '\x1b[2J'") == "x = '\\x1b[2J'"


def test_form_feed_is_defanged():
    # Legal Python whitespace, and it appears in real files.
    assert sanitise("a\x0cb") == "a\\x0cb"


def test_lone_surrogates_are_defanged():
    assert sanitise("a\ud800b") == "a\\ud800b"
