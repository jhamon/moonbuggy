"""Operator tiers, additive selection, and the `moonbuggy operators` listing.

Three things are pinned here and they are not the same thing:

- the *vocabulary* -- which tier names exist, that they are reserved, and that
  an operator declares its own tier rather than being named in a central table;
- the *grammar* -- what `--operators` accepts, and in particular that a bare
  list of names still means exactly what it meant before tiers existed;
- the *listing* -- that an agent can enumerate operators instead of guessing.
"""

import contextlib
import json

import pytest

import moonbuggy.operators as operators_pkg
from moonbuggy.cli import _build_parser, main
from moonbuggy.operators import (
    ALL_TIER,
    RESERVED_SELECTORS,
    TIERS,
    SelectionError,
    describe_operators,
    register,
    resolve_operators,
    tier_members,
)


@contextlib.contextmanager
def probe_operator(name, tier="deep", **attributes):
    """Register a throwaway operator for the body of the test.

    Appended to the live registry rather than written to a file: nothing here
    is testing discovery -- that is `tests/test_operator_seam.py` -- and these
    tests need an operator in a tier that has no members in this version.
    """
    operators_pkg.all_operators()  # force discovery before touching the registry
    namespace = {"name": name, "tier": tier, **attributes}
    cls = register(type("Probe", (), namespace))
    try:
        yield cls
    finally:
        operators_pkg._REGISTRY[:] = [
            c for c in operators_pkg._REGISTRY if c is not cls
        ]


def names(spec):
    return sorted(resolve_operators(spec))


# --- the vocabulary ---------------------------------------------------------


def test_every_built_in_operator_declares_a_tier():
    """Tier membership lives on the operator, not in a table somewhere else:
    adding an operator must stay 'add a file and nothing else'."""
    assert {info.tier for info in describe_operators()} <= set(TIERS)


def test_the_tiers_partition_the_operators():
    """Every operator is in exactly one tier and both tiers have members.
    This replaced an assertion that `deep` was empty, which was the honest
    state of the version that introduced tiers and stopped being true the
    moment `statement_deletion` landed."""
    default = set(tier_members("default"))
    deep = set(tier_members("deep"))

    assert default and deep
    assert not default & deep
    assert default | deep == {info.name for info in describe_operators()}


def test_the_deep_tier_membership_is_named_not_counted():
    """`deep` is opt-in, so which operators a bare `moonbuggy` does *not* run
    is part of the contract and is written out rather than counted. It has
    grown once already -- the function-interface operators joined
    `statement_deletion` there, and they are in `deep` because
    `docs/writing-an-operator.md` wants evidence from a real codebase before an
    operator starts costing every user."""
    assert tier_members("deep") == (
        "argument_swap",
        "default_arg",
        "kwarg_drop",
        "statement_deletion",
    )


def test_tier_names_are_reserved_against_a_future_operator():
    """Reserved rather than merely documented. An operator file named `deep`
    would otherwise silently shadow the tier and change what an existing
    command line means."""
    for reserved in RESERVED_SELECTORS:
        with pytest.raises(ValueError, match="reserved"):
            register(type("Clash", (), {"name": reserved}))


def test_an_operator_cannot_declare_a_tier_that_does_not_exist():
    with pytest.raises(ValueError, match="tier"):
        register(type("Clash", (), {"name": "zz_bad_tier", "tier": "medium"}))


# --- the grammar ------------------------------------------------------------


def test_a_bare_list_of_names_is_an_exact_set():
    """The compatibility pin. `--operators comparison_swap,boundary` meant
    exactly these two before tiers existed and must mean exactly these two
    after. Tier names and `+` are syntax layered on top, never underneath."""
    assert names("comparison_swap,boundary") == ["boundary", "comparison_swap"]


def test_a_single_bare_name_is_still_just_that_name():
    assert names("comparison_swap") == ["comparison_swap"]


def test_a_tier_name_expands_to_its_members():
    built_in = tier_members("deep")
    with probe_operator("zz_deep_probe"):
        assert names("deep") == sorted([*built_in, "zz_deep_probe"])


def test_all_is_every_registered_operator():
    with probe_operator("zz_deep_probe"):
        assert names(ALL_TIER) == sorted(i.name for i in describe_operators())


def test_plus_means_the_default_set_plus_this_one():
    """The common triage case: the ordinary run, plus one expensive operator.
    Spelling it out by hand is exactly the friction this removes."""
    with probe_operator("zz_deep_probe"):
        assert names("+zz_deep_probe") == sorted(
            [*tier_members("default"), "zz_deep_probe"]
        )


def test_plus_accepts_a_tier_too():
    with probe_operator("zz_deep_probe"):
        assert names("+deep") == sorted(
            [*tier_members("default"), *tier_members("deep")]
        )


def test_a_bare_base_and_a_plus_compose():
    """With a bare token present, `+` adds to that rather than to `default` --
    otherwise `--operators boundary,+deep` would quietly drag in five
    operators the user did not name."""
    with probe_operator("zz_deep_probe"):
        assert names("boundary,+zz_deep_probe") == ["boundary", "zz_deep_probe"]


def test_whitespace_and_empty_entries_are_tolerated():
    assert names(" boundary , , comparison_swap ") == ["boundary", "comparison_swap"]


def test_an_unknown_name_is_an_error_that_lists_what_is_available():
    """Silently resolving to nothing is the failure mode this replaces: a
    typo used to produce a zero-mutant run that exits 0 and reads as success."""
    with pytest.raises(SelectionError) as caught:
        resolve_operators("compaison_swap")

    message = str(caught.value)
    assert "compaison_swap" in message
    assert "comparison_swap" in message
    assert "moonbuggy operators" in message


def test_a_selection_that_resolves_to_nothing_says_so(monkeypatch):
    """A tier with no members. `deep` was that tier when tiers landed; now
    that it has one, the case is provoked rather than found lying around --
    it must stay a clear error and not a zero-mutant run that looks like a
    clean bill of health."""
    monkeypatch.setattr(operators_pkg, "TIERS", (*TIERS, "zz_empty_tier"), raising=True)
    with pytest.raises(SelectionError, match="zz_empty_tier"):
        resolve_operators("zz_empty_tier")


def test_an_all_plus_syntax_error_is_reported_rather_than_ignored():
    with pytest.raises(SelectionError, match="\\+"):
        resolve_operators("+")


# --- the listing ------------------------------------------------------------


def test_operators_subcommand_lists_every_operator(capsys):
    assert main(["operators"]) == 0

    out = capsys.readouterr().out
    for info in describe_operators():
        assert info.name in out
        assert info.description in out
    assert "default" in out
    assert "deep" in out


def test_operators_subcommand_tells_the_reader_how_to_select(capsys):
    """The listing exists so an agent can enumerate rather than experiment;
    a list of names with no way to act on them only half does that."""
    main(["operators"])

    out = capsys.readouterr().out
    assert "--operators" in out
    assert "+" in out


def test_the_listing_columns_line_up_whatever_a_cost_is_called(capsys):
    """The column widths come from the vocabularies, not from the values that
    happen to be in the registry. `COST` was hardcoded at four characters
    while `low` and `high` were the only costs anyone declared, and the first
    `medium` operator pushed the MUTATES column two places right on its own
    row -- in the one output whose whole purpose is being read by an agent."""
    main(["operators"])

    rows = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line and not line.startswith(" ")
    ]
    header, *entries = rows[: 1 + len(describe_operators())]
    mutates = header.index("MUTATES")

    for entry in entries:
        assert entry[mutates - 1] == " ", entry
        assert entry[mutates] != " ", entry


def test_operators_json_is_a_single_object(capsys):
    """A single object, like summary.json: there is one listing per
    invocation. JSONL is for per-mutant data, of which there is a stream."""
    assert main(["operators", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, dict)
    listed = {entry["name"]: entry for entry in payload["operators"]}
    assert listed.keys() == {info.name for info in describe_operators()}
    for entry in listed.values():
        assert entry["tier"] in TIERS
        assert entry["description"]
        assert entry["cost"]
    assert payload["tiers"]["deep"] == sorted(tier_members("deep"))
    assert payload["tiers"][ALL_TIER] == sorted(listed)


# --- the help surface -------------------------------------------------------


def test_help_mentions_tiers_the_plus_form_and_the_listing_command():
    """#13 made `-h` the onboarding path. An operator set an agent cannot
    enumerate from there is one it will not use."""
    text = _build_parser().format_help()

    assert "moonbuggy operators" in text
    for tier in TIERS:
        assert tier in text
    assert "+" in text
