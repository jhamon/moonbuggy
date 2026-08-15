"""Milestone M1.4.8 -- very large and very deeply nested source.

Kept out of the slow suite: these exercise generation, which is in-process and
needs no pytest subprocess, so they belong in the edit-run-edit loop where a
regression in the walk shows up immediately.

The bound stated below is deliberately generous. It is there to catch an
accidental quadratic, not to be a performance benchmark -- M2 owns performance,
and a tight bound here would fail on a loaded machine for no useful reason.
"""

import time

import pytest

from moonbuggy.generate import GenerationError, generate_mutants

# The stated bound for M1.4.8. A 10k-line module is a real thing to encounter
# in generated code and in a few well-known libraries.
LARGE_FILE_BUDGET_SECONDS = 60.0


def _large_source(functions=2000):
    """Roughly 10k lines: five lines per function, all of them mutable."""
    return "\n\n".join(
        f"def step_{n}(x):\n"
        f"    total = x + {n}\n"
        f"    if total > {n}:\n"
        f"        total = total * 2\n"
        f"    return total"
        for n in range(functions)
    ) + "\n"


def _deeply_nested_source(depth=90):
    """One function whose body is `depth` nested `if` statements.

    Statement nesting is capped by CPython itself at 100 levels of indentation,
    so this is as deep as block nesting can go. It is still deeper than a
    recursive walk that also recursed for every expression node would survive.
    """
    lines = ["def deep(x):"]
    for level in range(depth):
        lines.append("    " * (level + 1) + f"if x > {level}:")
    lines.append("    " * (depth + 1) + "return x + 1")
    lines.append("    return 0")
    return "\n".join(lines) + "\n"


def _deep_expression_source(terms=1000):
    """One expression nested `terms` deep.

    This is where nesting really has no ceiling: CPython's parser accepts a
    chain of thousands of binary operators, building a tree thousands of nodes
    deep, and every recursive pass over it -- the walk, `copy.deepcopy`,
    `ast.unparse` -- has to survive that depth.
    """
    return "def chained(x):\n    return x" + " + 1" * terms + "\n"


def test_a_ten_thousand_line_module_completes_within_the_stated_bound():
    source = _large_source()
    assert source.count("\n") > 10_000

    started = time.perf_counter()
    mutants = generate_mutants(source, module="large.py")
    elapsed = time.perf_counter() - started

    assert len(mutants) > 5_000
    assert elapsed < LARGE_FILE_BUDGET_SECONDS, f"took {elapsed:.1f}s"


def test_deeply_nested_blocks_do_not_raise_a_recursion_error():
    mutants = generate_mutants(_deeply_nested_source(), module="deep.py")

    # 90 `if x > N` comparisons plus their 90 integer constants, plus the
    # trailing arithmetic. The count matters less than the absence of a crash.
    assert len(mutants) > 180


def test_a_deeply_nested_expression_does_not_raise_a_recursion_error():
    # 1000 terms nests the tree 1000 deep -- past the default recursion limit,
    # which is what makes this fail without the raised limit in generation.
    mutants = generate_mutants(_deep_expression_source(), module="chained.py")

    assert len(mutants) > 1_000


def test_an_expression_too_deep_to_rewrite_is_reported_not_crashed(monkeypatch):
    """Past the raised limit, individual sites are skipped and announced.

    Losing a mutant is a real loss, so it must not be silent: a file that
    quietly produced half its mutants looks exactly like a file with half as
    many mutable sites.

    The failure is provoked by an operator that raises rather than by an
    expression deep enough to do it naturally. Such expressions exist -- around
    5000 chained operators, the tree still parses but `deepcopy` and `unparse`
    give up -- but generating one costs a quadratic number of node copies and
    put this single test above a minute. The behaviour under test is what
    generation does with a RecursionError from an operator, and that is exactly
    what this provokes.
    """
    class ExplodingOperator:
        name = "exploding"

        def mutations(self, node):
            raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(
        "moonbuggy.generate.all_operators", lambda: [ExplodingOperator()]
    )
    skipped = []

    mutants = generate_mutants(
        "def add(a, b):\n    return a + b\n",
        module="verydeep.py",
        on_skip=lambda line, reason: skipped.append((line, reason)),
    )

    assert mutants == []
    assert skipped, "sites were dropped without saying so"
    assert all("too deeply nested" in reason for _, reason in skipped)


def test_line_attribution_survives_deep_nesting():
    """The iterative walk must not scramble which line a mutant belongs to."""
    source = _deeply_nested_source(depth=50)
    lines = source.splitlines()

    for mutant in generate_mutants(source, module="deep.py"):
        assert mutant.original == lines[mutant.line - 1].strip()


def test_source_that_cannot_be_parsed_raises_a_named_error():
    with pytest.raises(GenerationError) as raised:
        generate_mutants("def broken(:\n    pass\n", module="broken.py")

    assert "syntax error at line 1" in str(raised.value)


def test_nesting_past_the_parsers_own_limit_is_reported_not_crashed():
    """CPython refuses more than 100 levels of indentation outright.

    That refusal arrives as a SyntaxError, and it has to reach the user as a
    GenerationError naming the file rather than as a traceback out of the
    middle of generation.
    """
    with pytest.raises(GenerationError) as raised:
        generate_mutants(_deeply_nested_source(depth=150), module="verydeep.py")

    assert "too many levels of indentation" in str(raised.value)
