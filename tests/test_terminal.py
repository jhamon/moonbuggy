"""Terminal measurement, environment resolution, and the live progress region.

These are the parts of the human reporter that depend on the outside world.
Keeping them here, behind pure functions, is what lets humanreport.py be
tested as string-in string-out with no pty.
"""

from moonbuggy.terminal import (
    char_width,
    display_width,
    palette_for,
    resolve_colour,
    resolve_format,
    resolve_width,
    sanitise,
)


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


def test_flag_beats_everything_for_format():
    assert resolve_format("agent", {"MOONBUGGY_REPORT": "human"}, True) == "agent"


def test_env_var_beats_tty_detection():
    # The failure this exists for: an agent harness that allocates a pty would
    # otherwise get the human format and grep nothing, with no error.
    assert resolve_format(None, {"MOONBUGGY_REPORT": "agent"}, True) == "agent"


def test_ci_means_agent():
    assert resolve_format(None, {"CI": "true"}, True) == "agent"


def test_tty_selects_human_and_a_pipe_selects_agent():
    assert resolve_format(None, {}, True) == "human"
    assert resolve_format(None, {}, False) == "agent"


def test_no_color_wins_whatever_its_value():
    # no-color.org: presence is the signal, not the value.
    assert resolve_colour(None, {"NO_COLOR": ""}, True) == 0
    assert resolve_colour(None, {"NO_COLOR": "0"}, True) == 0


def test_color_flag_beats_no_color():
    assert resolve_colour("always", {"NO_COLOR": "1"}, False) > 0


def test_clicolor_force_turns_colour_on_off_a_tty():
    assert resolve_colour(None, {"CLICOLOR_FORCE": "1"}, False) > 0
    assert resolve_colour(None, {"CLICOLOR_FORCE": "0"}, False) == 0


def test_force_color_selects_depth():
    assert resolve_colour(None, {"FORCE_COLOR": "1"}, False) == 8
    assert resolve_colour(None, {"FORCE_COLOR": "3"}, False) == 16777216
    assert resolve_colour(None, {"FORCE_COLOR": "0"}, True) == 0


def test_dumb_or_unset_term_gets_no_escapes():
    # TERM unset is not hypothetical; it is unset in ordinary subprocess envs.
    assert resolve_colour(None, {"TERM": "dumb"}, True) == 0
    assert resolve_colour(None, {}, True) == 0


def test_depth_comes_from_term_and_colorterm():
    assert resolve_colour(None, {"TERM": "xterm"}, True) == 8
    assert resolve_colour(None, {"TERM": "xterm-256color"}, True) == 256
    truecolor = {"TERM": "xterm-256color", "COLORTERM": "truecolor"}
    assert resolve_colour(None, truecolor, True) == 16777216


def test_width_prefers_the_flag_then_columns():
    assert resolve_width(50, {"COLUMNS": "120"}, None) == 50
    assert resolve_width(None, {"COLUMNS": "70"}, None) == 70


def test_width_falls_back_to_eighty_when_undetectable():
    # shutil.get_terminal_size silently substitutes 80, leaving no way to tell
    # a measurement from a guess. This reaches the same number honestly.
    assert resolve_width(None, {}, None) == 80


def test_width_is_capped_at_a_hundred():
    # A 300-column diff line requires head-turning.
    assert resolve_width(None, {"COLUMNS": "300"}, None) == 100


def test_nonsense_columns_is_ignored():
    assert resolve_width(None, {"COLUMNS": "wide"}, None) == 80
    assert resolve_width(None, {"COLUMNS": "-5"}, None) == 80


def test_palette_is_empty_without_colour():
    plain = palette_for(0)
    assert plain.dim == "" and plain.bold == "" and plain.reset == ""


def test_palette_avoids_red_and_green():
    # Red/green is the worst pair for the most common colour vision deficiency
    # and a pair of mid-luminance colours that also fails in greyscale.
    eight = palette_for(8)
    assert "31m" not in eight.minus and "32m" not in eight.plus
