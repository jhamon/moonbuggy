"""Loop-bearing arithmetic.

Fixture module. Contains the timeout case (a mutant that never terminates) and
the boundary/range case. See oracle.toml.
"""


def countdown(n):
    total = 0
    while n > 0:
        total += n
        n -= 1
    return total


def sum_first(n):
    total = 0
    for i in range(n):
        total += i
    return total
