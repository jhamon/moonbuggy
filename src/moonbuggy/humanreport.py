"""The human report: one grep-proof punch list of what survived, with diffs.

The agent format is one line per mutant and deliberately omits the diff, which
makes a survivor line say where but never what. This module says what.

Everything here is pure. Width and palette arrive as arguments, no function
reads the environment or writes to a stream, and the input is the same `Record`
that was written to `results.jsonl` -- so the human view cannot drift from the
canonical one, for the same reason the plaintext view cannot.
"""

from collections.abc import Sequence

from .report import Record
from .terminal import Palette, display_width, sanitise


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
    while end < len(mutated) and display_width(mutated[end]) == 0:
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
    records: Sequence[Record], palette: Palette, *, timeout: float
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

    Returns:
        The group's lines, without a trailing blank.
    """
    first = records[0]
    lines = [f"{first['file']}:{first['line']}"]
    original = sanitise(first["original"]).rstrip()
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
            lines.append(
                f"{' ' * CODE_INDENT}{palette.dim}{palette.minus}- "
                f"{original}{palette.reset}"
            )
            shown_original = True
        if mutated == original:
            # Two identical-looking lines would read as a rendering bug.
            lines.append(f"{' ' * CODE_INDENT}(differs only in trailing whitespace)")
            continue
        lines.append(
            f"{' ' * CODE_INDENT}{palette.bold}{palette.plus}+ {mutated}{palette.reset}"
        )
        start, end = changed_span(original, mutated)
        lines.append(ruler(mutated, start, end, SIGIL_WIDTH))
    lines.extend(f"{' ' * STATUS_INDENT}{line}" for line in coverage_sentence(first))
    return lines
