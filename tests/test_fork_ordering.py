"""C2 failing-fast ordering: cheap operators dispatch first (H31).

Tests the pure scheduling helpers the warm-host grandchild dispatcher uses,
without forking any process: :func:`_cheap_first_order` is the sort, and
:func:`_operator_dispatch_rank` is the key it sorts by. The ordering must be
unobservable in every result artifact -- statuses are keyed by job index, never
by dispatch position -- so these tests pin the *schedule*, not the output.
"""

from moonbuggy.forkserver import (
    Job,
    _cheap_first_order,
    _operator_dispatch_rank,
)
from moonbuggy.mutant import Mutant


def _mutant(operator: str) -> Mutant:
    return Mutant(
        id=f"{operator}-1",
        module="app/mod.py",
        line=1,
        operator=operator,
        original="x",
        mutated="y",
    )


def _jobs(operators: list[str]) -> list[Job]:
    return [Job(_mutant(op), []) for op in operators]


def test_cheap_operators_dispatch_first():
    rank = {"cheap": 0, "mid": 1, "pricey": 2}
    jobs = _jobs(["pricey", "cheap", "mid"])
    order = _cheap_first_order(jobs, rank)
    # cheap, then mid, then pricey -- regardless of caller order.
    assert [jobs[i].mutant.operator for i in order] == ["cheap", "mid", "pricey"]


def test_equal_cost_is_stable():
    rank = {"a": 0, "b": 0, "c": 0}
    jobs = _jobs(["b", "a", "c"])
    order = _cheap_first_order(jobs, rank)
    # All equal cost -> caller order preserved exactly (stable sort).
    assert [jobs[i].mutant.operator for i in order] == ["b", "a", "c"]


def test_empty_rank_is_identity():
    jobs = _jobs(["x", "y", "z"])
    assert _cheap_first_order(jobs, {}) == [0, 1, 2]


def test_unknown_operator_sorts_last():
    rank = {"known": 0}
    jobs = _jobs(["known", "mystery", "known"])
    order = _cheap_first_order(jobs, rank)
    # The unknown ranks last (worst), ties among known operators stable.
    assert [jobs[i].mutant.operator for i in order] == ["known", "known", "mystery"]


def test_order_is_a_permutation():
    jobs = _jobs(["a", "b", "c", "d"])
    order = _cheap_first_order(jobs, {"b": 0, "d": 0, "a": 2, "c": 2})
    assert sorted(order) == [0, 1, 2, 3]


def test_dispatch_rank_covers_every_operator():
    from moonbuggy.operators import describe_operators

    rank = _operator_dispatch_rank()
    assert rank  # a real registry read
    names = {info.name for info in describe_operators()}
    assert set(rank) == names


def test_deep_statement_deletion_ranks_after_cheap_arithmetic():
    rank = _operator_dispatch_rank()
    # statement_deletion is the expensive/deep operator; arithmetic_swap is a
    # cheap default-tier one. Failing-fast dispatches cheap first, so the rank
    # (smaller = sooner) must order arithmetic_swap before statement_deletion.
    assert rank["arithmetic_swap"] < rank["statement_deletion"]
