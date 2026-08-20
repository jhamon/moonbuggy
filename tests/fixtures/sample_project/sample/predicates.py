"""Predicate-shaped conditions.

Fixture module for condition_negation: one condition of each shape the operator
recognises, and nothing else mutable. No integers, no comparisons, no boolean
operators and no literals in test position, so every mutant here comes from
condition_negation and the inventory in oracle.toml stays readable.
"""


def is_ready(item):
    return item.ready


def describe(item):
    if is_ready(item):
        return "ready"
    return "waiting"


def label(flag):
    if flag:
        return "on"
    return "off"


def gate(blocked):
    if not blocked:
        return "open"
    return "closed"


def verdict(ok):
    return "yes" if ok else "no"


def wanted(values):
    return [value for value in values if value]
