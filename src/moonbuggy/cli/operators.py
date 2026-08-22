"""The operators subcommand."""

import argparse
import json

from ..operators import (
    ALL_TIER,
    COSTS,
    TIERS,
    describe_operators,
    tier_members,
)


def _operators(args: argparse.Namespace) -> int:
    """Print the operator listing.

    Args:
        args: the parsed `moonbuggy operators` command line.

    Returns:
        0. Nothing here can fail: it reports what is registered.
    """
    infos = describe_operators()
    tiers = {tier: list(tier_members(tier)) for tier in TIERS}
    tiers[ALL_TIER] = list(tier_members(ALL_TIER))

    if args.json:
        # A single object, like summary.json and for the same reason: there is
        # exactly one listing per invocation. JSONL is the shape for per-mutant
        # data, of which there is a stream.
        print(
            json.dumps(
                {
                    "operators": [
                        {
                            "name": info.name,
                            "tier": info.tier,
                            "description": info.description,
                            "cost": info.cost,
                        }
                        for info in infos
                    ],
                    "tiers": tiers,
                },
                sort_keys=True,
            )
        )
        return 0

    name_width = max((len(info.name) for info in infos), default=4)
    tier_width = max(len(tier) for tier in TIERS)
    # Widths come from the vocabularies rather than from the values in hand, so
    # a column cannot silently narrow when no operator happens to claim the
    # longest word. The cost column was hardcoded at 4 while `low` and `high`
    # were the only costs anyone declared, and the first `medium` operator
    # pushed MUTATES two columns right on its own row.
    cost_width = max(len("COST"), *(len(cost) for cost in COSTS))
    header = f"{'NAME':<{name_width}}  {'TIER':<{tier_width}}  "
    print(f"{header}{'COST':<{cost_width}}  MUTATES")
    for info in infos:
        print(
            f"{info.name:<{name_width}}  {info.tier:<{tier_width}}  "
            f"{info.cost:<{cost_width}}  {info.description}"
        )
    print()
    for tier in (*TIERS, ALL_TIER):
        members = tiers[tier]
        count = len(members)
        noun = "operator" if count == 1 else "operators"
        # An empty tier is named rather than hidden. A reader who cannot see
        # that a tier is empty would read `--operators <tier>` failing as a bug
        # rather than as the truth. `deep` was that tier when tiers landed.
        print(f"  {tier}: {count} {noun}" + (" (none yet)" if not members else ""))
    print()
    # The worked example has to name an operator that is *not* in the default
    # tier, or it demonstrates a no-op and teaches that `+` means something it
    # does not. `+boundary` was the example, and boundary is listed as
    # `default` three lines above it.
    print(
        "Select with --operators: a comma-separated list of names is an exact "
        "set,\na tier name stands for its members, and a `+` prefix adds to "
        "the rest of\nthe selection -- to `default` when nothing else is "
        "named, so\n`--operators +statement_deletion` is the default set plus "
        "that one."
    )
    return 0
