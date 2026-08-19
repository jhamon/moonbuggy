"""The human report: one grep-proof punch list of what survived, with diffs.

The agent format is one line per mutant and deliberately omits the diff, which
makes a survivor line say where but never what. This module says what.

Everything here is pure. Width and palette arrive as arguments, no function
reads the environment or writes to a stream, and the input is the same `Record`
that was written to `results.jsonl` -- so the human view cannot drift from the
canonical one, for the same reason the plaintext view cannot.
"""

from collections.abc import Mapping, Sequence

from .report import Record, summarise
from .terminal import (
    FALLBACK_WIDTH,
    Palette,
    char_width,
    display_width,
    sanitise,
    visible_width,
)


def changed_span(original: str, mutated: str) -> tuple[int, int]:
    """Which part of `mutated` differs from `original`.

    Found by common prefix and suffix, then widened to whole tokens. The
    widening matters: comparing `stock > 0` with `stock >= 0` finds only the
    `=`, because the `>` is common to both, and a caret under a lone `=`
    understates what changed. Snapping outward to the surrounding run of
    non-space characters recovers `>=`.

    Args:
        original: the source line before mutation.
        mutated: the source line after mutation.

    Returns:
        Half-open ``(start, end)`` character indices into `mutated`.
    """
    shorter = min(len(original), len(mutated))
    prefix = 0
    while prefix < shorter and original[prefix] == mutated[prefix]:
        prefix += 1
    # Clamped so the two spans cannot overlap. "x = 11" against "x = 1" has a
    # prefix of 5 and a suffix of 1 against a shorter length of 5, which
    # unclamped yields a negative-length span.
    suffix = 0
    while (
        suffix < shorter - prefix
        and original[len(original) - 1 - suffix] == mutated[len(mutated) - 1 - suffix]
    ):
        suffix += 1

    start, end = prefix, len(mutated) - suffix
    while start > 0 and not mutated[start - 1].isspace():
        start -= 1
    while end < len(mutated) and not mutated[end].isspace():
        end += 1
    # A boundary may have landed between a base character and its combining
    # mark, which would render the highlight starting on an orphaned diacritic.
    while end < len(mutated) and char_width(mutated[end]) == 0:
        end += 1
    return start, max(start, end)


def ruler(mutated: str, start: int, end: int, indent: int) -> str:
    """A line of carets sitting under the changed span.

    This is the only mechanism in the report that shows the changed span with
    no escape sequences at all, which is what makes the delta survive NO_COLOR,
    a pipe, `less` without `-R`, and a reader with a colour vision deficiency.

    Args:
        mutated: the line the carets go under.
        start: the span's first character index.
        end: the span's end index, exclusive.
        indent: how many cells the line is indented by, including its sigil.

    Returns:
        A string of spaces then carets, with no trailing whitespace.
    """
    lead = indent + display_width(mutated[:start])
    span = max(1, display_width(mutated[start:end]))
    return " " * lead + "^" * span


ELLIPSIS = "..."


def _walk(text: str, index: int, cells: int, direction: int) -> int:
    """Move a character index by at most `cells` display cells.

    Character-count slicing is the wrong measure here on purpose: a run of
    wide CJK characters is half as long in characters as it is in cells, so
    walking by character count -- as an earlier version of this function did
    -- silently lets a windowed line's real width overrun its budget.

    Args:
        text: the string being walked.
        index: the character index to start from.
        cells: the budget of display cells to spend moving.
        direction: -1 to move left, 1 to move right.

    Returns:
        The furthest character index reachable from `index` without spending
        more than `cells` display cells, and without leaving `text`.
    """
    spent = 0
    if direction < 0:
        while index > 0:
            width = char_width(text[index - 1])
            if spent + width > cells:
                break
            spent += width
            index -= 1
    else:
        while index < len(text):
            width = char_width(text[index])
            if spent + width > cells:
                break
            spent += width
            index += 1
    return index


def window(text: str, start: int, end: int, budget: int) -> tuple[str, int, int]:
    """Cut a long line down to a budget, keeping the changed span visible.

    Tail truncation is the specific wrong answer here, because the change may
    be at column 300. Wrapping is worse: a continuation line carries no `-` or
    `+` sigil, so it stops being clear which side of the diff it belongs to.

    The cut points are found by walking display cells, not characters: a run
    of wide CJK characters is worth two cells apiece, so slicing by character
    count alone would let the returned text overrun the budget by as much as
    the widest characters in it.

    Args:
        text: the line to fit.
        start: the changed span's first character index.
        end: the changed span's end index, exclusive.
        budget: how many cells are available.

    Returns:
        ``(windowed_text, start, end)`` with the indices rebased onto the
        returned text.
    """
    if display_width(text) <= budget:
        return text, start, end
    room = budget - 2 * len(ELLIPSIS)
    span_width = max(1, display_width(text[start:end]))
    lead = max(0, (room - span_width) // 2)
    left = _walk(text, start, lead, -1)
    right = _walk(text, left, room, 1)
    left = _walk(text, right, room, -1)
    cut = text[left:right]
    prefix = ELLIPSIS if left > 0 else ""
    suffix = ELLIPSIS if right < len(text) else ""
    shift = len(prefix) - left
    return (
        prefix + cut + suffix,
        start + shift,
        min(end + shift, len(cut) + len(prefix)),
    )


def _clip(line: str, width: int) -> str:
    """Fit a line with no highlighted span into a width, node ids exempt.

    A node id is a paste target and so is never truncated, however long. It
    is recognised by its `::`, the same marker pytest itself uses.

    Escaped lines are left alone entirely, on top of that: `window` slices by
    raw character index, which -- unlike `visible_width` -- does not know an
    ANSI escape sequence from visible text, so cutting one anywhere could
    sever the trailing reset and leak colour into every line printed after
    it. `render_group` already windows its plain content before wrapping it
    in a palette, so a group's code lines never need a second pass here; only
    plain header and footer text ever reaches this fallback.

    Args:
        line: the line to fit.
        width: how many cells are available.

    Returns:
        `line` unchanged if it already fits, holds a node id, or contains an
        escape sequence; otherwise a windowed copy.
    """
    if "::" in line or "\x1b" in line or visible_width(line) <= width:
        return line
    return window(line, 0, 0, width)[0]


# Indents. Two levels, not four: the location anchors at column 0, the status
# word sits under it, and the code sits under that. Deeper nesting spends
# columns to express a hierarchy that is only three deep.
STATUS_INDENT = 2
CODE_INDENT = 4
# Where a diff line's source text begins, counting the indent and the "- ".
SIGIL_WIDTH = CODE_INDENT + 2


def coverage_sentence(record: Record) -> list[str]:
    """Why nothing caught this mutant, in words.

    `tests_run` routes between two different jobs and so is not decoration.
    Zero means no test executes the line at all, and the action is to write one
    or delete the code -- there is no nearest test to read. A large number means
    the line is exercised and nothing asserts on the result, and the action is
    to strengthen an assertion.

    Args:
        record: one mutant's record.

    Returns:
        Zero, one, or two lines. Empty for anything but a survivor, because a
        timeout is a fact about the run rather than a gap in the tests.
    """
    if record["status"] != "SURVIVED":
        return []
    if record["module_level"]:
        # Selection widens to the whole suite for these, so the line-to-test
        # map attributes them to no single test and `nearest_test` is not
        # merely absent but inapplicable.
        return ["runs at import time; every test in the suite ran"]
    count = record["tests_run"]
    if count == 0:
        return ["no test runs this line at all"]
    noun = "test" if count == 1 else "tests"
    verb = "runs" if count == 1 else "run"
    lines = [f"{count} {noun} {verb} this line; first is"]
    if record["nearest_test"]:
        # Its own line, never truncated: a node id is a paste target, and the
        # head carries the path while the tail disambiguates, so neither end is
        # safe to cut. The terminal may soft-wrap it.
        lines.append(record["nearest_test"])
    return lines


def render_group(
    records: Sequence[Record],
    palette: Palette,
    *,
    timeout: float,
    width: int = FALLBACK_WIDTH,
) -> list[str]:
    """Every mutant sharing one file and line, as report lines.

    The `-` line is a property of the location rather than of a mutant, so it
    prints once however many mutants the line carries. So does the coverage
    sentence: `nearest_test` is computed per line, so rendering it per mutant
    would always duplicate it.

    Args:
        records: mutants sharing a file and line, in the order to print them.
        palette: the escape sequences to use, possibly empty.
        timeout: the run's configured timeout in seconds, used to word a
            TIMEOUT record's note. A `Record` carries no timeout value of its
            own, so this must come from the caller's `--timeout` rather than a
            hardcoded constant, or the note would print a number the run never
            used.
        width: how many columns the report may use. Every code line is
            windowed to fit it.

    Returns:
        The group's lines, without a trailing blank.
    """
    first = records[0]
    lines = [f"{first['file']}:{first['line']}"]
    # No dedent pass: `generate.py` stores `original` and `mutated` already
    # `.strip()`ed, so a record's operands never carry leading whitespace and
    # the deep-indentation case the spec describes cannot reach this module.
    original = sanitise(first["original"]).rstrip()
    budget = width - SIGIL_WIDTH
    shown_original = False
    for record in records:
        mutated = sanitise(record["mutated"]).rstrip()
        note = ""
        if record["status"] == "TIMEOUT":
            note = f"  (timed out after {timeout:g}s)"
        lines.append(
            f"{' ' * STATUS_INDENT}{record['status']}  {record['operator']}{note}"
        )
        if not shown_original:
            # Windowed around the same span that would highlight it as a
            # mutant, so the "-" line stays visually aligned with the "+"
            # line's ruler even though it carries no caret of its own.
            orig_start, orig_end = changed_span(mutated, original)
            windowed_original, _, _ = window(original, orig_start, orig_end, budget)
            lines.append(
                f"{' ' * CODE_INDENT}{palette.dim}{palette.minus}- "
                f"{windowed_original}{palette.reset}"
            )
            shown_original = True
        if mutated == original:
            # Two identical-looking lines would read as a rendering bug.
            lines.append(f"{' ' * CODE_INDENT}(differs only in trailing whitespace)")
            continue
        start, end = changed_span(original, mutated)
        windowed_mutated, w_start, w_end = window(mutated, start, end, budget)
        lines.append(
            f"{' ' * CODE_INDENT}{palette.bold}{palette.plus}+ "
            f"{windowed_mutated}{palette.reset}"
        )
        lines.append(ruler(windowed_mutated, w_start, w_end, SIGIL_WIDTH))
    lines.extend(
        _clip(f"{' ' * STATUS_INDENT}{line}", width)
        for line in coverage_sentence(first)
    )
    return lines


# The order counts appear in the footer. Survivors first because they are the
# work; killed and skipped last because they are context.
FOOTER_ORDER = ("SURVIVED", "TIMEOUT", "SUSPICIOUS", "KILLED", "SKIPPED")


def score_text(counts: Mapping[str, int]) -> str:
    """The kill rate, with the denominator visible.

    Suppressed mutants leave the denominator, because a mutant nobody could
    kill is not a test failure. The number appears in the footer rather than
    the header: at the top of a report a percentage reads as a target, and at
    the bottom it reads as an observation.

    Args:
        counts: per-status counts, as `report.summarise` returns.

    Returns:
        Something like "15/21 killed, 71%", or "n/a" when nothing was runnable.
    """
    total = sum(counts.values())
    runnable = total - counts.get("SKIPPED", 0)
    if runnable <= 0:
        return "n/a"
    killed = counts.get("KILLED", 0)
    return f"{killed}/{runnable} killed, {round(100 * killed / runnable)}%"


def render_footer(counts: Mapping[str, int], elapsed: float) -> str:
    """The report's closing lines: the tally, the artifact, and the exit code.

    Public because `--quiet` in human mode means the footer only, so `cli.py`
    needs the same three lines without the body above them. `render_report`
    calls this too, so there is exactly one implementation of them.

    Never clipped to a width. The score has to keep its denominator visible
    and the artifact path has to stay a whole path, so a narrow terminal soft
    wraps these rather than losing their tails.

    Args:
        counts: per-status counts, as `report.summarise` returns.
        elapsed: the run's wall clock, in seconds.

    Returns:
        Three newline-separated lines, with no trailing newline.
    """
    tally = ", ".join(
        f"{counts[status]} {status.lower()}"
        for status in FOOTER_ORDER
        if counts.get(status) or status in {"SURVIVED", "KILLED"}
    )
    return "\n".join(
        [
            f"{tally} in {elapsed:.1f}s -- {score_text(counts)}",
            "Full records: .moonbuggy/results.jsonl",
            "exit 1 -- survivors"
            if counts["SURVIVED"]
            else "exit 0 -- nothing survived",
        ]
    )


def render_report(
    records: Sequence[Record],
    *,
    palette: Palette,
    files: int,
    elapsed: float,
    timeout: float,
    width: int = FALLBACK_WIDTH,
) -> str:
    """The whole human report.

    Args:
        records: every mutant's record, in any order.
        palette: the escape sequences to use, possibly empty.
        files: how many source files were mutated.
        elapsed: the run's wall clock, in seconds.
        timeout: the run's configured timeout in seconds, forwarded to
            `render_group` so a TIMEOUT record's note names the budget the
            run actually used rather than a hardcoded guess.
        width: how many columns the report may use, forwarded to
            `render_group`. Group headers and the footer are never clipped to
            it: a `path:line` header must stay one contiguous token that a
            terminal and `$EDITOR +N` can act on, and the footer's score must
            keep its denominator, so both soft wrap instead.

    Returns:
        The report, newline-separated, with no trailing newline.
    """
    counts = summarise(records)
    mutant_noun = "mutant" if len(records) == 1 else "mutants"
    file_noun = "file" if files == 1 else "files"
    lines = [
        f"moonbuggy  {len(records)} {mutant_noun} across {files} {file_noun}",
        "",
    ]

    survivors = [r for r in records if r["status"] == "SURVIVED"]
    timeouts = [r for r in records if r["status"] == "TIMEOUT"]
    suspicious = [r for r in records if r["status"] == "SUSPICIOUS"]

    for group in _group_by_location(survivors):
        lines.extend(render_group(group, palette, timeout=timeout, width=width))
        lines.append("")

    if timeouts or suspicious:
        lines.extend(["Problems with the run", ""])
        for group in _group_by_location(timeouts):
            lines.extend(render_group(group, palette, timeout=timeout, width=width))
            lines.append("")
        if suspicious:
            # One cause, not N findings. The documented action -- investigate
            # the run -- is the same however many mutants are affected.
            lines.append(
                f"{len(suspicious)} mutants could not be answered confidently "
                "(SUSPICIOUS)."
            )
            lines.append(
                f"This is usually one cause rather than {len(suspicious)}.  "
                "See docs/troubleshooting.md"
            )
            lines.append("")

    lines.append(render_footer(counts, elapsed))
    return "\n".join(lines)


def _group_by_location(records: Sequence[Record]) -> list[list[Record]]:
    grouped: dict[tuple[str, int], list[Record]] = {}
    for record in records:
        grouped.setdefault((record["file"], record["line"]), []).append(record)
    return [grouped[key] for key in sorted(grouped)]
