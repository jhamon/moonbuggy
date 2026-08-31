"""Mutation operators, and the registry that discovers them.

Operators are discovered by
importing every module in this package, so adding one means adding a file here
and nothing else -- no edit to the engine's traversal, no import list to update,
no registration call to remember. The engine asks for `all_operators()` and
never learns their names.

An operator is any class decorated with @register that provides:

    name        str -- stable identifier, matches the operator names in oracle.toml
    mutations() takes an AST node, yields replacement nodes (possibly none)

and may optionally declare, as plain class attributes:

    tier        str -- "default" (the default) or "deep"
    cost        str -- "low" (the default), "medium" or "high"
    description str -- one line for the listing, defaulting to the docstring

Tier lives on the operator for the same reason discovery does: a tier table in
a central file would mean adding an operator required editing two places, and
the one property this package is built around is that it requires editing
none.

An operator whose decision depends on where the node sits implements
`mutations_in_context(node, context)` instead, and is handed a :class:`Context`
describing its parent, the field it occupies, and the chain of enclosing nodes.
Either method may yield a bare replacement for the node it was given, or a
`(target, replacement)` pair naming a different node to rewrite.

Operators must not mutate the node they are given. `replace_operator` is the
supported way to obey that without paying for a deep copy.
"""

import ast
import copy
import importlib
import pkgutil
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# What an operator yields. A bare node replaces the node the operator was
# handed; a pair names the node to replace and its replacement.
#
# The pair form exists because the node an operator has to *see* to make its
# decision is not always the node it wants to *edit*. `_splice` rewrites one
# source line by column offset and so refuses any node spanning several lines,
# which structurally excludes every compound statement: an operator handed an
# `ast.If` could never yield a replacement for it. Targeting a child -- the
# test expression, a single statement in a body -- keeps the edit inside one
# line while leaving the decision at whatever altitude it needs.
Mutation = ast.AST | tuple[ast.AST, ast.AST]


@dataclass(frozen=True, slots=True)
class Context:
    """Where a node sits in the tree it was walked from.

    Handed to operators that implement `mutations_in_context`. Without it an
    operator sees a bare `ast.Name` and cannot tell the test of an `if` from
    the right-hand side of an assignment -- and "negate every name" is absurd
    where "negate the ones in test position" is exactly right.

    Frozen, and built lazily: `outer` links to the parent node's own context
    rather than materialising an ancestor tuple per node, so a deeply nested
    expression costs one small object per node instead of one tuple per node
    whose length grows with depth.
    """

    #: The enclosing node, or None for the root of the walk.
    parent: ast.AST | None
    #: The field of `parent` holding this node -- "test", "body", "ifs",
    #: "defaults". None at the root.
    field: str | None
    #: This node's position within `field` when that field is a list, and None
    #: when it holds a single node. "Where am I in the enclosing body?" is
    #: this, plus `parent`.
    index: int | None
    #: The parent node's own context, or None at the root.
    outer: "Context | None"

    @property
    def ancestors(self) -> tuple[ast.AST, ...]:
        """Every enclosing node, outermost first.

        Returns:
            the chain from the root of the walk down to the immediate parent,
            empty at the root.
        """
        chain = []
        context: Context | None = self
        while context is not None and context.parent is not None:
            chain.append(context.parent)
            context = context.outer
        chain.reverse()
        return tuple(chain)

    def nearest(self, *types: type[ast.AST]) -> ast.AST | None:
        """The closest enclosing node of any of `types`.

        "What is my nearest enclosing Call?" is the question this answers, and
        it walks outwards lazily rather than through `ancestors`, so the common
        case of a hit one or two levels up costs two comparisons.

        Args:
            *types: AST node classes to look for.

        Returns:
            the innermost enclosing node matching any of `types`, or None.
        """
        context: Context | None = self
        while context is not None and context.parent is not None:
            if isinstance(context.parent, types):
                return context.parent
            context = context.outer
        return None


class NodeOperator(Protocol):
    """The shape of an operator that needs nothing but the node.

    A class decorated with @register needs a stable `name` and a `mutations`
    method that takes an AST node and yields its mutated replacements (or
    nothing). This is a structural type, not a base class -- operator modules
    never import it and never subclass anything; they just happen to match it.
    """

    name: str

    def mutations(self, node: ast.AST) -> Iterator[Mutation]:
        """Yield replacements for `node`, or nothing if none apply."""
        ...


@runtime_checkable
class ContextualOperator(Protocol):
    """The shape of an operator that needs to know where the node sits.

    A second method name rather than a second parameter on `mutations`, so
    that an operator which does not need context does not have to accept it.
    The engine tells the two apart by which method is present; nothing else
    about registration or discovery changes.
    """

    name: str

    def mutations_in_context(
        self, node: ast.AST, context: Context
    ) -> Iterator[Mutation]:
        """Yield replacements for `node` in `context`, or nothing if none apply."""
        ...


# What the engine holds. Widened rather than replaced: the five operators that
# predate the context seam are still exactly the shape they were.
Operator = NodeOperator | ContextualOperator


def replace_operator[NodeT: ast.AST](node: NodeT, **changes: object) -> NodeT:
    """A shallow copy of `node` with some fields replaced.

    Every operator needs the same thing: the original node with one field
    different. The obvious way to write that is `copy.deepcopy(node)` followed
    by an assignment, and every operator did -- which makes mutating one
    expression cost a copy of its entire subtree. An expression with *n* nested
    operators then costs O(n^2) node copies, and a 6000-term expression took
    over a minute to generate.

    A shallow copy is enough because nothing downstream writes to the tree.
    Generation unparses the replacement node and throws it away; the children
    it shares with the original are only ever read. What the deep copy actually
    bought was protection against an operator that mutated its input, and that
    protection is better provided by not writing such an operator -- which this
    function makes the path of least resistance.

    Args:
        node: the AST node being mutated. Not modified.
        **changes: field values to replace on the copy. Untyped because the
            fields vary per AST node type (`op` for a `BinOp`, `ops` for a
            `Compare`, `args` for a `Call`); `ast.AST` itself declares no
            common field set to check them against.

    Returns:
        a new node of the same type, sharing the untouched children.
    """
    replacement = copy.copy(node)
    for field, value in changes.items():
        setattr(replacement, field, value)
    return ast.copy_location(replacement, node)


#: The tier an operator lands in when it does not say. Every operator that
#: predates tiers is cheap and high-signal, which is what `default` means, so
#: silence and the truth coincide.
DEFAULT_TIER = "default"

#: The tiers an operator may declare itself into, cheapest first.
#:
#: `default` is what a bare `moonbuggy` runs: cheap to run, high signal.
#: `deep` is for operators that are expensive in wall-clock or noisy in
#: output, and is opted into deliberately -- `statement_deletion` is its
#: first member, at roughly one extra mutant per statement.
TIERS = ("default", "deep")

#: Not a tier an operator can declare, but a selector meaning every registered
#: operator regardless of tier. Kept out of `TIERS` so that "which tier is this
#: operator in?" and "what can I type after --operators?" stay two questions.
ALL_TIER = "all"

#: Selector words an operator may not take as its name. Enforced at
#: registration rather than documented, because the failure it prevents is
#: silent: an operator file named `deep` would shadow the tier, and every
#: existing `--operators deep` would quietly start meaning something else.
RESERVED_SELECTORS = frozenset({*TIERS, ALL_TIER})

#: Rough wall-clock-and-noise cost, for the `moonbuggy operators` listing.
#: Deliberately three coarse buckets rather than a number: the honest claim is
#: an ordering, and a measured per-operator cost would be a property of the
#: project under test, not of the operator.
COSTS = ("low", "medium", "high")

#: The cost an operator claims when it does not say.
DEFAULT_COST = "low"


class SelectionError(Exception):
    """A `--operators` selection that cannot be resolved to a set of operators.

    Raised for an unknown name, a malformed `+` token, or a selection that
    resolves to no operators at all. The last one matters most: before tiers,
    a typo'd operator name produced a run with zero mutants that exited 0 and
    read exactly like a clean bill of health.
    """


@dataclass(frozen=True, slots=True)
class OperatorInfo:
    """One operator, as `moonbuggy operators` reports it."""

    #: The name used in mutant ids, in `results.jsonl`, and in `--operators`.
    name: str
    #: Which of :data:`TIERS` it belongs to.
    tier: str
    #: One line about what it mutates.
    description: str
    #: One of :data:`COSTS`.
    cost: str


_REGISTRY: list[type[Operator]] = []


def register[OperatorT: type[Operator]](cls: OperatorT) -> OperatorT:
    """Register an operator class. Used as a decorator.

    Args:
        cls: the operator class.

    Returns:
        `cls`, unchanged.

    Raises:
        ValueError: if the name collides with a reserved selector word, or the
            declared tier or cost is not one this version knows. A contributor
            error rather than a user error, so it is loud at import time.
    """
    name = cls.name
    if name in RESERVED_SELECTORS:
        raise ValueError(
            f"operator name {name!r} is reserved: it is a --operators selector "
            f"word ({', '.join(sorted(RESERVED_SELECTORS))}). Choose another name."
        )
    tier = getattr(cls, "tier", DEFAULT_TIER)
    if tier not in TIERS:
        raise ValueError(
            f"operator {name!r} declares tier {tier!r}; the tiers are "
            f"{', '.join(TIERS)}."
        )
    cost = getattr(cls, "cost", DEFAULT_COST)
    if cost not in COSTS:
        raise ValueError(
            f"operator {name!r} declares cost {cost!r}; the costs are "
            f"{', '.join(COSTS)}."
        )
    _REGISTRY.append(cls)
    return cls


def describe_operators() -> list[OperatorInfo]:
    """Every registered operator's name, tier, description and cost.

    What `moonbuggy operators` prints, in both of its forms. The description
    falls back to the first line of the class docstring, so an operator author
    who writes the docstring they were going to write anyway gets a usable
    listing entry for free.

    Returns:
        One entry per operator, sorted by name -- the same order
        :func:`all_operators` uses.
    """
    _discover()
    return [
        OperatorInfo(
            name=cls.name,
            tier=getattr(cls, "tier", DEFAULT_TIER),
            description=_description(cls),
            cost=getattr(cls, "cost", DEFAULT_COST),
        )
        for cls in sorted(_REGISTRY, key=lambda c: c.name)
    ]


def _description(cls: type[Operator]) -> str:
    explicit = getattr(cls, "description", None)
    if explicit:
        return str(explicit).strip()
    docstring = cls.__doc__ or ""
    return docstring.strip().splitlines()[0].strip() if docstring.strip() else ""


def tier_members(tier: str) -> tuple[str, ...]:
    """The operator names in `tier`, sorted.

    Args:
        tier: one of :data:`TIERS`, or :data:`ALL_TIER`.

    Returns:
        The names, sorted. Empty is a legitimate answer -- a tier with no
        members is a state this version has been in and can be in again.
    """
    infos = describe_operators()
    if tier == ALL_TIER:
        return tuple(info.name for info in infos)
    return tuple(info.name for info in infos if info.tier == tier)


def resolve_operators(spec: str) -> frozenset[str]:
    """Turn a `--operators` selection into the set of names it selects.

    The grammar is comma-separated tokens, each either a bare selector or one
    prefixed with `+`:

    - A bare list of names is an *exact* set. `comparison_swap,boundary` meant
      exactly those two before tiers existed and means exactly those two now;
      everything else here is layered on top of that, never underneath it.
    - A bare tier name expands to that tier's members.
    - A `+` token adds to a base rather than replacing it. The base is
      whatever the bare tokens named, or the `default` tier when there are
      none -- so `+statement_deletion` is "the ordinary run plus this one",
      which is the case that would otherwise mean typing out the default set
      by hand.

    Args:
        spec: the raw `--operators` value.

    Returns:
        The selected operator names.

    Raises:
        SelectionError: for an unknown selector, a bare `+`, or a selection
            that resolves to no operators at all.
    """
    known = {info.name for info in describe_operators()}
    base: set[str] = set()
    added: set[str] = set()
    saw_bare = False
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            # A trailing or doubled comma is a typo with an obvious intent,
            # and refusing it teaches nothing.
            continue
        if token.startswith("+"):
            selector = token[1:].strip()
            if not selector:
                raise SelectionError(
                    "`+` in --operators must be followed by an operator or tier "
                    f"name, as in `+{sorted(known)[0]}`."
                )
            added |= _expand(selector, known)
        else:
            saw_bare = True
            base |= _expand(token, known)

    if not saw_bare:
        base = set(tier_members(DEFAULT_TIER))
    selected = base | added
    if not selected:
        empty = _empty_tiers_named(spec)
        raise SelectionError(
            f"--operators {spec!r} selects no operators. "
            + (
                f"The tier{'s' if len(empty) > 1 else ''} "
                f"{', '.join(repr(name) for name in empty)} "
                f"{'have' if len(empty) > 1 else 'has'} no members in this "
                "version. "
                if empty
                else ""
            )
            + "Run `moonbuggy operators` to see what is available."
        )
    return frozenset(selected)


def _empty_tiers_named(spec: str) -> list[str]:
    """The tiers this selection named that have no members in this version.

    Args:
        spec: the raw `--operators` value.

    Returns:
        The tier names, sorted. Empty when the selection failed for some other
        reason -- which keeps the error from explaining a tier the user never
        typed.
    """
    selectors = {token.strip().lstrip("+").strip() for token in spec.split(",")}
    return sorted(
        selector
        for selector in selectors
        if selector in TIERS and not tier_members(selector)
    )


def _expand(selector: str, known: set[str]) -> set[str]:
    """One selector's names: a tier's members, or the single operator it names."""
    if selector == ALL_TIER or selector in TIERS:
        return set(tier_members(selector))
    if selector in known:
        return {selector}
    raise SelectionError(
        f"unknown operator or tier {selector!r}. "
        f"Operators: {', '.join(sorted(known))}. "
        f"Tiers: {', '.join([*TIERS, ALL_TIER])}. "
        "Run `moonbuggy operators` for the full listing."
    )


def all_operators() -> list[Operator]:
    """Every registered operator, instantiated.

    Sorted by name so mutant ordering -- and therefore mutant ids -- stay stable
    across runs regardless of filesystem iteration order.
    """
    _discover()
    return [cls() for cls in sorted(_REGISTRY, key=lambda c: c.name)]


_discovered = False


def _discover() -> None:
    global _discovered
    if _discovered:
        return
    for info in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{info.name}")
    _discovered = True
