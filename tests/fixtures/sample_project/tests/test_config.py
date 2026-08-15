"""Tests for sample.config.

Covers MAX_RETRIES at its boundary so the module-level constant mutant is
killed. Nothing tests CACHE_SIZE -- nothing could, which is why it is suppressed.
"""

from sample.config import should_retry


def test_first_attempt_retries():
    assert should_retry(0) is True


def test_retries_stop_at_limit():
    assert should_retry(3) is False
