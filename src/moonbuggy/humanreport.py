"""The human report: one grep-proof punch list of what survived, with diffs.

The agent format is one line per mutant and deliberately omits the diff, which
makes a survivor line say where but never what. This module says what.

Everything here is pure. Width and palette arrive as arguments, no function
reads the environment or writes to a stream, and the input is the same `Record`
that was written to `results.jsonl` -- so the human view cannot drift from the
canonical one, for the same reason the plaintext view cannot.
"""

from .terminal import display_width


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
