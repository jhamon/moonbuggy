"""Fast unit tests of CLI behaviour that need no subprocess.

`test_cli.py` proves moonbuggy end-to-end by spawning a real pytest
subprocess per mutant, which is why it carries `pytestmark = pytest.mark.slow`
and is deselected by the default gate. The tests here exercise `cli.py`
functions directly, in-process, so they belong outside that mark and run on
every plain `pytest` invocation.
"""

import io
from pathlib import Path

import pytest

from moonbuggy.cli import _display_path, _harden_streams, main


def test_harden_streams_makes_encoding_errors_non_fatal(monkeypatch):
    """A source byte stdout cannot encode must degrade, never kill the run."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.setattr("sys.stderr", stream)
    _harden_streams()
    assert stream.errors == "backslashreplace"
    print("café")  # would raise UnicodeEncodeError before hardening


def test_interrupt_returns_130_not_a_traceback(monkeypatch):
    """Ctrl-C is an anticipated ending, so it gets a message and a code."""

    def _boom(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr("moonbuggy.cli._run", _boom)
    assert main([]) == 130


def test_report_flag_rejects_an_unknown_value():
    """argparse's own validation, pinned so a typo fails fast and clearly."""
    with pytest.raises(SystemExit):
        main(["--report", "fancy"])


def test_display_path_renders_the_short_relative_path():
    # The common case, and the one the frozen agent-format summary line
    # depends on: an output-dir under the project renders the same short
    # relative path it always has.
    project_dir = Path("/repo")
    jsonl_path = project_dir / ".moonbuggy" / "results.jsonl"
    assert _display_path(jsonl_path, project_dir) == ".moonbuggy/results.jsonl"


def test_display_path_falls_back_to_the_absolute_path():
    # `--output-dir` may be given as an absolute path. `project_dir /
    # args.output_dir` then silently discards `project_dir` -- an absolute
    # right operand replaces the left one under `/` -- so `jsonl_path` ends up
    # outside `project_dir` and `relative_to` would raise. Criterion H5: an
    # anticipated shape of input must never produce a traceback, so this
    # degrades to the absolute path instead.
    project_dir = Path("/repo")
    jsonl_path = Path("/tmp/elsewhere/results.jsonl")
    assert _display_path(jsonl_path, project_dir) == "/tmp/elsewhere/results.jsonl"
