"""Everything about the human reporter that depends on the outside world.

Measurement, environment resolution, and the live progress region live here so
that `humanreport` can be a pure function of its arguments. The split is what
makes the report testable: every alignment and truncation case is a string
comparison, with no terminal, no pty, and no environment involved.
"""

import unicodedata

# Tab stops. Eight is what terminals do, and matching them is the whole point:
# a tab expanded at a different interval renders at a different column than the
# source file it came from.
TAB_WIDTH = 8


def char_width(char: str, ambiguous_wide: bool = False) -> int:
    """How many terminal cells one character occupies.

    Args:
        char: a single character.
        ambiguous_wide: whether to treat East Asian Ambiguous characters as two
            cells, which is what a terminal configured for a CJK locale does.

    Returns:
        0, 1, or 2.
    """
    if unicodedata.combining(char) or unicodedata.category(char) in {
        "Mn",
        "Me",
        "Cf",
    }:
        return 0
    kind = unicodedata.east_asian_width(char)
    if kind in {"W", "F"}:
        return 2
    if kind == "A":
        return 2 if ambiguous_wide else 1
    return 1


def display_width(text: str, ambiguous_wide: bool = False) -> int:
    """How many terminal cells a string occupies.

    Undercounts emoji ZWJ sequences, which are several wide code points forming
    one grapheme. That is documented rather than solved, and it is one more
    reason the report never right-aligns anything after source text.

    Args:
        text: the string to measure.
        ambiguous_wide: whether East Asian Ambiguous characters take two cells.

    Returns:
        The total width in cells.
    """
    return sum(char_width(char, ambiguous_wide) for char in text)


def sanitise(text: str) -> str:
    """Make one line of arbitrary source safe to print and to measure.

    Tabs are expanded here rather than left to the terminal, which would expand
    them from its own current column and so render an indent that does not match
    the file. Control characters are escaped because a source file may hold an
    ESC in a string literal, and printing it verbatim would replay whatever it
    encodes -- a screen clear, a title change -- into the reader's terminal.

    Args:
        text: one raw source line.

    Returns:
        The line with tabs expanded and every control character escaped.
    """
    out: list[str] = []
    for char in text.expandtabs(TAB_WIDTH):
        if unicodedata.category(char) == "Cc" or "\ud800" <= char <= "\udfff":
            point = ord(char)
            out.append(f"\\x{point:02x}" if point < 256 else f"\\u{point:04x}")
        else:
            out.append(char)
    return "".join(out)
