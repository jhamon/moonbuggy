"""Everything about the human reporter that depends on the outside world.

Measurement, environment resolution, and the live progress region live here so
that `humanreport` can be a pure function of its arguments. The split is what
makes the report testable: every alignment and truncation case is a string
comparison, with no terminal, no pty, and no environment involved.
"""

import os
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import IO

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


def visible_width(text: str) -> int:
    """How many terminal cells a string occupies once escapes are ignored.

    `display_width` treats an ESC byte like any other character and counts
    one cell for it, so a palette-wrapped line measures far wider than what
    actually reaches the screen -- the escape bytes never occupy a cell at
    all. This skips every ANSI CSI sequence (`ESC [` through a final byte in
    `@`..`~`) before measuring what is left.

    Args:
        text: the string to measure, which may contain ANSI CSI sequences.

    Returns:
        The total width in cells, excluding any CSI sequence.
    """
    width = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\x1b" and index + 1 < length and text[index + 1] == "[":
            end = index + 2
            while end < length and not ("@" <= text[end] <= "~"):
                end += 1
            index = min(end + 1, length)
            continue
        width += char_width(char)
        index += 1
    return width


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


# Colour depths, as the number of colours available. 0 means no escape
# sequences at all -- not merely no colour, but no bold and no reverse either,
# which is what TERM=dumb and a pipe both mean.
NO_COLOUR = 0
ANSI_8 = 8
ANSI_256 = 256
TRUECOLOR = 16777216

# The widest the report ever draws. Nothing here needs more, and a 300-column
# diff line requires head-turning.
MAX_WIDTH = 100
FALLBACK_WIDTH = 80

# The narrowest the report is ever allowed to wrap to. Below this, `window`'s
# `room = budget - 2 * len(ELLIPSIS)` goes negative and it returns more
# ellipsis than content, so a floor here is what keeps every downstream
# consumer of `resolve_width`'s result safe to call without its own check.
MIN_WIDTH = 20


def resolve_format(flag: str | None, env: Mapping[str, str], isatty: bool) -> str:
    """Choose the human or the agent report.

    The environment variable sits above TTY detection on purpose. TTY detection
    fails silently in the direction that matters: an agent harness that
    allocates a pty gets the human format and its `grep SURVIVED` returns
    nothing, with no error to notice. A harness pins the variable once.

    Args:
        flag: the value of `--report`, or None.
        env: the process environment.
        isatty: whether the report's stream is a terminal.

    Returns:
        Either "human" or "agent".
    """
    if flag is not None:
        return flag
    from_env = env.get("MOONBUGGY_REPORT")
    if from_env in {"human", "agent"}:
        return from_env
    if env.get("CI"):
        return "agent"
    return "human" if isatty else "agent"


def resolve_colour(flag: str | None, env: Mapping[str, str], isatty: bool) -> int:
    """Decide whether to emit escape sequences, and how many colours to use.

    Colour is a separate decision from the report format: a human redirecting
    the report to a file still wants the human format, and still wants no
    escapes in it.

    Args:
        flag: the value of `--color` -- "always", "never", "auto", or None.
        env: the process environment.
        isatty: whether the report's stream is a terminal.

    Returns:
        A colour depth: 0, 8, 256, or 16777216.
    """
    if flag == "never":
        return NO_COLOUR
    if flag == "always":
        return _depth(env)
    if flag is None or flag == "auto":
        # Presence is the signal, whatever the value (no-color.org).
        if "NO_COLOR" in env:
            return NO_COLOUR
        forced = env.get("CLICOLOR_FORCE")
        if forced is not None:
            return NO_COLOUR if forced == "0" else _depth(env)
        force_colour = env.get("FORCE_COLOR")
        if force_colour is not None:
            if force_colour == "0":
                return NO_COLOUR
            return {"2": ANSI_256, "3": TRUECOLOR}.get(force_colour, ANSI_8)
        if env.get("CLICOLOR") == "0":
            return NO_COLOUR
        if env.get("TERM", "dumb") == "dumb":
            return NO_COLOUR
        return _depth(env) if isatty else NO_COLOUR
    return NO_COLOUR


def _depth(env: Mapping[str, str]) -> int:
    if env.get("COLORTERM") in {"truecolor", "24bit"}:
        return TRUECOLOR
    if "256color" in env.get("TERM", ""):
        return ANSI_256
    return ANSI_8


def resolve_width(flag: int | None, env: Mapping[str, str], fd: int | None) -> int:
    """How many columns the report may use.

    `shutil.get_terminal_size` is deliberately not used: it silently
    substitutes 80 on failure, so a caller cannot tell a measurement from a
    guess, and the two need different behaviour -- a guessed width must not be
    used for anything that would look broken if it were wrong.

    Args:
        flag: the value of `--width`, or None.
        env: the process environment.
        fd: the file descriptor the report is going to, or None if there is not
            one to measure.

    Returns:
        A column count between MIN_WIDTH and MAX_WIDTH.
    """
    detected: int | None = None
    if flag is not None and flag > 0:
        detected = flag
    else:
        # COLUMNS is rarely exported, so its presence is a deliberate override.
        from_env = env.get("COLUMNS", "")
        if from_env.isdigit() and int(from_env) > 0:
            detected = int(from_env)
        elif fd is not None:
            try:
                detected = os.get_terminal_size(fd).columns
            except OSError:
                detected = None
    return max(MIN_WIDTH, min(detected or FALLBACK_WIDTH, MAX_WIDTH))


@dataclass(frozen=True)
class Palette:
    """The escape sequences the report may use, empty when it may not.

    Every field defaults to the empty string, so `Palette()` is the no-escapes
    palette and rendering code needs no conditionals: it always concatenates.
    """

    dim: str = ""
    bold: str = ""
    reverse: str = ""
    minus: str = ""
    plus: str = ""
    reset: str = ""


def palette_for(depth: int) -> Palette:
    """The palette for a colour depth.

    Not red and green. That pair is both the worst for the most common colour
    vision deficiency and a pair of mid-luminance colours, so it also fails a
    greyscale test. Cyan and amber are legible on light and dark backgrounds
    alike, which is why they are used at every depth.

    Args:
        depth: a value from `resolve_colour`.

    Returns:
        A Palette; every field is empty when depth is 0.
    """
    if depth == NO_COLOUR:
        return Palette()
    if depth == TRUECOLOR:
        minus, plus = "\x1b[38;2;86;180;233m", "\x1b[38;2;230;159;0m"
    elif depth == ANSI_256:
        minus, plus = "\x1b[38;5;39m", "\x1b[38;5;214m"
    else:
        minus, plus = "\x1b[36m", "\x1b[33m"
    return Palette(
        dim="\x1b[2m",
        bold="\x1b[1m",
        reverse="\x1b[7m",
        minus=minus,
        plus=plus,
        reset="\x1b[0m",
    )


# Erase the whole line and return to column 0. Padding with spaces instead
# would leave a tail behind whenever the new frame is shorter than the old.
ERASE = "\r\x1b[2K"
# Ten frames a second. Faster buys nothing a reader can see, and every frame is
# a write that a terminal recording or a CI log capture keeps forever.
MIN_INTERVAL = 0.1


class LiveRegion:
    """A single-row progress line that other output can be written around.

    One physical row, deliberately. A multi-row region has to move the cursor
    up to repaint, and the number of physical rows a logical line occupies
    changes when the terminal is resized -- at which point the cursor
    arithmetic is wrong and the corruption is unrecoverable.

    The cursor is never hidden. `run()` exits through `os._exit`, which skips
    `atexit` by design, so there is no exit path that could reliably show it
    again; a Ctrl-C would leave the user's shell with an invisible cursor until
    they ran `reset`. Parking the cursor at column 0 costs nothing.

    While the region is open it is the only thing writing to its stream. Callers
    route their own messages through `log` so that each one erases the live line
    first and redraws it afterwards.
    """

    def __init__(
        self,
        stream: IO[str],
        *,
        enabled: bool,
        clock: Callable[[], float],
    ) -> None:
        """Initialise the region.

        Args:
            stream: the stream to write frames to.
            enabled: whether to emit escapes and repaint at all.
            clock: a monotonic time source, called to rate-limit repaints.
        """
        self.stream = stream
        self.enabled = enabled
        self.clock = clock
        self._current = ""
        self._last_paint = float("-inf")
        self._closed = False

    def tick(self, text: str) -> None:
        """Repaint the live line, if anything has changed and it is time to.

        Args:
            text: the whole line to show, already fitted to the width.
        """
        if not self.enabled or text == self._current:
            return
        now = self.clock()
        if now - self._last_paint < MIN_INTERVAL:
            return
        self._last_paint = now
        self._current = text
        self.stream.write(ERASE + text)
        self.stream.flush()

    def log(self, text: str) -> None:
        """Commit a line of scrolling output above the live line.

        Args:
            text: the message, without a trailing newline.
        """
        if self.enabled:
            self.stream.write(ERASE)
        self.stream.write(text + "\n")
        if self.enabled and self._current:
            self.stream.write(self._current)
        self.stream.flush()

    def close(self, final: str | None = None) -> None:
        """Stop redrawing and leave one durable line in the scrollback.

        Idempotent: a region already closed by an earlier call emits nothing
        and does not raise, however many more times it is closed or whatever
        `final` it is given. This matters because teardown tends to happen
        twice -- once in a `finally` and once on an interrupt path -- and only
        the first call may commit a line.

        Args:
            final: the line to leave behind, or None to leave nothing.
        """
        if self._closed:
            return
        if self.enabled:
            self.stream.write(ERASE)
        self._current = ""
        self.enabled = False
        self._closed = True
        if final is not None:
            self.stream.write(final + "\n")
        self.stream.flush()
