"""Tests for `moonbuggy run <id> --trace-json`: the verdict-trace audit log.

Fast unit tests for the flag's grammar and the one-flag-one-output rule. The
end-to-end behaviour (a real project, a real run) lives in `test_cli.py` under
`pytest.mark.slow`.
"""

import pytest

from moonbuggy.cli import _build_parser


def test_run_accepts_trace_json():
    args = _build_parser().parse_args(["run", "a:1:op:0", "--trace-json"])

    assert args.command == "run-one"
    assert args.trace_json is True


def test_run_defaults_to_off():
    args = _build_parser().parse_args(["run", "a:1:op:0"])

    assert args.trace_json is False


def test_why_does_not_offer_trace_json():
    # `why` measures nothing, so it has no verdict to trace evidence for.
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["why", "a:1:op:0", "--trace-json"])
