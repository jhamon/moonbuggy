"""Fast unit tests of CLI behaviour that need no subprocess.

`test_cli.py` proves moonbuggy end-to-end by spawning a real pytest
subprocess per mutant, which is why it carries `pytestmark = pytest.mark.slow`
and is deselected by the default gate. The tests here exercise `cli.py`
functions directly, in-process, so they belong outside that mark and run on
every plain `pytest` invocation.
"""

import io
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from moonbuggy.cli import (
    _build_parser,
    _clock,
    _display_path,
    _harden_streams,
    _measurable_fd,
    _prepare_cache,
    _settled_line,
    main,
)
from moonbuggy.mutant import Mutant


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


def test_the_clock_reads_as_minutes_and_seconds():
    # M:SS, the shape the spec's progress and milestone lines show.
    assert _clock(7) == "0:07"
    assert _clock(65.9) == "1:05"


def test_the_milestone_line_names_the_counts_and_the_clock():
    # The greppable stand-in for a live region that is not being drawn.
    counts = Counter({"KILLED": 9, "SURVIVED": 2})
    assert (
        _settled_line(11, 22, counts, 5.0)
        == "moonbuggy: 11/22 settled -- 9 killed, 2 survived, 0:05"
    )


def test_the_milestone_line_survives_having_nothing_to_report():
    assert _settled_line(0, 22, Counter(), 0.0) == "moonbuggy: 0/22 settled, 0:00"


def test_a_non_terminal_stream_has_no_measurable_width():
    # None means "no measurement", which resolve_width distinguishes from a
    # measured 80 -- a StringIO has neither a size nor an fd to ask about.
    assert _measurable_fd(io.StringIO()) is None


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


def _cache_key(tmp_path, *argv):
    """The key one mutant gets under `argv`, through the CLI's own wiring."""
    args = _build_parser().parse_args(list(argv))
    cache = _prepare_cache(args, tmp_path)
    assert cache is not None
    mutant = Mutant(
        id="lib.py:2:comparison_swap:0",
        module="lib.py",
        line=2,
        operator="comparison_swap",
        original="return stock > 0",
        mutated="return stock >= 0",
    )
    return cache.key_for(mutant, tmp_path, ("test_lib.py::test_x",))


def test_pytest_args_reach_the_cache_key(tmp_path):
    """The wiring, not the hashing -- `cache.py` proves the digest changes,
    this proves the CLI actually hands it the arguments. Without it, the two
    correct halves can still be connected by nothing."""
    bare = _cache_key(tmp_path, "--project", str(tmp_path))
    doctests = _cache_key(
        tmp_path, "--project", str(tmp_path), "--pytest-arg=--doctest-modules"
    )

    assert bare != doctests


def test_the_same_command_line_produces_the_same_cache_key(tmp_path):
    first = _cache_key(tmp_path, "--project", str(tmp_path), "--pytest-arg=-x")
    second = _cache_key(tmp_path, "--project", str(tmp_path), "--pytest-arg=-x")

    assert first == second


def _tiny_project(root):
    (root / "pytest.ini").write_text("[pytest]\n")
    (root / "lib.py").write_text("def one():\n    return 1\n")
    (root / "test_lib.py").write_text("def test_nothing():\n    assert True\n")
    return root


def test_since_outside_a_git_repository_exits_2(tmp_path, capsys):
    # Criterion H5: the flag needs git history, and saying so beats a
    # traceback out of subprocess.
    _tiny_project(tmp_path)

    code = main(["--project", str(tmp_path), "--since", "main"])

    assert code == 2
    assert "not inside a git repository" in capsys.readouterr().err


def test_since_with_an_unknown_ref_exits_2(tmp_path, capsys):
    _tiny_project(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    code = main(["--project", str(tmp_path), "--since", "no-such-branch"])

    assert code == 2
    err = capsys.readouterr().err
    assert "no-such-branch" in err
    assert "fetch-depth" in err


def test_since_does_not_enter_the_cache_fingerprint(tmp_path):
    # A mutant's verdict cannot depend on how the run reached it, so a
    # diff-scoped run must fill and read the same cache as a full one. If
    # `--since` were folded into the fingerprint, every PR run would start
    # cold and the flag's whole point -- being cheap -- would go with it.
    full = _cache_key(tmp_path, "--project", str(tmp_path))
    scoped = _cache_key(tmp_path, "--project", str(tmp_path), "--since", "origin/main")

    assert full == scoped
