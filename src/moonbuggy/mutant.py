"""The Mutant record.

Deliberately a plain frozen dataclass with no behaviour: it crosses process
boundaries (xdist workers) and gets persisted to the results cache, so it stays
trivially serialisable.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Mutant:
    """One candidate mutation of one source line.

    `id` is stable across runs for unchanged source (criterion C3) -- the
    results cache keys on it, so a shifting id silently invalidates the cache.
    It includes an occurrence index because a single line can host several
    mutants: line 9 of the inventory fixture carries three, from three
    different operators, and a line with two `+` operators would carry two from
    the same one. A file and a line alone are not an identity.
    """

    id: str
    module: str
    line: int
    operator: str
    original: str
    mutated: str
    suppressed: bool = False
    """True when this mutant is settled as `SKIPPED` without ever being run:
    the line carries the `# moonbuggy: skip` marker, or it sits inside a
    logging call and the run did not ask for those. Suppressed mutants leave
    the score's denominator -- a mutant nobody could kill is not a test
    failure -- but they are still reported, so the count stays honest."""
    module_level: bool = False
    """True when the mutated line runs at import time rather than inside a
    function body. Selection has to widen the test set for these: the line->test
    map is built from test-body execution, so a module-level line is attributed
    to no test, and an empty covering set reports a false NO_COVERAGE."""

    logging_call: bool = False
    """True when the mutation sits inside the arguments of a logging call, as
    :mod:`moonbuggy.logging_policy` recognises one. Nothing asserts on the
    contents of a debug line, so these survive whatever the tests do -- by
    default they are also `suppressed`, but the tag is set either way so a
    project that *does* assert on log output can run them and still tell them
    apart."""


def make_id(module: str, line: int, operator: str, index: int) -> str:
    """Build a mutant id from its four parts.

    Args:
        module: the module's path, as the mutant records it.
        line: the mutated line number.
        operator: the operator's name.
        index: the occurrence index within that line and operator.

    Returns:
        The id, in the ``path:line:operator:index`` form the reports print.
    """
    return f"{module}:{line}:{operator}:{index}"


def parse_id(mutant_id: str) -> tuple[str, int, str, int] | None:
    """Take a mutant id back apart.

    The inverse of :func:`make_id`, and the reason both live here: an id is the
    only thing a user hands back to moonbuggy (`moonbuggy show`, `moonbuggy
    accept`, `moonbuggy run`), so the format is a contract and it is defined in
    exactly one place.

    Split from the right, so a module path containing a colon -- a Windows
    drive letter -- survives the round trip.

    Args:
        mutant_id: an id as printed in `id=...`.

    Returns:
        ``(module, line, operator, index)``, or None if the string is not an
        id at all. None rather than an exception: callers get this string from
        a human or a pipeline and have to say something helpful about it.
    """
    parts = mutant_id.rsplit(":", 3)
    if len(parts) != 4:
        return None
    module, line, operator, index = parts
    if not module or not operator:
        return None
    try:
        return module, int(line), operator, int(index)
    except ValueError:
        return None
