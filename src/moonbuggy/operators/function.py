"""Function-interface mutations: argument order, defaults, and explicit keywords.

Every other operator in the set works *inside* an expression -- swap a
comparison, bump a constant, flip a boolean. These three work at the boundary
between a function and its callers, which is where a large class of real bugs
lives and which no existing operator reaches:

- two arguments passed in the wrong order (`argument_swap`);
- a default that should have been a different value (`default_arg`);
- an explicit keyword argument that turns out not to matter (`kwarg_drop`).

All three are plausible mistakes in the sense the fourth rule of
`docs/writing-an-operator.md` asks for: a human could easily have written each
one, and a suite that never notices is a suite that never checks the boundary
it depends on most.

WHY ALL THREE ARE `deep`
========================

The tier is the one real decision in this module, and it went the conservative
way. `docs/writing-an-operator.md` sets the bar for the `default` set
explicitly: run the operator against a real codebase and count real gaps
against noise, in the `docs/oss-findings.md` format. No such evidence exists
for any of these three yet, and for `argument_swap` in particular the honest
position is that nobody knows its equivalent rate -- there is no type inference
here, so every call whose two adjacent arguments are genuinely interchangeable
produces an equivalent mutant that a person has to read and dismiss.

`deep` is where an operator waits for that evidence. It is opt-in
(`--operators deep`, `--operators +argument_swap`), it costs nothing to anyone
who has not asked for it, and promoting an operator to `default` later is a
one-line change plus an `oss-findings.md` entry. Demoting one after it has been
in the default set is a change to what every existing run reports.

`kwarg_drop` belongs in `deep` on the stronger, permanent argument: when the
parameter it drops is required, every test errors with `TypeError`. That is a
kill that proves nothing about test quality, which is exactly why it needs
`KILLED_BY_ERROR` reporting alongside it -- without that, this operator inflates
the kill rate and teaches nobody anything.
"""

import ast
from collections.abc import Iterator

from . import Context, Mutation, register, replace_operator

# The fields of `ast.arguments` holding default *expressions*. `defaults`
# covers positional-only and ordinary parameters together; `kw_defaults` covers
# keyword-only ones and is the list that also holds a bare Python `None` for a
# keyword-only parameter with no default at all -- which the walk never yields,
# because it is not an AST node.
DEFAULT_FIELDS = frozenset({"defaults", "kw_defaults"})


@register
class ArgumentSwap:
    """Swap two adjacent positional arguments in a call.

    `resize(width, height)` becomes `resize(height, width)`. A survivor means
    nothing in the suite distinguishes the two, which for a call whose
    arguments mean different things is a gap.

    Three sites are skipped. Only *adjacent* pairs are swapped, which is what
    keeps an n-argument call at n-1 mutants instead of n! (see `mutations`);
    a pair is skipped when either side is a starred argument, and when the two
    sides are identical as source. Both of those are provably equivalent
    swaps rather than judgement calls -- `_is_equivalent_swap` carries the
    reasoning, including why the guards stop there.
    """

    name = "argument_swap"
    tier = "deep"
    cost = "medium"

    def mutations(self, node: ast.AST) -> Iterator[ast.AST]:
        """Yield the call with one adjacent pair of arguments exchanged.

        Adjacent pairs only, so an n-argument call costs n-1 mutants rather
        than n!. Transposing two neighbours is also the mistake people
        actually make; permuting three arguments is not.

        Args:
            node: any AST node; only a `Call` with two or more positional
                arguments produces mutations.

        Yields:
            One replacement node per swappable adjacent pair.
        """
        if not isinstance(node, ast.Call):
            return
        args = node.args
        for index in range(len(args) - 1):
            left, right = args[index], args[index + 1]
            if _is_equivalent_swap(left, right):
                continue
            swapped = list(args)
            swapped[index], swapped[index + 1] = right, left
            # Keywords ride along untouched: they are named at the call site,
            # so their order cannot be the mistake this models.
            yield replace_operator(node, args=swapped)


def _is_equivalent_swap(left: ast.expr, right: ast.expr) -> bool:
    """Whether exchanging these two arguments provably cannot change anything.

    Two guards, and both are cheap certainties rather than inference:

    - a starred argument unpacks a sequence of unknown length, so swapping it
      with its neighbour is not "the two arguments in the wrong order" at all;
    - two arguments identical as source -- `f(x, x)`, `f(0, 0)` -- transpose
      to the same call. An equivalent mutant by construction.

    The guards deliberately stop there. Whether `f(a, b)` and `f(b, a)` differ
    for two *distinct* expressions is a question about types and about the
    callee, and there is no type inference in this tool to ask it with. Those
    equivalents are the cost of the operator, and the accepted-equivalents
    ledger is where they get retired.

    Args:
        left: the earlier argument.
        right: the argument immediately after it.

    Returns:
        True when the swap is not worth generating.
    """
    if isinstance(left, ast.Starred) or isinstance(right, ast.Starred):
        return True
    # Source equality rather than structural comparison: `ast.unparse`
    # normalises whitespace and parentheses, so `f(x, (x))` is caught too, and
    # `ast.AST` has no `__eq__` that would answer this.
    return ast.unparse(left) == ast.unparse(right)


@register
class DefaultArg:
    """Turn a `None` default into `0`.

    Defaults are configuration callers rely on and tests almost never
    exercise, because a test that cares passes the argument explicitly. A
    survivor means nothing checks what happens when the caller does not.
    """

    name = "default_arg"
    tier = "deep"
    cost = "low"

    def mutations_in_context(
        self, node: ast.AST, context: Context
    ) -> Iterator[Mutation]:
        """Yield a replacement for this parameter's default, if it has one worth making.

        The node mutated is the default *expression* -- the `None` in
        `timeout=None` -- and not the `ast.arguments` holding it, which carries
        no `lineno`, nor the `FunctionDef`, which would unparse its entire body
        onto one line. The default expression is a single-line `expr` even
        inside a signature spread over four lines, so it splices correctly;
        `context` is what says it is a default rather than any other literal.

        Args:
            node: any AST node; only a default expression qualifies.
            context: where `node` sits, which is the whole of the position half
                of the decision.

        Yields:
            One replacement node, if this default has a narrow mutation.
        """
        if context.field not in DEFAULT_FIELDS:
            return
        if not isinstance(context.parent, ast.arguments):
            return
        if not _is_none(node):
            return
        # `0` rather than some other value because it is falsy like `None` and
        # so separates the two ways code tests a sentinel default: `if timeout
        # is None:` takes the other branch, `if not timeout:` does not. It is
        # also the mistake itself -- `timeout=0` written to mean "no timeout"
        # is a bug people ship.
        yield ast.Constant(value=0)


def _is_none(node: ast.AST) -> bool:
    """Whether this default is the literal `None`.

    The one shape this operator mutates, and the narrowness is the point.
    Every other default is either already covered or has no *narrow*
    replacement:

    - an integer or boolean default is reached by `constant_int` and
      `constant_bool`, in the `default` tier where more people will see it.
      Producing `retries=4` here as well would put two byte-identical
      survivors in the report under two ids -- the same double count
      `condition_negation` refuses when it declines to negate a literal test.
    - a float default is not mutated for the same reason no float is: it
      invites float-comparison flakiness for no extra signal.
    - a string default cannot be mutated at all without risking a mutation
      inside a string literal.
    - a computed default (`clock=time.time`, `items=()`) has obvious-looking
      mutations and no plausible one.

    Args:
        node: the default expression.

    Returns:
        True if it is the literal `None`.
    """
    return isinstance(node, ast.Constant) and node.value is None


@register
class KwargDrop:
    """Drop an explicit keyword argument so the callee's default applies.

    `connect(host, timeout=30)` becomes `connect(host)`. A survivor means the
    value the caller went to the trouble of passing makes no difference to
    anything the suite checks.
    """

    name = "kwarg_drop"
    tier = "deep"
    cost = "medium"
    description = "Drop an explicit keyword argument, so the callee's default applies."

    def mutations(self, node: ast.AST) -> Iterator[ast.AST]:
        """Yield the call with one keyword argument removed.

        Expect crash-kills. When the parameter is required rather than
        defaulted, every test raises `TypeError` -- reported `KILLED_BY_ERROR`
        rather than `KILLED`, so those kills do not quietly inflate the rate.

        Args:
            node: any AST node; only a `Call` with a named keyword produces
                mutations.

        Yields:
            One replacement node per named keyword argument.
        """
        if not isinstance(node, ast.Call):
            return
        for index, keyword in enumerate(node.keywords):
            # `**extra` has `arg is None`. It names no parameter, so there is
            # no "the callee's default applies instead" to test, and it stands
            # for an unknown number of arguments -- dropping it removes all of
            # them at once, which is a different and much blunter mutation.
            if keyword.arg is None:
                continue
            remaining = [
                other
                for position, other in enumerate(node.keywords)
                if position != index
            ]
            yield replace_operator(node, keywords=remaining)
