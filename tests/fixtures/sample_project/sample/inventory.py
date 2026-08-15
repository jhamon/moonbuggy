"""Stock availability and restocking.

Fixture module. Deliberately under-tested: several mutants here survive, which
is the point. See oracle.toml.
"""


def is_available(stock, discontinued):
    return stock > 0 and not discontinued


def restock_amount(stock, target):
    if stock < target:
        return target - stock
    return 0
