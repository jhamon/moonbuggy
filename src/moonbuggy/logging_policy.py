"""Which mutants sit inside a logging call, and what to do about them.

A mutation inside `logger.debug(f"retrying in {delay * 2}s")` is unkillable by
construction. Nothing asserts on the contents of a debug line, so the mutant
survives every suite that will ever be written against that code, and it
arrives in the survivor list looking exactly like a real finding. In one
reported session two thirds of the survivors in a retry region were arithmetic
inside `logger.debug(...)` arguments -- pure triage tax on the one output the
tool exists to produce.

So they are recognised as a class. Every such mutant is *tagged*
(`Mutant.logging_call`), because a project that does assert on log output has a
real finding here and the tool must not lie about what it generated; and by
default it is also *suppressed*, reported `SKIPPED` and left out of the
survivor list, because that is what almost everyone wants. `--include-logging-
mutants` runs them and leaves the tag in place.

The boundary that defines the whole module: only the *argument expressions* of
the call qualify. In

    if attempts > 5:
        logger.debug("giving up after %d", attempts * 2)

the `>` is a real finding -- a branch nobody checked -- and must survive
untouched, while `attempts * 2` is noise. One is inside the call's arguments
and one is around the call. Nothing else separates them, which is why this is
decided from the mutation site's enclosing-node chain rather than from the line
it sits on.
"""

import ast
from collections.abc import Iterable
from dataclasses import dataclass, field

from .operators import Context

# The receiver names that make an attribute call a logging call. Matched
# against the *last* component of the receiver, so `self.logger`, `cls._log`
# and `mypkg.util.LOGGER` all reduce to a name in this set.
#
# `logging` is here for the module-level shorthand -- `logging.info(...)` is
# the stdlib's own convenience API and every project's first logging call.
DEFAULT_LOGGER_NAMES = frozenset(
    {
        "log",
        "logger",
        "logging",
        "_log",
        "_logger",
        "LOG",
        "LOGGER",
        "_LOG",
        "_LOGGER",
    }
)

# The method names that make the call a logging call. Fixed rather than
# configurable, and the asymmetry with the receiver names is deliberate: a
# project that wraps its logger wraps the *object* (`audit.info(...)`,
# `self.telemetry.warning(...)`) and keeps the level names, because the level
# names come from `logging.Logger` and everything downstream -- handlers,
# `caplog`, log aggregators -- is built on them.
#
# `log` appears both here and in the receiver names, for `logger.log(INFO, ...)`
# and for `self.log.log(...)`.
LEVEL_METHODS = frozenset(
    {
        "debug",
        "info",
        "warning",
        "warn",
        "error",
        "critical",
        "fatal",
        "exception",
        "log",
    }
)

# The fields of an `ast.Call` that hold arguments. `func` is deliberately
# absent: mutating `logger.debug` itself is not a mutation to what gets logged,
# and if some operator ever produces one it is not this module's to suppress.
_ARGUMENT_FIELDS = frozenset({"args", "keywords"})


@dataclass(frozen=True, slots=True)
class LoggingPolicy:
    """What counts as a logging call, and whether its mutants are run.

    Frozen and passed down into generation rather than read from a global, so
    that a caller mutating two projects with different logger names in one
    process gets two answers rather than whichever it configured last.
    """

    #: Receiver names recognised as loggers. Compared against the last
    #: component of the attribute chain.
    logger_names: frozenset[str] = field(default=DEFAULT_LOGGER_NAMES)
    #: Whether a mutant inside a logging call is suppressed -- settled as
    #: `SKIPPED` without being run. Tagging happens either way.
    skip: bool = True


def policy_for(
    extra_names: Iterable[str] = (), *, include_logging_mutants: bool = False
) -> LoggingPolicy:
    """The policy a run's options ask for.

    Args:
        extra_names: additional receiver names to recognise as loggers, for
            projects that wrap the stdlib logger under a name of their own.
            Added to the defaults rather than replacing them: a project with an
            `audit` logger almost certainly still has a `logger` too.
        include_logging_mutants: run them instead of suppressing them. They stay
            tagged either way, so a project that does assert on log output can
            opt in and see the findings.

    Returns:
        The :class:`LoggingPolicy` for the run.
    """
    names = frozenset(DEFAULT_LOGGER_NAMES | {name.strip() for name in extra_names})
    return LoggingPolicy(logger_names=names - {""}, skip=not include_logging_mutants)


def is_logging_call(node: ast.AST, logger_names: Iterable[str]) -> bool:
    """Whether this call looks like a call to a logger.

    A heuristic, and knowingly one: there is no way to know from the AST alone
    that `audit.info(...)` reaches `logging.Logger.info`. It errs towards *not*
    recognising a call, because a missed logging call costs one noisy survivor
    and a wrongly-recognised one hides a real finding.

    Args:
        node: any AST node; only an `ast.Call` can qualify.
        logger_names: the receiver names to accept.

    Returns:
        True if `node` is a call of a level method on something named like a
        logger.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    # Attribute access only. A bare `log(...)` would sweep up `math.log` under
    # `from math import log`, and no amount of naming convention distinguishes
    # the two at this level.
    if not isinstance(func, ast.Attribute) or func.attr not in LEVEL_METHODS:
        return False
    receiver = _receiver_name(func.value)
    return receiver is not None and receiver in logger_names


def in_logging_call(context: Context, policy: LoggingPolicy) -> bool:
    """Whether the node in this context sits inside a logging call's arguments.

    Walks outwards to the *nearest* enclosing call and asks about that one
    only. A mutation inside `logger.debug("%s", compute(n + 1))` is inside
    `compute`, not inside the logging call, and stays a finding: `compute` runs
    for real and a mutation to its argument can raise, be returned, or be
    observed. Only the arguments the logging call itself evaluates are
    unkillable by construction, and the nearest-call rule is exactly the line
    between the two.

    Args:
        context: where the node sits, as generation walked it.
        policy: the run's logging policy.

    Returns:
        True if the nearest enclosing call is a logging call and this node is
        somewhere in its arguments.
    """
    current: Context | None = context
    while current is not None and current.parent is not None:
        if isinstance(current.parent, ast.Call):
            # `current.field` is the field of the call this branch descends
            # through, which is what separates an argument from the callee.
            return current.field in _ARGUMENT_FIELDS and is_logging_call(
                current.parent, policy.logger_names
            )
        current = current.outer
    return False


def _receiver_name(node: ast.AST) -> str | None:
    """The name a logging call is made on, reduced to its last component.

    `logger` -> "logger", `self.logger` -> "logger", `mypkg.util.LOG` -> "LOG".
    The prefix is dropped because it says nothing: the same logger is `logger`
    in a function, `self._logger` on an instance and `_mod.LOGGER` after an
    import, and a set of receiver names that had to enumerate those spellings
    would be unusable as configuration.

    Args:
        node: the `value` of the attribute being called.

    Returns:
        The name, or None if the receiver is not a name or attribute chain --
        `get_logger().info(...)` has no name to match.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None
