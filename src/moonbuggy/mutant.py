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
    the same one. file:line alone is not an identity.
    """

    id: str
    module: str
    line: int
    operator: str
    original: str
    mutated: str
    suppressed: bool = False
