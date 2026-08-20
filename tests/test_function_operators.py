"""The function-interface operators: argument_swap, default_arg, kwarg_drop.

Every existing operator works *inside* an expression -- swap a comparison, bump
a constant, flip a boolean. These three work at the boundary between a function
and its callers, which is a class of real bug nothing else in the set reaches.

Two claims are pinned here, and as with `statement_deletion` they pull against
each other:

- *reach* -- the operator fires on every shape of the mistake it models,
  because a site it declines to mutate is a finding the tool never reports;
- *restraint* -- it does not fire where the mutation is provably equivalent,
  where the source is meaningless to swap, or where another operator already
  produces the identical mutant. Each of those is a line somebody has to read
  and dismiss by hand.

Tested through `generate_mutants` rather than by handing an operator a bare
node: `default_arg` decides entirely from where the node sits, and a node on
its own carries no such thing.
"""

import ast

import pytest

from moonbuggy.generate import generate_mutants
from moonbuggy.operators import ALL_TIER, describe_operators, tier_members
from moonbuggy.srcio import replace_line
from moonbuggy.verify import resolve_targets

FUNCTION_OPERATORS = ("argument_swap", "default_arg", "kwarg_drop")


def mutated(source, operator):
    """The mutated lines `operator` produces for `source`, in generation order."""
    return [
        m.mutated
        for m in generate_mutants(source, module="lib.py", operators=[operator])
    ]


# --- the tier ---------------------------------------------------------------


def test_the_function_operators_are_all_deep_tier():
    """`docs/writing-an-operator.md` asks for evidence from a real codebase
    before an operator joins the set a bare `moonbuggy` runs, and there is no
    `docs/oss-findings.md` entry for any of these three yet. `deep` is where an
    operator waits for that evidence: opt-in, and costing nobody who has not
    asked for it."""
    infos = {info.name: info for info in describe_operators()}

    for name in FUNCTION_OPERATORS:
        assert infos[name].tier == "deep", name


def test_a_default_run_generates_none_of_them():
    source = (
        "def fetch(url, timeout=None):\n    return connect(url, timeout, retries=3)\n"
    )

    produced = {m.operator for m in generate_mutants(source, module="lib.py")}
    assert not produced & set(FUNCTION_OPERATORS)


def test_they_are_reachable_by_name_and_by_the_deep_tier():
    """Opt-in has to mean something you can actually type."""
    assert set(FUNCTION_OPERATORS) <= set(tier_members("deep"))
    assert set(FUNCTION_OPERATORS) <= set(tier_members(ALL_TIER))


# --- argument_swap ----------------------------------------------------------


def test_argument_swap_swaps_an_adjacent_pair():
    assert mutated("resize(width, height)\n", "argument_swap") == [
        "resize(height, width)"
    ]


def test_argument_swap_is_adjacent_only():
    """One mutant per adjacent pair, so an n-argument call costs n-1 mutants
    rather than n!. `f(a, c, b)` is reachable from `f(a, b, c)`; `f(c, b, a)`
    is not, and buying it would mean quadratic mutant counts for a mistake
    nobody makes twice in one call."""
    assert mutated("f(a, b, c)\n", "argument_swap") == ["f(b, a, c)", "f(a, c, b)"]


def test_argument_swap_needs_two_positional_arguments():
    assert mutated("f(a)\n", "argument_swap") == []
    assert mutated("f()\n", "argument_swap") == []
    assert mutated("f(a, key=b)\n", "argument_swap") == []


def test_argument_swap_skips_arguments_that_are_identical_as_source():
    """`f(x, x)` and `f(0, 0)` are equivalent mutants by construction. No
    inference is needed to see it and none is done."""
    assert mutated("f(x, x)\n", "argument_swap") == []
    assert mutated("f(0, 0)\n", "argument_swap") == []
    assert mutated("f(a.b, a.b)\n", "argument_swap") == []


def test_argument_swap_still_fires_on_the_pairs_around_an_identical_one():
    """The guard is per-pair, not per-call: one duplicated pair must not
    silence the rest of the call."""
    assert mutated("f(x, x, y)\n", "argument_swap") == ["f(x, y, x)"]


def test_argument_swap_skips_a_starred_position():
    """`f(*args, y)` unpacks a sequence of unknown length. Swapping the two
    changes which values land where in a way no reader can reason about, so
    it is not the mistake this models."""
    assert mutated("f(*args, y)\n", "argument_swap") == []
    assert mutated("f(x, *args)\n", "argument_swap") == []


def test_argument_swap_leaves_keywords_alone():
    """Keywords are named at the call site, so their order cannot be a
    mistake. They ride along untouched."""
    assert mutated("f(a, b, key=c)\n", "argument_swap") == ["f(b, a, key=c)"]


def test_argument_swap_fires_inside_a_method_call():
    assert mutated("self.resize(w, h)\n", "argument_swap") == ["self.resize(h, w)"]


# --- default_arg ------------------------------------------------------------


def test_default_arg_turns_a_none_default_into_zero():
    """The mistake modelled: a sentinel default that should have been a
    concrete value. It also separates `if timeout is None:` from
    `if not timeout:` -- 0 is falsy, so only the identity check notices."""
    assert mutated("def fetch(url, timeout=None):\n    pass\n", "default_arg") == [
        "def fetch(url, timeout=0):"
    ]


def test_default_arg_fires_on_a_keyword_only_default():
    assert mutated("def fetch(url, *, timeout=None):\n    pass\n", "default_arg") == [
        "def fetch(url, *, timeout=0):"
    ]


def test_default_arg_fires_on_a_positional_only_default():
    assert mutated("def fetch(timeout=None, /):\n    pass\n", "default_arg") == [
        "def fetch(timeout=0, /):"
    ]


def test_default_arg_fires_on_a_lambda_default():
    assert mutated("f = lambda timeout=None: timeout\n", "default_arg") == [
        "f = lambda timeout=0: timeout"
    ]


def test_default_arg_leaves_a_parameter_with_no_default_alone():
    assert mutated("def fetch(url):\n    pass\n", "default_arg") == []
    assert mutated("def fetch(*args, **kw):\n    pass\n", "default_arg") == []


def test_default_arg_does_not_repeat_what_the_constant_operators_reach():
    """`def fetch(url, retries=3)` -> `retries=4` is a real and wanted mutant,
    and `constant_int` already produces it -- in the *default* tier, where
    more people will see it. Producing it here as well would put two
    byte-identical survivors in the report under two ids, which is the same
    double-count `condition_negation` refuses when it declines to negate a
    literal test."""
    assert mutated("def fetch(url, retries=3):\n    pass\n", "default_arg") == []
    assert mutated("def fetch(url, strict=True):\n    pass\n", "default_arg") == []

    by_constant = mutated("def fetch(url, retries=3):\n    pass\n", "constant_int")
    assert by_constant == ["def fetch(url, retries=4):"]


def test_default_arg_leaves_defaults_it_has_no_plausible_mutation_for():
    """A string, a float and a computed default all have obvious-looking
    mutations and no *narrow* one. Mutating a string default would also break
    criterion C2, which says no mutation touches a string literal."""
    assert mutated('def f(mode="strict"):\n    pass\n', "default_arg") == []
    assert mutated("def f(ratio=1.5):\n    pass\n", "default_arg") == []
    assert mutated("def f(clock=time.time):\n    pass\n", "default_arg") == []
    assert mutated("def f(items=()):\n    pass\n", "default_arg") == []


def test_default_arg_ignores_none_everywhere_that_is_not_a_default():
    """The whole operator is a claim about *position*. A `None` in a return, a
    comparison or a call argument is not a default and is not its business."""
    source = "def f(x):\n    if x is None:\n        return None\n    return g(None)\n"
    assert mutated(source, "default_arg") == []


def test_default_arg_reports_the_def_line():
    """Splicing the default expression, not the `FunctionDef`: unparsing the
    function would drag its whole body onto one line."""
    source = "def fetch(\n    url,\n    timeout=None,\n):\n    pass\n"
    found = generate_mutants(source, module="lib.py", operators=["default_arg"])

    assert [(m.line, m.original, m.mutated) for m in found] == [
        (3, "timeout=None,", "timeout=0,")
    ]


# --- kwarg_drop -------------------------------------------------------------


def test_kwarg_drop_removes_an_explicit_keyword():
    """The question it asks is "does the value you passed actually matter?"."""
    assert mutated("connect(host, timeout=30)\n", "kwarg_drop") == ["connect(host)"]


def test_kwarg_drop_produces_one_mutant_per_keyword():
    assert mutated("connect(host, timeout=30, retries=2)\n", "kwarg_drop") == [
        "connect(host, retries=2)",
        "connect(host, timeout=30)",
    ]


def test_kwarg_drop_skips_a_double_starred_argument():
    """`**extra` names no parameter, so there is no "the callee's default
    applies instead" to test. It is also unbounded: dropping it removes an
    unknown number of arguments at once."""
    assert mutated("connect(host, **extra)\n", "kwarg_drop") == []


def test_kwarg_drop_keeps_the_double_star_while_dropping_a_named_one():
    assert mutated("connect(host, timeout=30, **extra)\n", "kwarg_drop") == [
        "connect(host, **extra)"
    ]


def test_kwarg_drop_does_nothing_to_a_call_with_no_keywords():
    assert mutated("connect(host, 30)\n", "kwarg_drop") == []


def test_kwarg_drop_does_not_touch_a_function_definition():
    """`def f(timeout=30)` has defaults, not keyword arguments. Removing one
    there would change the interface rather than exercise it, and would not
    even parse for a parameter that follows one with a default."""
    assert mutated("def f(timeout=30):\n    pass\n", "kwarg_drop") == []


# --- the shared invariants --------------------------------------------------

COMPILES = (
    "def fetch(url, timeout=None, retries=3, *rest, mode=None, **extra):\n"
    "    payload = build(url, timeout, mode=mode, strict=True)\n"
    "    send(payload, *rest, **extra)\n"
    "    return [wrap(a, b) for a, b in pairs(url, timeout)]\n"
)


@pytest.mark.parametrize("operator", FUNCTION_OPERATORS)
def test_every_mutant_compiles(operator):
    """The property `make check-properties` states over generated modules,
    asserted here too: the deep tier is not part of that run, so a splicing
    mistake in one of these would otherwise reach nothing."""
    for mutant in generate_mutants(COMPILES, module="lib.py", operators=[operator]):
        patched = replace_line(COMPILES, mutant.line, mutant.mutated)
        compile(patched, "<mutated>", "exec")


@pytest.mark.parametrize("operator", FUNCTION_OPERATORS)
def test_no_mutant_edits_a_string_literal(operator):
    """Criterion C2. `default_arg` is the one with a temptation here -- a
    string default is a constant in a position it cares about."""
    source = 'def f(label="keep me", timeout=None):\n    return log("keep me", label)\n'
    before = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    for mutant in generate_mutants(source, module="lib.py", operators=[operator]):
        patched = replace_line(source, mutant.line, mutant.mutated)
        after = [
            node.value
            for node in ast.walk(ast.parse(patched))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert sorted(after) == sorted(before), mutant.id


@pytest.mark.parametrize("operator", FUNCTION_OPERATORS)
def test_a_multi_line_call_is_declined_rather_than_mangled(operator):
    """`_splice` rewrites one line by column offset. A call spread over four
    lines has no single-line diff, and the guard that drops it is the engine's
    rather than the operator's -- asserted so that a future operator author
    does not go looking for a special case that is not there."""
    source = "connect(\n    host,\n    port,\n    timeout=30,\n)\n"

    assert mutated(source, operator) == []


@pytest.mark.parametrize("operator", FUNCTION_OPERATORS)
def test_run_and_why_can_still_address_a_deep_tier_mutant(tmp_path, operator):
    """`moonbuggy run <id>` and `moonbuggy why <id>` regenerate the module to
    find the id they were handed, and they regenerate with *every* operator
    rather than the default tier. A deep-tier operator whose ids could not be
    fixed up afterwards would print findings nobody could act on."""
    module = "lib.py"
    (tmp_path / module).write_text(COMPILES, encoding="utf-8")
    found = generate_mutants(COMPILES, module=module, operators=[operator])
    assert found, f"{operator} generated nothing to look up"

    resolved = resolve_targets(tmp_path, [m.id for m in found])

    assert [m.id for m in resolved] == [m.id for m in found]
    assert [m.mutated for m in resolved] == [m.mutated for m in found]
