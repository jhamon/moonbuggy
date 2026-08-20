"""Milestone M1.2 -- property-based testing of mutant generation.

The existing operator tests assert on examples chosen by the person who wrote
the operators. That is a useful check on intent and a poor check on coverage:
the cases someone thinks to write down are exactly the cases they had in mind
while writing the code. These properties are stated over *any* parseable
module, and Hypothesis picks the inputs.

Each property below is one M1.2 criterion, and each is phrased so that a
failure is a bug in moonbuggy rather than a surprise about Python. Where a
property is deliberately narrower than its criterion, the narrowing is stated
in the test's docstring rather than left for a reader to infer from the code.

Regression examples (criterion M1.2.8) are attached with `@example` and carry a
comment naming what they found. They stay attached after the fix: a shrunk
counterexample is the cheapest possible regression test, and deleting it after
the fix throws away the only evidence that the fix works.
"""

import ast
import io
import tokenize
import warnings
from collections import Counter

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis.errors import NonInteractiveExampleWarning
from strategies import module_source

from moonbuggy.generate import generate_mutants
from moonbuggy.operators import ALL_TIER, tier_members
from moonbuggy.srcio import replace_line

# 500 examples across six properties runs about two minutes, which is too
# long for the edit-run-edit loop and exactly right for a gate. Same reasoning
# as the other slow suites: `make check-properties` owns it.
pytestmark = pytest.mark.slow

# Criterion M1.2.7: at least 500 examples per property in CI. Generation is
# pure and in-process, so this costs seconds rather than minutes.
EXAMPLES = 500

PROPERTY = settings(
    max_examples=EXAMPLES,
    deadline=None,  # Module size varies enough that a per-example deadline
    # would fail on the large tail rather than on a bug.
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

# A module that once broke splicing: the mutated fragment sat on a line whose
# trailing whitespace was dropped by the round-trip. Kept as a permanent
# example so the fix cannot regress unnoticed.
TRAILING_WHITESPACE_CASE = "def f(x):\n    return x + 1  \n"

# A module-level `def` whose default argument contains a mutable expression.
# The default is evaluated at import time, in the enclosing scope -- so a
# mutant there is module level, and classifying it as in-function meant
# selecting no tests for it and reporting a false SURVIVED.
DEFAULT_ARGUMENT_CASE = "def f(p=1 + 2):\n    return p\n"

# A string literal inside a statement `statement_deletion` will delete. Not a
# bug that was found and fixed -- a case that pins the narrowing below, so the
# exempted branch of M1.2.2 is exercised on every run rather than only when
# Hypothesis happens to generate one.
STRING_IN_A_DELETED_STATEMENT_CASE = 'def f():\n    print("hello")\n    return 1\n'

# The same bug through a decorator: `@_tagged(1 + 1)` also runs at import time.
DECORATOR_ARGUMENT_CASE = (
    "def _tagged(marker):\n"
    "    def wrap(fn):\n"
    "        return fn\n"
    "    return wrap\n"
    "\n"
    "\n"
    "@_tagged(1 + 1)\n"
    "def f():\n"
    "    return 1\n"
)


# Every registered operator, not just the `default` tier.
#
# `generate_mutants`'s default -- `operators=None` -- is the `default` tier,
# which is what a bare `moonbuggy` run uses. Passing that default here would
# quietly exempt the whole `deep` tier from every property below: from PR #30
# until this suite was widened, `statement_deletion`, `argument_swap`,
# `default_arg` and `kwarg_drop` had no property coverage at all, while
# docs/writing-an-operator.md told contributors that a new operator was covered
# automatically. Asking for `all` by name is what makes that promise true, and
# it is why `_mutants` exists rather than each test calling `generate_mutants`
# directly -- one place to get this right.
EVERY_OPERATOR = tier_members(ALL_TIER)


def _mutants(source):
    """Every mutant of `source`, from every registered operator."""
    return generate_mutants(source, module="generated.py", operators=EVERY_OPERATOR)


def _mutate(source, mutant):
    """The full source with this mutant applied, as moonbuggy would apply it."""
    return replace_line(source, mutant.line, mutant.mutated)


# --------------------------------------------------------------------------
# M1.2.1 -- every generated mutant compiles
# --------------------------------------------------------------------------


@PROPERTY
@given(module_source())
@example(TRAILING_WHITESPACE_CASE)
@example(DEFAULT_ARGUMENT_CASE)
def test_every_mutant_compiles(source):
    """A mutant that does not compile surfaces as SUSPICIOUS and reads as a
    finding. It is not one -- it is moonbuggy producing invalid Python."""
    for mutant in _mutants(source):
        mutated = _mutate(source, mutant)
        try:
            ast.parse(mutated)
            compile(mutated, "<generated>", "exec")
        except SyntaxError as error:
            raise AssertionError(
                f"mutant {mutant.id} produced invalid source: {error}\n"
                f"--- original line ---\n{mutant.original}\n"
                f"--- mutated line ---\n{mutant.mutated}\n"
                f"--- module ---\n{source}"
            ) from error


# --------------------------------------------------------------------------
# M1.2.2 -- mutation never edits string or comment content
# --------------------------------------------------------------------------


def _string_literals(source):
    """Every string constant's *value*, as a multiset.

    A multiset rather than a set: a mutation that turned one of two identical
    literals into something else would leave the set unchanged.
    """
    return Counter(
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _comments(source):
    """Every comment's text, as a multiset. Read with tokenize, which is the
    only thing that knows a `#` inside a string is not a comment."""
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return Counter(token.string for token in tokens if token.type == tokenize.COMMENT)


def _deletes_a_whole_statement(mutant):
    """True when the mutation replaces one entire statement with `pass`.

    Asked of the mutation's *shape*, not of the operator's name. Naming
    `statement_deletion` here would encode today's registry into the property
    and would say nothing about why the exemption is legitimate; asking
    "does the replacement discard the statement?" is the actual condition, and
    any future operator that deletes a statement gets the same treatment
    without editing this file.

    A trailing comment survives the splice (`x = "a"  # note` becomes
    `pass  # note`), so this parses the fragment rather than comparing text.
    """
    try:
        fragment = ast.parse(mutant.mutated)
    except SyntaxError:
        # A mutated fragment that is not a standalone statement -- `if not x:`,
        # for instance. Whatever it is, it is not a deletion.
        return False
    return len(fragment.body) == 1 and isinstance(fragment.body[0], ast.Pass)


@PROPERTY
@given(module_source())
@example(STRING_IN_A_DELETED_STATEMENT_CASE)
def test_mutation_never_edits_a_string_or_a_comment(source):
    """Criterion C2, generalised past the one hand-written case.

    This is the property that justifies the whole AST-based design. A
    text-substitution mutation engine cannot state it, let alone hold it.

    The narrowing, stated rather than left to be inferred. Criterion C2 is
    "no mutation happens *inside* a string literal": the engine must never
    write into string or comment text, because a mutant there changes a
    message or a docstring and not behaviour. That is asserted here for every
    operator, in both halves:

    * No string or comment content is ever *invented or altered*. Every
      literal and every comment in the mutated module was already in the
      original, verbatim. This is the half that is criterion C2, and it is
      unconditional.
    * No string content is *removed* either -- except by a mutation that
      deletes the statement containing it outright, which is a different act
      from editing within a string. `statement_deletion` replacing
      `x = "hello"` with `pass` does not edit a string; it removes a
      statement, and the string goes with it because it was part of the
      statement. The original phrasing of this property -- an exact multiset
      equality -- was written when every operator was a token swap, and a
      token swap cannot remove anything. It does not fit a deletion operator,
      and holding it against one would be asserting that deletion operators
      may not exist.

    Comments stay under exact equality for every operator including deletion,
    because splicing preserves a trailing comment on a deleted line: nothing
    moonbuggy does should ever remove a comment.
    """
    strings_before = _string_literals(source)
    comments_before = _comments(source)

    for mutant in _mutants(source):
        mutated = _mutate(source, mutant)
        strings_after = _string_literals(mutated)

        assert strings_after <= strings_before, (
            f"mutant {mutant.id} invented or altered a string literal\n"
            f"- {mutant.original}\n+ {mutant.mutated}"
        )
        if not _deletes_a_whole_statement(mutant):
            assert strings_after == strings_before, (
                f"mutant {mutant.id} removed a string literal without "
                f"deleting the statement it belonged to\n"
                f"- {mutant.original}\n+ {mutant.mutated}"
            )
        assert _comments(mutated) == comments_before, (
            f"mutant {mutant.id} changed a comment\n"
            f"- {mutant.original}\n+ {mutant.mutated}"
        )


# --------------------------------------------------------------------------
# M1.2.3 -- ids are stable and unique
# --------------------------------------------------------------------------


@PROPERTY
@given(module_source())
def test_ids_are_stable_across_runs_and_unique_within_a_module(source):
    """The results cache keys on the id, so an id that shifts between runs
    silently serves the wrong cached status for a mutant that never changed."""
    first = [m.id for m in _mutants(source)]
    second = [m.id for m in _mutants(source)]

    assert first == second
    assert len(set(first)) == len(first), "duplicate ids: " + ", ".join(
        i for i, count in Counter(first).items() if count > 1
    )


# --------------------------------------------------------------------------
# M1.2.4 -- splicing round-trips
# --------------------------------------------------------------------------


@PROPERTY
@given(module_source())
@example(TRAILING_WHITESPACE_CASE)
def test_splicing_round_trips_byte_for_byte(source):
    """Apply the mutation, then put the original fragment back.

    Anything lost in that round trip -- trailing whitespace, a line ending, a
    trailing comment -- is something the mutation edited without meaning to,
    and therefore something a status could be wrong about.
    """
    for mutant in _mutants(source):
        mutated = _mutate(source, mutant)
        restored = replace_line(mutated, mutant.line, mutant.original)
        assert restored == source, (
            f"mutant {mutant.id} did not round-trip\n"
            f"--- expected line ---\n{source.splitlines()[mutant.line - 1]!r}\n"
            f"--- restored line ---\n{restored.splitlines()[mutant.line - 1]!r}"
        )


# --------------------------------------------------------------------------
# M1.2.5 -- line attribution is correct
# --------------------------------------------------------------------------


@PROPERTY
@given(module_source())
def test_every_mutant_reports_the_line_it_actually_changed(source):
    """The reported line number must contain the reported original text.

    This is the field a user navigates by. Being wrong here sends them to a
    line that has nothing to do with the finding, which is worse than no
    location at all because it looks authoritative.
    """
    lines = source.splitlines()
    for mutant in _mutants(source):
        assert 1 <= mutant.line <= len(lines)
        assert mutant.original == lines[mutant.line - 1].strip()

        mutated = _mutate(source, mutant)
        changed = [
            index
            for index, (before, after) in enumerate(
                zip(lines, mutated.splitlines(), strict=True), start=1
            )
            if before != after
        ]
        assert changed == [mutant.line], (
            f"mutant {mutant.id} claims line {mutant.line} but changed {changed}"
        )


# --------------------------------------------------------------------------
# M1.2.6 -- scope classification is sound
# --------------------------------------------------------------------------

CO_OPTIMIZED = 0x0001
CO_NEWLOCALS = 0x0002


def _import_time_lines(source):
    """Lines that execute at import time, according to CPython itself.

    Deliberately not a second AST walk. The implementation under test derives
    this from the tree; deriving the oracle the same way would mostly test that
    two similar traversals agree with each other. Compiling the module and
    reading the line tables asks a completely different authority -- the code
    generator's own record of which bytecode belongs to which line.

    Import time means the module's code object plus the code objects of class
    bodies reachable through import-time scopes. A class body executes when the
    `class` statement runs; a class defined inside a function therefore does
    not run at import time, which is why this walks reachability rather than
    collecting every class body in the file.

    :returns: ``(import_time, known)`` -- lines that run at import time, and
        every line any code object accounts for. Lines in neither are ones the
        compiler folded away entirely, and nothing can be claimed about them.
    """
    module_code = compile(source, "<oracle>", "exec")

    import_time = set()
    known = set()
    pending = [(module_code, True)]
    while pending:
        code, at_import = pending.pop()
        for _, _, line in code.co_lines():
            if line is None:
                continue
            known.add(line)
            if at_import:
                import_time.add(line)

        for const in code.co_consts:
            if not hasattr(const, "co_code"):
                continue
            # A class body has its own locals but is not an optimised scope;
            # a function is both. That flag pair is how CPython itself tells
            # them apart.
            is_class_body = not (const.co_flags & CO_OPTIMIZED) and (
                const.co_flags & CO_NEWLOCALS
            )
            pending.append((const, at_import and is_class_body))

    return import_time, known


@PROPERTY
@given(module_source())
@example(DEFAULT_ARGUMENT_CASE)
@example(DECORATOR_ARGUMENT_CASE)
def test_scope_classification_matches_an_independent_scope_map(source):
    """Getting this wrong in the module-level direction is a false SURVIVED.

    A module-level line executes at import time, so the coverage map -- built
    from test-body execution -- attributes it to no test at all. Classified as
    in-function, it selects no tests, nothing can kill it, and moonbuggy
    reports a survivor that is really an artefact of its own bookkeeping.

    The assertion is one-directional on purpose. Claiming module level when the
    line is not is merely wasteful: selection widens to the whole suite, the
    run is slower, and every status is still right. Claiming the opposite is
    the false SURVIVED. So this asserts the safe direction strictly and lets
    the wasteful direction pass -- which is also what makes the deliberately
    conservative treatment of lambdas legal rather than a hole.
    """
    import_time, known = _import_time_lines(source)

    for mutant in _mutants(source):
        if mutant.line not in known:
            continue  # Constant-folded away; no code object claims it.
        if mutant.line in import_time:
            assert mutant.module_level, (
                f"mutant {mutant.id} on line {mutant.line} runs at import time "
                f"but was classified as deferred, so selection will run no "
                f"tests for it and report a false SURVIVED\n"
                f"line: {mutant.original}\n--- module ---\n{source}"
            )


# --------------------------------------------------------------------------
# M1.2.7 -- the strategy generates non-trivial programs
# --------------------------------------------------------------------------

FEATURES = {
    "nested functions": lambda tree: any(
        isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
        for outer in ast.walk(tree)
        if isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef))
        for inner in ast.walk(outer)
        if inner is not outer
    ),
    "classes": lambda tree: any(isinstance(n, ast.ClassDef) for n in ast.walk(tree)),
    "comprehensions": lambda tree: any(
        isinstance(n, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp))
        for n in ast.walk(tree)
    ),
    "decorators": lambda tree: any(
        getattr(n, "decorator_list", []) for n in ast.walk(tree)
    ),
    "async defs": lambda tree: any(
        isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(tree)
    ),
    "chained comparisons": lambda tree: any(
        isinstance(n, ast.Compare) and len(n.ops) > 1 for n in ast.walk(tree)
    ),
    "augmented assignment": lambda tree: any(
        isinstance(n, ast.AugAssign) for n in ast.walk(tree)
    ),
    "lambdas": lambda tree: any(isinstance(n, ast.Lambda) for n in ast.walk(tree)),
    # The two test positions that are not statements. Without these the
    # properties would say nothing about condition_negation on the shapes it
    # exists for, and the operator's whole point is that it reaches conditions
    # no other operator does.
    "conditional expressions": lambda tree: any(
        isinstance(n, ast.IfExp) for n in ast.walk(tree)
    ),
    "comprehension guards": lambda tree: any(
        isinstance(n, ast.comprehension) and n.ifs for n in ast.walk(tree)
    ),
}


SAMPLES_FOR_FEATURE_COVERAGE = 120


def test_the_strategy_reaches_every_named_feature():
    """Criterion M1.2.7's second half, as a test rather than as a claim.

    A property suite is only as good as its inputs. Asserting in a comment that
    the generator produces async defs is worth nothing: a run that never
    generated one would pass every property above and prove nothing about it.
    This is the test that would fail if someone tightened the grammar to fix an
    unrelated generation bug and quietly made a whole feature unreachable.

    Sampled directly rather than through `@given`, and deliberately so. This is
    a question about the *distribution* the strategy produces, which is not a
    property of any single example -- and Hypothesis's shrinking, which is what
    makes it good at the other question, spends minutes here reducing a module
    that was already an acceptable answer.
    """
    strategy = module_source()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NonInteractiveExampleWarning)
        trees = [
            ast.parse(strategy.example()) for _ in range(SAMPLES_FOR_FEATURE_COVERAGE)
        ]

    missing = sorted(
        feature
        for feature, predicate in FEATURES.items()
        if not any(predicate(tree) for tree in trees)
    )
    assert not missing, (
        f"the strategy never generated {', '.join(missing)} in "
        f"{SAMPLES_FOR_FEATURE_COVERAGE} samples, so no property above is "
        "testing that feature"
    )
