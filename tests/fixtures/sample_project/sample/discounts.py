"""Bulk discount rules.

Fixture module. Every mutable site here is deliberate and enumerated in
oracle.toml -- do not add incidental constants or operators.
"""

BULK_THRESHOLD = 10


def qualifies_for_bulk(quantity):
    return quantity >= BULK_THRESHOLD


def discount_percent(quantity):
    if qualifies_for_bulk(quantity):
        return 20
    return 0
