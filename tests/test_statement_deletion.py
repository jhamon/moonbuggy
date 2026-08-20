"""`statement_deletion` and the inertness heuristic it is built on.

Two separate claims are pinned here and they pull in opposite directions:

- *coverage* -- every statement whose removal could be observed is mutated,
  because a statement this operator declines to touch is a finding the tool
  will never report;
- *inertness* -- the closed list of shapes that provably cannot be observed is
  never mutated, because each one is an equivalent mutant somebody has to read
  and dismiss by hand.

The heuristic runs in the inert direction on purpose. A mistake in the first
list costs a real finding; a mistake in the second costs noise. See the module
docstring of `moonbuggy.operators.deletion`.

Tested through `generate_mutants` rather than by handing the operator a bare
node: half the decision is about where the statement sits, and a node on its
own carries no such thing.
"""

import pytest

from moonbuggy.generate import generate_mutants
from moonbuggy.operators import ALL_TIER, describe_operators, tier_members


def deletions(source):
    """The `(original, mutated)` pairs statement_deletion produces for a source."""
    return [
        (m.original, m.mutated)
        for m in generate_mutants(
            source, module="lib.py", operators=["statement_deletion"]
        )
    ]


def deleted(source):
    """Just the original lines statement_deletion offers to replace with `pass`."""
    return [original for original, _ in deletions(source)]


# --- the tier ---------------------------------------------------------------


def test_statement_deletion_is_a_deep_operator():
    """The whole reason #15 landed first. Roughly one extra mutant per
    statement is not something a bare `moonbuggy` should start doing."""
    info = {i.name: i for i in describe_operators()}["statement_deletion"]

    assert info.tier == "deep"
    assert info.cost == "high"


def test_a_default_run_generates_no_deletions():
    """`generate_mutants` with no operator selection is the `default` tier,
    not every registered operator. Before a deep operator existed those two
    sets were equal and the difference could not be observed."""
    source = "def f(x):\n    return x + 1\n"

    assert [
        m
        for m in generate_mutants(source, module="lib.py")
        if m.operator == "statement_deletion"
    ] == []
    assert any(
        m.operator == "statement_deletion"
        for m in generate_mutants(
            source, module="lib.py", operators=tier_members(ALL_TIER)
        )
    )


# --- the mutation itself ----------------------------------------------------


def test_a_statement_becomes_pass():
    assert deletions("def f(x):\n    log(x)\n") == [("log(x)", "pass")]


def test_indentation_is_preserved():
    """Splicing at `col_offset` rather than rewriting the line is what makes
    this work at all: a `pass` at column zero would not parse."""
    mutants = generate_mutants(
        "def f(x):\n    if x:\n        run(x)\n",
        module="lib.py",
        operators=["statement_deletion"],
    )

    assert [m.mutated for m in mutants] == ["pass"]
    # `original`/`mutated` are stripped for the report; the spliced line is
    # what the runner applies, and it keeps the eight spaces.
    assert mutants[0].line == 3


def test_a_one_statement_body_stays_valid():
    """`pass` rather than removal, so an `if` whose only statement went away
    is still a program."""
    assert deletions("def f(x):\n    if x:\n        run(x)\n") == [("run(x)", "pass")]


def test_a_return_is_deleted_which_is_return_value_mutation_for_free():
    assert deleted("def f(x):\n    return x * 2\n") == ["return x * 2"]


def test_a_multi_line_statement_excludes_itself():
    """`_splice` refuses any node spanning several lines, and this operator
    deliberately does not work around that -- a multi-line deletion is not a
    one-line diff, which the whole pipeline is built on."""
    assert deletions("def f(x):\n    run(\n        x,\n    )\n") == []


@pytest.mark.parametrize(
    "statement",
    [
        "x[0] = 1",
        "self.total = 1",
        "total += 1",
        "raise ValueError('no')",
        "assert x",
        "del x",
        "break",
        "continue",
        "queue.append(1)",
    ],
)
def test_statements_with_an_effect_are_mutated(statement):
    """The impactful set is arrived at by subtraction: anything not provably
    inert is mutated, including every shape here."""
    source = f"def f(x, queue, total, self):\n    while x:\n        {statement}\n"

    assert statement in deleted(source)


def test_a_bare_call_expression_is_mutated():
    assert deleted("def f(x):\n    notify(x)\n") == ["notify(x)"]


def test_an_attribute_expression_is_mutated():
    """`obj.prop` on its own line may be a property with a side effect, and
    proving otherwise needs type information this deliberately does not have."""
    assert deleted("def f(obj):\n    obj.prop\n") == ["obj.prop"]


# --- provably inert: never mutated ------------------------------------------


def test_a_docstring_is_never_deleted():
    assert deletions('def f(x):\n    """Explain."""\n    return x\n') == [
        ("return x", "pass")
    ]


def test_a_module_docstring_is_never_deleted():
    assert deletions('"""A module."""\nX = call()\n') == [("X = call()", "pass")]


@pytest.mark.parametrize("statement", ["pass", "...", "global g", "nonlocal g"])
def test_statements_that_do_nothing_locally_are_never_deleted(statement):
    source = f"def outer():\n    g = 1\n    def f():\n        {statement}\n"

    assert statement not in deleted(source)


@pytest.mark.parametrize("statement", ["import os", "from os import path"])
def test_imports_are_never_deleted(statement):
    """Not because deleting one is equivalent -- it is the opposite, a
    `NameError` at every use. That is a crash-kill carrying no information
    about test quality, so it inflates the score and teaches nothing."""
    source = f"{statement}\ndef f():\n    return 1\n"

    assert statement not in deleted(source)


@pytest.mark.parametrize("statement", ["x", "42", "'a string'", "None"])
def test_a_bare_constant_or_name_is_never_deleted(statement):
    """A guaranteed equivalent mutant: nothing about evaluating a name or a
    literal for its value alone can be observed."""
    source = f"def f(x):\n    {statement}\n    return x\n"

    assert deleted(source) == ["return x"]


# --- the dead-store analysis ------------------------------------------------


def test_a_local_never_read_again_is_not_mutated():
    """The one cheap local analysis, and the shape it pays for: a scratch
    binding with a pure right-hand side that nothing reads is provably
    equivalent to no binding at all."""
    source = "def f(x):\n    unused = x + 1\n    return x\n"

    assert deleted(source) == ["return x"]


def test_a_local_read_later_is_mutated():
    source = "def f(x):\n    scaled = x + 1\n    return scaled\n"

    assert "scaled = x + 1" in deleted(source)


def test_a_local_read_earlier_in_a_loop_is_mutated():
    """ "Never read *again*" is implemented as "never read anywhere in the
    function", because a read above the write is still a read after it once
    the two share a loop. The stricter rule is the correct one."""
    source = "def f(items):\n    for i in items:\n        use(seen)\n        seen = i\n"

    assert "seen = i" in deleted(source)


def test_a_call_on_the_right_hand_side_keeps_the_store_mutable():
    """The binding is dead, but the call is not: deleting the line deletes
    the call too, and that is exactly the mutation worth making."""
    source = "def f(x):\n    unused = notify(x)\n    return x\n"

    assert "unused = notify(x)" in deleted(source)


def test_an_await_on_the_right_hand_side_keeps_the_store_mutable():
    source = "async def f(x):\n    unused = await fetch\n    return x\n"

    assert "unused = await fetch" in deleted(source)


def test_an_attribute_target_is_never_a_dead_store():
    """`self.x = 1` writes somewhere this function cannot see the readers of,
    so the analysis has no claim and the statement is mutated."""
    source = "def f(self):\n    self.total = 1\n"

    assert deleted(source) == ["self.total = 1"]


def test_a_subscript_target_is_never_a_dead_store():
    source = "def f(d, k):\n    d[k] = 1\n"

    assert deleted(source) == ["d[k] = 1"]


def test_a_local_read_by_a_nested_function_is_mutated():
    """The walk covers the whole `def`, closures included, so a binding a
    nested function reads is not dead."""
    source = "def f(x):\n    captured = x + 1\n    return lambda: captured\n"

    assert "captured = x + 1" in deleted(source)


def test_a_local_augmented_later_is_mutated():
    """`total += 1` reads `total`, and its target `Name` carries a `Store`
    context that says otherwise. Asked of the `AugAssign`, not the `Name`."""
    source = "def f(x):\n    total = 0\n    total += x\n"

    assert "total = 0" in deleted(source)


def test_a_function_that_can_read_its_own_locals_gets_no_dead_store_claim():
    """`locals()` reads every binding without any `Name` node naming it, so
    the analysis has nothing to prove and mutates the statement."""
    source = "def f(x):\n    unused = x + 1\n    return locals()\n"

    assert "unused = x + 1" in deleted(source)


def test_a_module_level_binding_is_never_a_dead_store():
    """There is no body to prove the absence of readers against: anything
    that imports the module can read the name."""
    assert deleted("THRESHOLD = 10\n") == ["THRESHOLD = 10"]


def test_a_global_declared_name_is_mutated():
    source = "def f(x):\n    global counter\n    counter = x + 1\n"

    assert "counter = x + 1" in deleted(source)
