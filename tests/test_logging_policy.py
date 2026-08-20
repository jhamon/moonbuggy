"""The logging-call mutation policy (issue #8).

A mutation inside `logger.debug(...)` is unkillable by construction, so it is
tagged and -- by default -- suppressed. The test that matters most in this file
is `test_a_condition_around_a_log_call_is_still_a_finding`: the whole policy is
worthless if it also swallows the `if` that guards the log line.
"""

from moonbuggy.generate import generate_mutants
from moonbuggy.logging_policy import policy_for

GUARDED_LOG = """\
import logging

logger = logging.getLogger(__name__)


def retry(attempts, delay):
    if attempts > 5:
        logger.debug("giving up after %d", attempts * 2)
        return None
    return delay * 2
"""


def _by_line(mutants):
    result = {}
    for mutant in mutants:
        result.setdefault(mutant.line, []).append(mutant)
    return result


def test_a_condition_around_a_log_call_is_still_a_finding():
    """The boundary the issue is explicit about.

    `attempts > 5` guards the log call; it is not inside it. Mutating it
    changes which branch runs, which is exactly the finding a mutation tester
    exists to produce, and no logging policy may sweep it up.
    """
    mutants = _by_line(generate_mutants(GUARDED_LOG, module="m.py"))

    guard = mutants[7]
    assert [m.operator for m in guard] == [
        "comparison_swap",
        "condition_negation",
        "constant_int",
    ]
    assert not any(m.logging_call for m in guard)
    assert not any(m.suppressed for m in guard)


def test_a_mutation_inside_the_log_arguments_is_tagged_and_skipped():
    mutants = _by_line(generate_mutants(GUARDED_LOG, module="m.py"))

    inside = mutants[8]
    assert inside, "the arithmetic inside the log call should still be generated"
    assert all(m.logging_call for m in inside)
    assert all(m.suppressed for m in inside)


def test_an_identical_expression_outside_a_log_call_is_untouched():
    """`delay * 2` on line 10 is the same expression as the one in the log
    call, and the only thing separating them is where they sit."""
    mutants = _by_line(generate_mutants(GUARDED_LOG, module="m.py"))

    outside = mutants[10]
    assert [m.operator for m in outside] == ["arithmetic_swap", "constant_int"]
    assert not any(m.logging_call for m in outside)
    assert not any(m.suppressed for m in outside)


def test_including_logging_mutants_keeps_the_tag_and_drops_the_suppression():
    mutants = _by_line(
        generate_mutants(
            GUARDED_LOG,
            module="m.py",
            logging_policy=policy_for(include_logging_mutants=True),
        )
    )

    inside = mutants[8]
    assert all(m.logging_call for m in inside)
    assert not any(m.suppressed for m in inside)


def test_the_policy_does_not_change_which_mutants_exist():
    """Tagging is not filtering. Ids are cache keys, so the set of mutants --
    and their occurrence indices -- must be identical either way."""
    default = generate_mutants(GUARDED_LOG, module="m.py")
    included = generate_mutants(
        GUARDED_LOG,
        module="m.py",
        logging_policy=policy_for(include_logging_mutants=True),
    )

    assert [m.id for m in default] == [m.id for m in included]


def test_the_call_being_mutated_is_the_nearest_one():
    """A mutation inside a real call that happens to be an argument to a log
    call is not a logging mutant: `compute` runs, and its argument matters."""
    source = "def f(n, logger):\n    logger.info('%s', compute(n + 1))\n"

    mutants = generate_mutants(source, module="m.py")

    assert [m.operator for m in mutants] == ["arithmetic_swap", "constant_int"]
    assert not any(m.logging_call for m in mutants)


def test_the_callee_itself_is_not_inside_the_call():
    """`logger.debug` occupies the call's `func` field, not its arguments."""
    source = "def f(logger, flag):\n    logger.debug('x') if flag else None\n"

    mutants = generate_mutants(source, module="m.py")

    # The conditional expression's test is around the call, not inside it.
    assert [m.operator for m in mutants] == ["condition_negation"]
    assert not any(m.logging_call for m in mutants)


def test_a_wrapped_logger_is_recognised_when_configured():
    source = "def f(n):\n    audit.info('%s', n + 1)\n"

    default = generate_mutants(source, module="m.py")
    configured = generate_mutants(
        source, module="m.py", logging_policy=policy_for(["audit"])
    )

    assert not any(m.logging_call for m in default)
    assert all(m.logging_call for m in configured)


def test_an_attribute_logger_matches_on_its_last_component():
    source = "class C:\n    def f(self, n):\n        self._logger.warning(n + 1)\n"

    mutants = generate_mutants(source, module="m.py")

    assert mutants and all(m.logging_call for m in mutants)


def test_a_same_named_method_on_a_non_logger_is_not_a_logging_call():
    """`math.log` shares a name with `Logger.log`; the receiver is what
    separates them."""
    source = "import math\n\n\ndef f(n):\n    return math.log(n + 1)\n"

    mutants = generate_mutants(source, module="m.py")

    assert mutants and not any(m.logging_call for m in mutants)


def test_keyword_arguments_count_as_arguments():
    source = "def f(logger, n):\n    logger.error('x', extra={'n': n + 1})\n"

    mutants = generate_mutants(source, module="m.py")

    assert mutants and all(m.logging_call for m in mutants)
