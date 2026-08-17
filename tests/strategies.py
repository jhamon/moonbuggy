"""Hypothesis strategies that generate Python modules (milestone M1.2).

Generating *source text* rather than ASTs, deliberately. moonbuggy's input is a
file, and half of what the properties check -- line attribution, splicing,
comment preservation, encodings -- are properties of the text, not of the tree.
An AST-only generator would make those properties unstatable, and they are the
ones most likely to be wrong.

The grammar is built as a small tree of tuples and rendered with an
indent-aware writer. Rendering from a tree rather than concatenating strings is
what makes every generated module parseable by construction: a strategy that
produced syntactically invalid programs would spend its whole budget rejecting
them and never reach the interesting cases.

Criterion M1.2.7 names the features the generator must reach. Each is produced
here, and `test_properties.py` asserts that each is reachable rather than
trusting this comment:

    nested functions, classes, comprehensions, decorators, async defs,
    chained comparisons, augmented assignment

Two things are generated on purpose that a naive generator would not bother
with, because they are what criterion M1.2.2 is about:

- string literals whose *contents* look mutable -- `"a + b"`, `"x > 1"`, `"# 1"`
- comments containing operators and numbers

Any mutation that touches those has escaped the AST and started editing text.
"""

import keyword

from hypothesis import strategies as st

NAMES = ["a", "b", "c", "x", "y", "total", "value", "items"]
PARAMS = ["p", "q", "r"]

BIN_OPS = ["+", "-", "*", "/", "//", "%", "**"]
AUG_OPS = ["+=", "-=", "*=", "//=", "%="]
BOOL_OPS = ["and", "or"]
COMPARE_OPS = ["<", "<=", ">", ">=", "==", "!="]

# Literals whose contents would be mutable if anything ever read them as code.
# These are the trap for criterion M1.2.2.
TRAP_STRINGS = [
    '"a + b"',
    "'x > 1'",
    '"1 + 1 == 2"',
    '"# moonbuggy: skip"',
    '"True and False"',
    '"range(10)"',
]
TRAP_COMMENTS = [
    "# 1 + 1 should stay 1 + 1",
    "# if x > 0 and y < 3",
    "# range(10) is not code here",
    "# total *= 2",
]

assert not any(name in keyword.kwlist for name in NAMES + PARAMS)


def identifiers():
    return st.sampled_from(NAMES)


def atoms():
    return st.one_of(
        st.sampled_from(NAMES),
        st.integers(min_value=-4, max_value=9).map(repr),
        st.sampled_from(["True", "False", "None"]),
        st.sampled_from(TRAP_STRINGS),
    )


def expressions(depth=2):
    """Expression source text, at most `depth` levels of nesting.

    Everything is parenthesised. Precedence bugs in a generator produce
    programs that parse but mean something other than they look like, and a
    property failure on one of those is a report about the generator rather
    than about moonbuggy.
    """
    if depth <= 0:
        return atoms()

    inner = expressions(depth - 1)
    return st.one_of(
        atoms(),
        st.builds("({} {} {})".format, inner, st.sampled_from(BIN_OPS), inner),
        st.builds("({} {} {})".format, inner, st.sampled_from(BOOL_OPS), inner),
        st.builds("({} {} {})".format, inner, st.sampled_from(COMPARE_OPS), inner),
        # A chained comparison is one Compare node with two ops, which the
        # comparison operator has to mutate one at a time.
        st.builds(
            "({} {} {} {} {})".format,
            inner,
            st.sampled_from(COMPARE_OPS),
            inner,
            st.sampled_from(COMPARE_OPS),
            inner,
        ),
        st.builds("range({})".format, inner),
        st.builds("len([{} for {} in range({})])".format, inner, identifiers(), inner),
        st.builds(
            "{{{}: {} for {} in range({})}}".format, inner, inner, identifiers(), inner
        ),
        st.builds(
            "(lambda {}={}: {})({})".format,
            st.sampled_from(PARAMS),
            inner,
            inner,
            inner,
        ),
        # f-string replacement fields take a restricted expression. PEP 701
        # permits nested quotes but still forbids `#` inside a field, and the
        # trap strings deliberately contain one -- so an unrestricted inner
        # here would generate unparseable modules rather than interesting ones.
        st.builds(
            'f"{{{}}}"'.format, st.sampled_from(NAMES + ["1 + 1", "x > 2", "a * 3"])
        ),
        st.builds("(not {})".format, inner),
        st.builds("[{}, {}]".format, inner, inner),
    )


def _simple_statements(expression):
    return st.one_of(
        st.builds(lambda n, e: ("assign", n, e), identifiers(), expression),
        st.builds(
            lambda n, o, e: ("augassign", n, o, e),
            identifiers(),
            st.sampled_from(AUG_OPS),
            expression,
        ),
        st.builds(lambda e: ("expr", e), expression),
        st.builds(lambda c: ("comment", c), st.sampled_from(TRAP_COMMENTS)),
        st.just(("pass",)),
    )


def statements(depth=2, expression_depth=2):
    """A list of statement nodes, possibly containing nested blocks."""
    expression = expressions(expression_depth)
    simple = _simple_statements(expression)
    if depth <= 0:
        return st.lists(simple, min_size=1, max_size=3)

    body = statements(depth - 1, expression_depth)
    compound = st.one_of(
        st.builds(lambda c, b, o: ("if", c, b, o), expression, body, body),
        st.builds(lambda n, i, b: ("for", n, i, b), identifiers(), expression, body),
        st.builds(lambda c, b: ("while", c, b), expression, body),
        # A function defined inside a function: its defaults and decorators run
        # in the ENCLOSING scope while its body does not, which is exactly the
        # distinction criterion M1.2.6 is about.
        st.builds(
            lambda n, d, b: ("def", n, d, [], b, False),
            identifiers(),
            st.lists(expression, max_size=2),
            body,
        ),
    )
    return st.lists(st.one_of(simple, compound), min_size=1, max_size=3)


def _definition(depth=2):
    body = statements(depth)
    decorators = st.lists(st.sampled_from(["staticmethod"]), max_size=0)
    return st.one_of(
        st.builds(
            lambda n, d, dec, b, a: ("def", n, d, dec, b, a),
            identifiers(),
            st.lists(expressions(1), max_size=2),
            st.lists(st.sampled_from(["_noop", "_tagged(1 + 1)"]), max_size=2),
            body,
            st.booleans(),  # async def
        ),
        st.builds(
            lambda n, dec, attrs, methods: ("class", n, dec, attrs, methods),
            st.sampled_from(["Alpha", "Beta"]),
            st.lists(st.sampled_from(["_noop"]), max_size=1),
            st.lists(expressions(1), min_size=0, max_size=2),
            st.lists(
                st.builds(
                    lambda n, b, d: ("def", n, [], d, b, False),
                    identifiers(),
                    statements(1),
                    decorators,
                ),
                min_size=1,
                max_size=2,
            ),
        ),
    )


PRELUDE = '''\
"""Generated module. The docstring is here because a module docstring is a
string constant at line 1, and criterion M1.2.2 says its content is untouchable."""

# 2 + 2 = 4, and this comment must survive every mutation
_LIMIT = 3 + 4


def _noop(fn):
    return fn


def _tagged(marker):
    def wrap(fn):
        return fn
    return wrap
'''


@st.composite
def module_source(draw, definitions=(1, 3)):
    """A complete, parseable Python module as source text.

    The prelude is fixed rather than generated: the decorators have to exist
    for the generated `@_noop` to be applyable, and a module-level constant
    with a mutable expression guarantees every example exercises the
    module-level branch of scope classification.
    """
    low, high = definitions
    nodes = draw(st.lists(_definition(), min_size=low, max_size=high))
    module_level = draw(st.lists(_simple_statements(expressions(1)), max_size=2))

    lines = PRELUDE.splitlines()
    lines.append("")
    _render(lines, module_level, 0)
    for node in nodes:
        lines.append("")
        lines.append("")
        _render(lines, [node], 0)
    return "\n".join(lines) + "\n"


def _render_block(lines, nodes, level):
    """Write an indented block, guaranteeing it is not empty.

    A body drawn as nothing but comments renders as a block with no statements,
    which is a SyntaxError. Emitting `pass` costs one uninteresting line and
    keeps every generated module parseable, which is the whole premise of
    generating from a tree.
    """
    if not _render(lines, nodes, level):
        lines.append("    " * level + "pass")


def _render(lines, nodes, level):
    """Write statement nodes as indented source lines.

    :returns: how many statements (as opposed to comments) were emitted.
    """
    pad = "    " * level
    emitted = 0
    for node in nodes:
        kind = node[0]
        if kind == "comment":
            lines.append(f"{pad}{node[1]}")
            continue
        emitted += 1
        if kind == "assign":
            lines.append(f"{pad}{node[1]} = {node[2]}")
        elif kind == "augassign":
            # Augmented assignment on a name that may not be bound yet would be
            # a runtime NameError. Nothing here is executed, only parsed and
            # mutated, but keeping it bindable keeps the module importable for
            # any future property that wants to run it.
            lines.append(f"{pad}{node[1]} = 1")
            lines.append(f"{pad}{node[1]} {node[2]} {node[3]}")
        elif kind == "expr":
            lines.append(f"{pad}{node[1]}")
        elif kind == "pass":
            lines.append(f"{pad}pass")
        elif kind == "if":
            lines.append(f"{pad}if {node[1]}:")
            _render_block(lines, node[2], level + 1)
            lines.append(f"{pad}else:")
            _render_block(lines, node[3], level + 1)
        elif kind == "for":
            lines.append(f"{pad}for {node[1]} in [{node[2]}]:")
            _render_block(lines, node[3], level + 1)
        elif kind == "while":
            lines.append(f"{pad}while {node[1]}:")
            _render_block(lines, node[2], level + 1)
            lines.append(f"{pad}    break")
        elif kind == "def":
            _render_def(lines, node, level)
        elif kind == "class":
            _render_class(lines, node, level)
        else:  # pragma: no cover - a typo in the grammar, not a real branch
            raise AssertionError(f"unknown node {kind}")
    return emitted


def _render_def(lines, node, level):
    _, name, defaults, decorators, body, is_async = node
    pad = "    " * level
    for decorator in decorators:
        lines.append(f"{pad}@{decorator}")
    params = ", ".join(
        f"{PARAMS[index]}={default}"
        for index, default in enumerate(defaults[: len(PARAMS)])
    )
    prefix = "async def" if is_async else "def"
    lines.append(f"{pad}{prefix} {name}({params}):")
    _render_block(lines, body, level + 1)


def _render_class(lines, node, level):
    _, name, decorators, attributes, methods = node
    pad = "    " * level
    for decorator in decorators:
        lines.append(f"{pad}@{decorator}")
    lines.append(f"{pad}class {name}:")
    for index, attribute in enumerate(attributes):
        # Class-body assignments execute at import time, so they must be
        # classified module level despite being indented.
        lines.append(f"{pad}    attr_{index} = {attribute}")
    for method in methods:
        _render_def(lines, method, level + 1)
