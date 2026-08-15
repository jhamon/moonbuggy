"""Module-level configuration.

Fixture module. Carries the suppression case: CACHE_SIZE is a tuning constant
with no observable behaviour, so mutating it produces an equivalent mutant that
no test could ever kill. It is suppressed inline rather than left to survive
and pollute the report.
"""

MAX_RETRIES = 3

CACHE_SIZE = 128  # moonbuggy: skip -- tuning only, no observable behaviour


def should_retry(attempt):
    return attempt < MAX_RETRIES
