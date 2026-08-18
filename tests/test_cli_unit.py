"""Fast unit tests of CLI behaviour that need no subprocess.

`test_cli.py` proves moonbuggy end-to-end by spawning a real pytest
subprocess per mutant, which is why it carries `pytestmark = pytest.mark.slow`
and is deselected by the default gate. The tests here exercise `cli.py`
functions directly, in-process, so they belong outside that mark and run on
every plain `pytest` invocation.
"""

import io

from moonbuggy.cli import _harden_streams, main


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
