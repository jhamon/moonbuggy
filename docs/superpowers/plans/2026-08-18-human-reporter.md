# Human Reporter Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second terminal output mode that shows a human the code delta for every surviving mutant, alongside the existing agent format, which does not change.

**Architecture:** Two new modules. `terminal.py` owns everything environment-dependent — display-width measurement, text sanitising, format/colour/width resolution, and the live progress region. `humanreport.py` is pure: functions from a list of `Record` to a string, with width and palette passed in, so every alignment and encoding case is a unit test on a string with no pty involved. `cli.py` picks a renderer and wires the progress region. `report.py` gains two fields on `Record`.

**Tech Stack:** Python 3.12, stdlib only (`unicodedata`, `os`, `shutil` deliberately avoided for width — see Task 4), pytest.

**Spec:** [docs/superpowers/specs/2026-08-18-human-reporter-design.md](../specs/2026-08-18-human-reporter-design.md)

## Global Constraints

Every task's requirements implicitly include this section.

- **No new dependencies.** stdlib only. `pyproject.toml` gains nothing.
- **Python 3.12**, `target-version = "py312"`. Modern syntax (`X | None`, not `Optional[X]`).
- **Line length 88** (ruff). Prose comments wrap nearer 79.
- **mypy strict** over `src/moonbuggy` with nothing carved out. Every new function is fully annotated.
- **100% docstring coverage** on public names (`interrogate`, `fail-under = 100`). Names with a single leading underscore are exempt. Public docstrings must pass `pydoclint --style=google`: an `Args:` section naming every parameter and a `Returns:` section when the function returns something.
- **British spelling in identifiers**, matching the existing `summarise`. Use `sanitise`, `colour`, `normalise`. The *CLI flag* is `--color`, which is the universal spelling users type.
- **The agent format is frozen.** `render_line`, `plaintext_from_records`, `results.txt`, and the stderr summary keep their exact current bytes. Task 11 enforces this with a golden test; do not wait for it to be careful.
- **No meaning may live only in a colour.** Every distinction is carried by plain text first.
- **The report is ASCII.** No box drawing, no symbols, no arrows.
- **Nothing is right-aligned.** Fields that do not fit go on their own line.

Run commands with the project venv: `.venv/bin/python`. Note the `.venv/bin/moonbuggy` console script has a stale shebang and does not run; use `.venv/bin/python -m moonbuggy.cli` if you need the CLI directly.

---

### Task 1: `Record` carries the mutation operands

The human reporter renders from records read back off disk, so that the two artifacts cannot drift (criterion E3). `record_for` currently emits `diff` as a pre-rendered `f"- {original}\n+ {mutated}"` string and does not emit the operands themselves. Without this task the reporter would have to split its own output format back into operands to compute a changed span.

Additive JSON keys break no existing reader.

**Files:**
- Modify: `src/moonbuggy/report.py:36-50` (the `Record` TypedDict), `src/moonbuggy/report.py:52-75` (`record_for`)
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Record` gains `original: str` and `mutated: str`. Every later task reads `record["original"]` and `record["mutated"]` rather than parsing `record["diff"]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_report.py`:

```python
def test_record_carries_the_operands_separately():
    """The human reporter needs the two lines, not a pre-rendered diff string."""
    result = make("SURVIVED")
    record = record_for(result)
    assert record["original"] == "stock > 0"
    assert record["mutated"] == "stock >= 0"


def test_diff_stays_derived_from_the_operands():
    """The diff string must not drift from the fields it is built out of."""
    record = record_for(make("SURVIVED"))
    assert record["diff"] == f"- {record['original']}\n+ {record['mutated']}"
```

Check the existing `make` helper at the top of `tests/test_report.py` for the `original`/`mutated` values it builds its `Mutant` with, and use those literals in the assertions above rather than the ones written here.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_report.py -k operands -v`
Expected: FAIL with `KeyError: 'original'`.

- [ ] **Step 3: Write minimal implementation**

In the `Record` TypedDict, after `module_level: bool` and before `diff: str`:

```python
    original: str
    mutated: str
```

In `record_for`, in the returned dict, immediately before the `"diff"` key:

```python
        # The operands, not just the rendered diff. The human reporter computes
        # a changed span from these; deriving them by splitting `diff` would be
        # a reporter parsing its own output format.
        "original": mutant.original,
        "mutated": mutant.mutated,
```

- [ ] **Step 4: Run the report tests**

Run: `.venv/bin/python -m pytest tests/test_report.py -v`
Expected: PASS, including the pre-existing tests.

- [ ] **Step 5: Run the full suite and the type gate**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m mypy`
Expected: PASS. If a test asserts an exact set of JSONL keys, update it — the new keys are intentional.

- [ ] **Step 6: Commit**

```bash
git add src/moonbuggy/report.py tests/test_report.py
git commit -m "Report: carry the mutation operands on Record"
```

---

### Task 2: Encoding and interrupt hardening

Two pre-existing defects that the human reporter makes reachable on ordinary input. They stand on their own and land first so the rest of the work builds on a process that cannot die from a source file's contents.

`srcio.py` deliberately supports non-UTF-8 source via `tokenize.detect_encoding` and PEP 263, so `mutant.original` can hold characters `sys.stdout` cannot encode. `print` uses `errors="strict"`, so under a `C` locale this raises `UnicodeEncodeError` inside `_run`, past `main`'s four-exception handler, escaping as a traceback and violating the project's own H5 criterion.

**Files:**
- Modify: `src/moonbuggy/cli.py` (add `_harden_streams`, call it in `main`, add the `KeyboardInterrupt` handler, add `encoding=` to the `results.txt` write at `cli.py:281`)
- Modify: `src/moonbuggy/report.py` (add `encoding="utf-8"` to `write_jsonl`, `read_jsonl`, and `StreamingJSONL.__enter__`)
- Test: `tests/test_cli.py`, `tests/test_report.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `_harden_streams() -> None` in `cli.py`, called at the top of `main`. No other task calls it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_report.py`:

```python
def test_jsonl_is_written_as_utf8_regardless_of_locale(tmp_path):
    """results.jsonl must not depend on the machine's locale encoding."""
    path = tmp_path / "results.jsonl"
    write_jsonl([make("SURVIVED", nearest="tests/t.py::test_café")], path)
    # Decodes as UTF-8 and no other way round-trips.
    assert read_jsonl(path)[0]["nearest_test"] == "tests/t.py::test_café"
    path.read_bytes().decode("utf-8")
```

Add to `tests/test_cli.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "harden or interrupt" tests/test_report.py -k utf8 -v`
Expected: FAIL — `ImportError` for `_harden_streams`, and `KeyboardInterrupt` escaping `main`.

- [ ] **Step 3: Harden the streams**

In `src/moonbuggy/cli.py`, above `main`:

```python
def _harden_streams() -> None:
    # A source file may legally be latin-1 or cp1251 (srcio honours PEP 263),
    # so a mutated line can hold characters stdout cannot encode. With the
    # default errors="strict" that is a UnicodeEncodeError raised from inside
    # the report, past main's handler, as a traceback -- which criterion H5
    # forbids -- and past run()'s explicit flush, losing the buffered report.
    # backslashreplace degrades the character and keeps the run.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")
```

`getattr` rather than a direct call because an in-process caller may have replaced the streams with an object that has no `reconfigure`; the docstring on `main` says calling it in-process is supported.

Call it as the first statement of `main`, before the `profiling` line.

- [ ] **Step 4: Handle the interrupt**

In `main`, extend the `try` around the dispatch:

```python
    except KeyboardInterrupt:
        # An anticipated ending, not a crash. 130 is the shell convention for
        # SIGINT. The results file is valid at every instant (criterion
        # M1.4.13), so whatever finished is already usable.
        print(
            "\nmoonbuggy: interrupted. Partial results in "
            f"{args.output_dir}/results.jsonl",
            file=sys.stderr,
        )
        return 130
```

Place it before the existing `except (LayoutError, ...)` clause.

- [ ] **Step 5: Pin the file encodings**

In `src/moonbuggy/report.py`, three call sites:

```python
    with open(path, "w", encoding="utf-8") as handle:      # write_jsonl
    with open(path, encoding="utf-8") as handle:           # read_jsonl
        self._handle = open(self.path, "w", encoding="utf-8")  # StreamingJSONL
```

In `src/moonbuggy/cli.py`, the `results.txt` write:

```python
        text_path.write_text(plaintext_from_records(records) + "\n", encoding="utf-8")
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_cli.py tests/test_report.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full gate**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m mypy && .venv/bin/ruff check . && .venv/bin/ruff format --check .`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/moonbuggy/cli.py src/moonbuggy/report.py tests/test_cli.py tests/test_report.py
git commit -m "CLI: survive unencodable source and Ctrl-C"
```

---

### Task 3: Display width and text sanitising

The foundation everything else measures with. Pure functions, no environment reads.

`len()` is the wrong measure for a terminal: combining marks occupy no cell, CJK and emoji occupy two, and East-Asian *Ambiguous* characters occupy whatever the terminal was configured to give them.

**Files:**
- Create: `src/moonbuggy/terminal.py`
- Test: `tests/test_terminal.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `char_width(char: str, ambiguous_wide: bool = False) -> int`
  - `display_width(text: str, ambiguous_wide: bool = False) -> int`
  - `sanitise(text: str) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_terminal.py`:

```python
"""Terminal measurement, environment resolution, and the live progress region.

These are the parts of the human reporter that depend on the outside world.
Keeping them here, behind pure functions, is what lets humanreport.py be
tested as string-in string-out with no pty.
"""

from moonbuggy.terminal import display_width, sanitise


def test_ascii_is_one_cell_per_character():
    assert display_width("return 0") == 8


def test_east_asian_wide_is_two_cells():
    assert display_width("日本") == 4


def test_combining_marks_occupy_no_cell():
    # "e" plus U+0301 COMBINING ACUTE ACCENT is one cell, not two.
    assert display_width("é") == 1


def test_ambiguous_width_follows_the_locale():
    arrow = "→"  # East Asian Ambiguous
    assert display_width(arrow) == 1
    assert display_width(arrow, ambiguous_wide=True) == 2


def test_tabs_expand_to_eight_column_stops():
    # A raw tab would expand from the terminal's column, not the file's, so
    # the rendered indent would not match the source's.
    assert sanitise("a\tb") == "a       b"


def test_escape_sequences_in_source_are_defanged():
    # A source file holding an ESC would otherwise replay it into the reader's
    # terminal when the report prints the line.
    assert sanitise("x = '\x1b[2J'") == "x = '\\x1b[2J'"


def test_form_feed_is_defanged():
    # Legal Python whitespace, and it appears in real files.
    assert sanitise("a\x0cb") == "a\\x0cb"


def test_lone_surrogates_are_defanged():
    assert sanitise("a\ud800b") == "a\\ud800b"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terminal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'moonbuggy.terminal'`.

- [ ] **Step 3: Write the implementation**

Create `src/moonbuggy/terminal.py`:

```python
"""Everything about the human reporter that depends on the outside world.

Measurement, environment resolution, and the live progress region live here so
that `humanreport` can be a pure function of its arguments. The split is what
makes the report testable: every alignment and truncation case is a string
comparison, with no terminal, no pty, and no environment involved.
"""

import unicodedata

# Tab stops. Eight is what terminals do, and matching them is the whole point:
# a tab expanded at a different interval renders at a different column than the
# source file it came from.
TAB_WIDTH = 8


def char_width(char: str, ambiguous_wide: bool = False) -> int:
    """How many terminal cells one character occupies.

    Args:
        char: a single character.
        ambiguous_wide: whether to treat East Asian Ambiguous characters as two
            cells, which is what a terminal configured for a CJK locale does.

    Returns:
        0, 1, or 2.
    """
    if unicodedata.combining(char) or unicodedata.category(char) in {
        "Mn",
        "Me",
        "Cf",
    }:
        return 0
    kind = unicodedata.east_asian_width(char)
    if kind in {"W", "F"}:
        return 2
    if kind == "A":
        return 2 if ambiguous_wide else 1
    return 1


def display_width(text: str, ambiguous_wide: bool = False) -> int:
    """How many terminal cells a string occupies.

    Undercounts emoji ZWJ sequences, which are several wide code points forming
    one grapheme. That is documented rather than solved, and it is one more
    reason the report never right-aligns anything after source text.

    Args:
        text: the string to measure.
        ambiguous_wide: whether East Asian Ambiguous characters take two cells.

    Returns:
        The total width in cells.
    """
    return sum(char_width(char, ambiguous_wide) for char in text)


def sanitise(text: str) -> str:
    """Make one line of arbitrary source safe to print and to measure.

    Tabs are expanded here rather than left to the terminal, which would expand
    them from its own current column and so render an indent that does not match
    the file. Control characters are escaped because a source file may hold an
    ESC in a string literal, and printing it verbatim would replay whatever it
    encodes -- a screen clear, a title change -- into the reader's terminal.

    Args:
        text: one raw source line.

    Returns:
        The line with tabs expanded and every control character escaped.
    """
    out: list[str] = []
    for char in text.expandtabs(TAB_WIDTH):
        if unicodedata.category(char) == "Cc" or "\ud800" <= char <= "\udfff":
            point = ord(char)
            out.append(f"\\x{point:02x}" if point < 256 else f"\\u{point:04x}")
        else:
            out.append(char)
    return "".join(out)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_terminal.py -v`
Expected: PASS, all eight.

- [ ] **Step 5: Run the gates**

Run: `.venv/bin/python -m mypy && .venv/bin/ruff check . && .venv/bin/python -m interrogate -c pyproject.toml src/moonbuggy`
Expected: PASS. `interrogate` requires the module docstring and all three public docstrings, which are present.

- [ ] **Step 6: Commit**

```bash
git add src/moonbuggy/terminal.py tests/test_terminal.py
git commit -m "Terminal: display width and source-line sanitising"
```

---

### Task 4: Format, colour, and width resolution

Three independent precedence chains, all pure so they can be tested as tables.

The env var sits above TTY detection deliberately: TTY detection fails silently in the direction that matters, because an agent harness that allocates a pty would get the human format and its `grep SURVIVED` would return nothing with no error.

**Files:**
- Modify: `src/moonbuggy/terminal.py`
- Test: `tests/test_terminal.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `resolve_format(flag: str | None, env: Mapping[str, str], isatty: bool) -> str` returning `"human"` or `"agent"`
  - `resolve_colour(flag: str | None, env: Mapping[str, str], isatty: bool) -> int` returning a depth: `0`, `8`, `256`, or `16777216`
  - `resolve_width(flag: int | None, env: Mapping[str, str], fd: int | None) -> int`
  - `Palette` frozen dataclass with fields `dim`, `bold`, `reverse`, `minus`, `plus`, `reset`, all `str`
  - `palette_for(depth: int) -> Palette`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_terminal.py`:

```python
from moonbuggy.terminal import (
    palette_for,
    resolve_colour,
    resolve_format,
    resolve_width,
)


def test_flag_beats_everything_for_format():
    assert resolve_format("agent", {"MOONBUGGY_REPORT": "human"}, True) == "agent"


def test_env_var_beats_tty_detection():
    # The failure this exists for: an agent harness that allocates a pty would
    # otherwise get the human format and grep nothing, with no error.
    assert resolve_format(None, {"MOONBUGGY_REPORT": "agent"}, True) == "agent"


def test_ci_means_agent():
    assert resolve_format(None, {"CI": "true"}, True) == "agent"


def test_tty_selects_human_and_a_pipe_selects_agent():
    assert resolve_format(None, {}, True) == "human"
    assert resolve_format(None, {}, False) == "agent"


def test_no_color_wins_whatever_its_value():
    # no-color.org: presence is the signal, not the value.
    assert resolve_colour(None, {"NO_COLOR": ""}, True) == 0
    assert resolve_colour(None, {"NO_COLOR": "0"}, True) == 0


def test_color_flag_beats_no_color():
    assert resolve_colour("always", {"NO_COLOR": "1"}, False) > 0


def test_clicolor_force_turns_colour_on_off_a_tty():
    assert resolve_colour(None, {"CLICOLOR_FORCE": "1"}, False) > 0
    assert resolve_colour(None, {"CLICOLOR_FORCE": "0"}, False) == 0


def test_force_color_selects_depth():
    assert resolve_colour(None, {"FORCE_COLOR": "1"}, False) == 8
    assert resolve_colour(None, {"FORCE_COLOR": "3"}, False) == 16777216
    assert resolve_colour(None, {"FORCE_COLOR": "0"}, True) == 0


def test_dumb_or_unset_term_gets_no_escapes():
    # TERM unset is not hypothetical; it is unset in ordinary subprocess envs.
    assert resolve_colour(None, {"TERM": "dumb"}, True) == 0
    assert resolve_colour(None, {}, True) == 0


def test_depth_comes_from_term_and_colorterm():
    assert resolve_colour(None, {"TERM": "xterm"}, True) == 8
    assert resolve_colour(None, {"TERM": "xterm-256color"}, True) == 256
    truecolor = {"TERM": "xterm-256color", "COLORTERM": "truecolor"}
    assert resolve_colour(None, truecolor, True) == 16777216


def test_width_prefers_the_flag_then_columns():
    assert resolve_width(50, {"COLUMNS": "120"}, None) == 50
    assert resolve_width(None, {"COLUMNS": "70"}, None) == 70


def test_width_falls_back_to_eighty_when_undetectable():
    # shutil.get_terminal_size silently substitutes 80, leaving no way to tell
    # a measurement from a guess. This reaches the same number honestly.
    assert resolve_width(None, {}, None) == 80


def test_width_is_capped_at_a_hundred():
    # A 300-column diff line requires head-turning.
    assert resolve_width(None, {"COLUMNS": "300"}, None) == 100


def test_nonsense_columns_is_ignored():
    assert resolve_width(None, {"COLUMNS": "wide"}, None) == 80
    assert resolve_width(None, {"COLUMNS": "-5"}, None) == 80


def test_palette_is_empty_without_colour():
    plain = palette_for(0)
    assert plain.dim == "" and plain.bold == "" and plain.reset == ""


def test_palette_avoids_red_and_green():
    # Red/green is the worst pair for the most common colour vision deficiency
    # and a pair of mid-luminance colours that also fails in greyscale.
    eight = palette_for(8)
    assert "31m" not in eight.minus and "32m" not in eight.plus
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terminal.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_format'`.

- [ ] **Step 3: Write the implementation**

Append to `src/moonbuggy/terminal.py`. Add `import os` and `from collections.abc import Mapping` and `from dataclasses import dataclass` to the imports.

```python
# Colour depths, as the number of colours available. 0 means no escape
# sequences at all -- not merely no colour, but no bold and no reverse either,
# which is what TERM=dumb and a pipe both mean.
NO_COLOUR = 0
ANSI_8 = 8
ANSI_256 = 256
TRUECOLOR = 16777216

# The widest the report ever draws. Nothing here needs more, and a 300-column
# diff line requires head-turning.
MAX_WIDTH = 100
FALLBACK_WIDTH = 80


def resolve_format(flag: str | None, env: Mapping[str, str], isatty: bool) -> str:
    """Choose the human or the agent report.

    The environment variable sits above TTY detection on purpose. TTY detection
    fails silently in the direction that matters: an agent harness that
    allocates a pty gets the human format and its `grep SURVIVED` returns
    nothing, with no error to notice. A harness pins the variable once.

    Args:
        flag: the value of `--report`, or None.
        env: the process environment.
        isatty: whether the report's stream is a terminal.

    Returns:
        Either "human" or "agent".
    """
    if flag is not None:
        return flag
    from_env = env.get("MOONBUGGY_REPORT")
    if from_env in {"human", "agent"}:
        return from_env
    if env.get("CI"):
        return "agent"
    return "human" if isatty else "agent"


def resolve_colour(flag: str | None, env: Mapping[str, str], isatty: bool) -> int:
    """Decide whether to emit escape sequences, and how many colours to use.

    Colour is a separate decision from the report format: a human redirecting
    the report to a file still wants the human format, and still wants no
    escapes in it.

    Args:
        flag: the value of `--color` -- "always", "never", "auto", or None.
        env: the process environment.
        isatty: whether the report's stream is a terminal.

    Returns:
        A colour depth: 0, 8, 256, or 16777216.
    """
    if flag == "never":
        return NO_COLOUR
    if flag == "always":
        return _depth(env)
    if flag is None or flag == "auto":
        # Presence is the signal, whatever the value (no-color.org).
        if "NO_COLOR" in env:
            return NO_COLOUR
        forced = env.get("CLICOLOR_FORCE")
        if forced is not None:
            return NO_COLOUR if forced == "0" else _depth(env)
        force_colour = env.get("FORCE_COLOR")
        if force_colour is not None:
            if force_colour == "0":
                return NO_COLOUR
            return {"2": ANSI_256, "3": TRUECOLOR}.get(force_colour, ANSI_8)
        if env.get("CLICOLOR") == "0":
            return NO_COLOUR
        if env.get("TERM", "dumb") == "dumb":
            return NO_COLOUR
        return _depth(env) if isatty else NO_COLOUR
    return NO_COLOUR


def _depth(env: Mapping[str, str]) -> int:
    if env.get("COLORTERM") in {"truecolor", "24bit"}:
        return TRUECOLOR
    if "256color" in env.get("TERM", ""):
        return ANSI_256
    return ANSI_8


def resolve_width(flag: int | None, env: Mapping[str, str], fd: int | None) -> int:
    """How many columns the report may use.

    `shutil.get_terminal_size` is deliberately not used: it silently
    substitutes 80 on failure, so a caller cannot tell a measurement from a
    guess, and the two need different behaviour -- a guessed width must not be
    used for anything that would look broken if it were wrong.

    Args:
        flag: the value of `--width`, or None.
        env: the process environment.
        fd: the file descriptor the report is going to, or None if there is not
            one to measure.

    Returns:
        A column count between 1 and MAX_WIDTH.
    """
    detected: int | None = None
    if flag is not None and flag > 0:
        detected = flag
    else:
        # COLUMNS is rarely exported, so its presence is a deliberate override.
        from_env = env.get("COLUMNS", "")
        if from_env.isdigit() and int(from_env) > 0:
            detected = int(from_env)
        elif fd is not None:
            try:
                detected = os.get_terminal_size(fd).columns
            except OSError:
                detected = None
    return min(detected or FALLBACK_WIDTH, MAX_WIDTH)


@dataclass(frozen=True)
class Palette:
    """The escape sequences the report may use, empty when it may not.

    Every field defaults to the empty string, so `Palette()` is the no-escapes
    palette and rendering code needs no conditionals: it always concatenates.
    """

    dim: str = ""
    bold: str = ""
    reverse: str = ""
    minus: str = ""
    plus: str = ""
    reset: str = ""


def palette_for(depth: int) -> Palette:
    """The palette for a colour depth.

    Not red and green. That pair is both the worst for the most common colour
    vision deficiency and a pair of mid-luminance colours, so it also fails a
    greyscale test. Cyan and amber are legible on light and dark backgrounds
    alike, which is why they are used at every depth.

    Args:
        depth: a value from `resolve_colour`.

    Returns:
        A Palette; every field is empty when depth is 0.
    """
    if depth == NO_COLOUR:
        return Palette()
    if depth == TRUECOLOR:
        minus, plus = "\x1b[38;2;86;180;233m", "\x1b[38;2;230;159;0m"
    elif depth == ANSI_256:
        minus, plus = "\x1b[38;5;39m", "\x1b[38;5;214m"
    else:
        minus, plus = "\x1b[36m", "\x1b[33m"
    return Palette(
        dim="\x1b[2m",
        bold="\x1b[1m",
        reverse="\x1b[7m",
        minus=minus,
        plus=plus,
        reset="\x1b[0m",
    )
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_terminal.py -v`
Expected: PASS.

- [ ] **Step 5: Run the gates**

Run: `.venv/bin/python -m mypy && .venv/bin/ruff check . && .venv/bin/python -m interrogate -c pyproject.toml src/moonbuggy`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/moonbuggy/terminal.py tests/test_terminal.py
git commit -m "Terminal: resolve report format, colour depth, and width"
```

---

### Task 5: The changed span and its caret ruler

The mechanism that makes the delta visible without colour. Pure string functions.

**Files:**
- Create: `src/moonbuggy/humanreport.py`
- Test: `tests/test_humanreport.py`

**Interfaces:**
- Consumes: `display_width` from `terminal.py`.
- Produces:
  - `changed_span(original: str, mutated: str) -> tuple[int, int]` — half-open character indices into `mutated`
  - `ruler(mutated: str, start: int, end: int, indent: int) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_humanreport.py`:

```python
"""The human report: pure rendering, no terminal involved.

Every function here is string-in string-out with width and palette passed in,
which is what lets the alignment, truncation, and encoding cases be ordinary
unit tests.
"""

from moonbuggy.humanreport import changed_span, ruler


def test_span_snaps_outward_to_the_whole_token():
    # Raw common-prefix/suffix finds only the "=", because the ">" is common to
    # both. A caret under the "=" alone understates the change.
    original = "return stock > 0 and not discontinued"
    mutated = "return stock >= 0 and not discontinued"
    start, end = changed_span(original, mutated)
    assert mutated[start:end] == ">="


def test_span_covers_a_whole_augmented_assignment():
    start, end = changed_span("n -= 1", "n += 1")
    assert "n += 1"[start:end] == "+="


def test_span_finds_a_changed_literal():
    start, end = changed_span("return 0", "return 1")
    assert "return 1"[start:end] == "1"


def test_span_survives_an_overlapping_prefix_and_suffix():
    # "x = 11" against "x = 1": prefix 5 and suffix 1 sum past the shorter
    # length. Without clamping this is a negative-length span.
    start, end = changed_span("x = 11", "x = 1")
    assert 0 <= start <= end <= len("x = 1")


def test_span_is_never_empty():
    start, end = changed_span("a", "a")
    assert end >= start


def test_ruler_sits_under_the_span():
    mutated = "return stock >= 0 and not discontinued"
    start, end = changed_span("return stock > 0 and not discontinued", mutated)
    line = f"    + {mutated}"
    assert ruler(mutated, start, end, 6) == " " * line.index(">=") + "^^"


def test_ruler_counts_cells_not_characters():
    # Two double-width characters before the span push the carets four cells.
    mutated = "日本 = 1"
    start, end = changed_span("日本 = 0", mutated)
    assert ruler(mutated, start, end, 0) == " " * 7 + "^"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_humanreport.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'moonbuggy.humanreport'`.

- [ ] **Step 3: Write the implementation**

Create `src/moonbuggy/humanreport.py`:

```python
"""The human report: one grep-proof punch list of what survived, with diffs.

The agent format is one line per mutant and deliberately omits the diff, which
makes a survivor line say where but never what. This module says what.

Everything here is pure. Width and palette arrive as arguments, no function
reads the environment or writes to a stream, and the input is the same `Record`
that was written to `results.jsonl` -- so the human view cannot drift from the
canonical one, for the same reason the plaintext view cannot.
"""

from .terminal import display_width


def changed_span(original: str, mutated: str) -> tuple[int, int]:
    """Which part of `mutated` differs from `original`.

    Found by common prefix and suffix, then widened to whole tokens. The
    widening matters: comparing `stock > 0` with `stock >= 0` finds only the
    `=`, because the `>` is common to both, and a caret under a lone `=`
    understates what changed. Snapping outward to the surrounding run of
    non-space characters recovers `>=`.

    Args:
        original: the source line before mutation.
        mutated: the source line after mutation.

    Returns:
        Half-open ``(start, end)`` character indices into `mutated`.
    """
    shorter = min(len(original), len(mutated))
    prefix = 0
    while prefix < shorter and original[prefix] == mutated[prefix]:
        prefix += 1
    # Clamped so the two spans cannot overlap. "x = 11" against "x = 1" has a
    # prefix of 5 and a suffix of 1 against a shorter length of 5, which
    # unclamped yields a negative-length span.
    suffix = 0
    while (
        suffix < shorter - prefix
        and original[len(original) - 1 - suffix] == mutated[len(mutated) - 1 - suffix]
    ):
        suffix += 1

    start, end = prefix, len(mutated) - suffix
    while start > 0 and not mutated[start - 1].isspace():
        start -= 1
    while end < len(mutated) and not mutated[end].isspace():
        end += 1
    # A boundary may have landed between a base character and its combining
    # mark, which would render the highlight starting on an orphaned diacritic.
    while end < len(mutated) and display_width(mutated[end]) == 0:
        end += 1
    return start, max(start, end)


def ruler(mutated: str, start: int, end: int, indent: int) -> str:
    """A line of carets sitting under the changed span.

    This is the only mechanism in the report that shows the changed span with
    no escape sequences at all, which is what makes the delta survive NO_COLOR,
    a pipe, `less` without `-R`, and a reader with a colour vision deficiency.

    Args:
        mutated: the line the carets go under.
        start: the span's first character index.
        end: the span's end index, exclusive.
        indent: how many cells the line is indented by, including its sigil.

    Returns:
        A string of spaces then carets, with no trailing whitespace.
    """
    lead = indent + display_width(mutated[:start])
    span = max(1, display_width(mutated[start:end]))
    return " " * lead + "^" * span
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_humanreport.py -v`
Expected: PASS, all seven.

- [ ] **Step 5: Run the gates**

Run: `.venv/bin/python -m mypy && .venv/bin/ruff check . && .venv/bin/python -m interrogate -c pyproject.toml src/moonbuggy`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/moonbuggy/humanreport.py tests/test_humanreport.py
git commit -m "Human report: changed-span detection and the caret ruler"
```

---

### Task 6: Rendering one location group

A group is every mutant sharing a `file:line`. The `-` line belongs to the location and prints once; each mutant adds a status line, a `+` line, and a ruler.

Grouping by location rather than by file is what makes `nearest_test` print once. That is structural, not cosmetic: `runner.py:227` computes it as `sorted(selected)[0]` over the line-to-test map, so every mutant on a line has the same value by construction.

**Files:**
- Modify: `src/moonbuggy/humanreport.py`
- Test: `tests/test_humanreport.py`

**Interfaces:**
- Consumes: `changed_span`, `ruler`, `sanitise`, `Palette`.
- Produces:
  - `coverage_sentence(record: Record) -> list[str]`
  - `render_group(records: Sequence[Record], palette: Palette) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_humanreport.py`:

```python
from moonbuggy.humanreport import coverage_sentence, render_group
from moonbuggy.terminal import Palette

PLAIN = Palette()


def rec(**over):
    """A Record with sane defaults, overridden per test."""
    base = {
        "id": "sample/inventory.py:9:comparison_swap:0",
        "status": "SURVIVED",
        "file": "sample/inventory.py",
        "line": 9,
        "operator": "comparison_swap",
        "category": "comparison_swap",
        "nearest_test": "tests/test_inventory.py::test_discontinued",
        "tests_run": 2,
        "duration": 0.1,
        "module_level": False,
        "suppressed": False,
        "original": "return stock > 0 and not discontinued",
        "mutated": "return stock >= 0 and not discontinued",
        "diff": "",
    }
    base.update(over)
    return base


def test_group_prints_the_original_line_once():
    lines = render_group([rec(), rec(operator="constant_int",
                                     mutated="return stock > 1 and not discontinued")],
                         PLAIN)
    assert sum(1 for line in lines if line.lstrip().startswith("- ")) == 1
    assert sum(1 for line in lines if line.lstrip().startswith("+ ")) == 2


def test_group_header_is_a_clickable_path_and_line():
    # Contiguous path:line at column 0 is what terminals and $EDITOR +N act on.
    assert render_group([rec()], PLAIN)[0] == "sample/inventory.py:9"


def test_status_is_a_word_not_a_symbol():
    # The five keywords are the vocabulary of results.txt and of every grep a
    # user writes; a parallel set of glyphs would not transfer.
    assert "  SURVIVED  comparison_swap" in render_group([rec()], PLAIN)


def test_timeout_says_how_long_it_waited():
    lines = render_group([rec(status="TIMEOUT", nearest_test=None)], PLAIN)
    assert "  TIMEOUT  comparison_swap  (timed out after 30s)" in lines


def test_coverage_sentence_pluralises():
    assert coverage_sentence(rec(tests_run=1))[0].startswith("1 test runs")
    assert coverage_sentence(rec(tests_run=2))[0].startswith("2 tests run")


def test_an_unexercised_line_says_so_rather_than_naming_a_test():
    # tests_run=0 is a different finding: the action is write a test or delete
    # the code, and there is no nearest test to read.
    assert coverage_sentence(rec(tests_run=0, nearest_test=None)) == [
        "no test runs this line at all"
    ]


def test_a_module_level_mutant_explains_its_widened_selection():
    sentence = coverage_sentence(rec(module_level=True, tests_run=14))
    assert sentence == ["runs at import time; every test in the suite ran"]


def test_the_node_id_gets_its_own_line_and_is_never_truncated():
    long_id = "tests/t.py::TestClass::test_thing[a-very-long-parametrised-id]"
    lines = coverage_sentence(rec(nearest_test=long_id))
    assert long_id in lines


def test_only_survivors_get_a_coverage_sentence():
    assert coverage_sentence(rec(status="TIMEOUT", nearest_test=None)) == []


def test_a_whitespace_only_mutation_says_so():
    lines = render_group([rec(original="x = 1", mutated="x = 1 ")], PLAIN)
    assert "    (differs only in trailing whitespace)" in lines
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_humanreport.py -k "group or coverage or whitespace" -v`
Expected: FAIL with `ImportError: cannot import name 'coverage_sentence'`.

- [ ] **Step 3: Write the implementation**

Add to `src/moonbuggy/humanreport.py`. Extend the imports with
`from collections.abc import Sequence`, `from .report import Record`, and
`from .terminal import Palette, display_width, sanitise`.

```python
# Indents. Two levels, not four: the location anchors at column 0, the status
# word sits under it, and the code sits under that. Deeper nesting spends
# columns to express a hierarchy that is only three deep.
STATUS_INDENT = 2
CODE_INDENT = 4
# Where a diff line's source text begins, counting the indent and the "- ".
SIGIL_WIDTH = CODE_INDENT + 2


def coverage_sentence(record: Record) -> list[str]:
    """Why nothing caught this mutant, in words.

    `tests_run` routes between two different jobs and so is not decoration.
    Zero means no test executes the line at all, and the action is to write one
    or delete the code -- there is no nearest test to read. A large number means
    the line is exercised and nothing asserts on the result, and the action is
    to strengthen an assertion.

    Args:
        record: one mutant's record.

    Returns:
        Zero, one, or two lines. Empty for anything but a survivor, because a
        timeout is a fact about the run rather than a gap in the tests.
    """
    if record["status"] != "SURVIVED":
        return []
    if record["module_level"]:
        # Selection widens to the whole suite for these, so the line-to-test
        # map attributes them to no single test and `nearest_test` is not
        # merely absent but inapplicable.
        return ["runs at import time; every test in the suite ran"]
    count = record["tests_run"]
    if count == 0:
        return ["no test runs this line at all"]
    noun = "test" if count == 1 else "tests"
    verb = "runs" if count == 1 else "run"
    lines = [f"{count} {noun} {verb} this line; first is"]
    if record["nearest_test"]:
        # Its own line, never truncated: a node id is a paste target, and the
        # head carries the path while the tail disambiguates, so neither end is
        # safe to cut. The terminal may soft-wrap it.
        lines.append(record["nearest_test"])
    return lines


def render_group(records: Sequence[Record], palette: Palette) -> list[str]:
    """Every mutant sharing one file and line, as report lines.

    The `-` line is a property of the location rather than of a mutant, so it
    prints once however many mutants the line carries. So does the coverage
    sentence: `nearest_test` is computed per line, so rendering it per mutant
    would always duplicate it.

    Args:
        records: mutants sharing a file and line, in the order to print them.
        palette: the escape sequences to use, possibly empty.

    Returns:
        The group's lines, without a trailing blank.
    """
    first = records[0]
    lines = [f"{first['file']}:{first['line']}"]
    original = sanitise(first["original"]).rstrip()
    shown_original = False
    for record in records:
        mutated = sanitise(record["mutated"]).rstrip()
        note = ""
        if record["status"] == "TIMEOUT":
            note = "  (timed out after 30s)"
        lines.append(
            f"{' ' * STATUS_INDENT}{record['status']}  {record['operator']}{note}"
        )
        if not shown_original:
            lines.append(
                f"{' ' * CODE_INDENT}{palette.dim}{palette.minus}- "
                f"{original}{palette.reset}"
            )
            shown_original = True
        if mutated == original:
            # Two identical-looking lines would read as a rendering bug.
            lines.append(f"{' ' * CODE_INDENT}(differs only in trailing whitespace)")
            continue
        lines.append(
            f"{' ' * CODE_INDENT}{palette.bold}{palette.plus}+ "
            f"{mutated}{palette.reset}"
        )
        start, end = changed_span(original, mutated)
        lines.append(ruler(mutated, start, end, SIGIL_WIDTH))
    lines.extend(f"{' ' * STATUS_INDENT}{line}" for line in coverage_sentence(first))
    return lines
```

Note `display_width` is imported for Task 8's use; if ruff flags it as unused now, add it in Task 8 instead.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_humanreport.py -v`
Expected: PASS.

- [ ] **Step 5: Run the gates**

Run: `.venv/bin/python -m mypy && .venv/bin/ruff check . && .venv/bin/python -m interrogate -c pyproject.toml src/moonbuggy`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/moonbuggy/humanreport.py tests/test_humanreport.py
git commit -m "Human report: render one file:line group"
```

---

### Task 7: The whole report

Header, survivor groups, the problems section, and the footer.

**Files:**
- Modify: `src/moonbuggy/humanreport.py`
- Test: `tests/test_humanreport.py`

**Interfaces:**
- Consumes: `render_group`, `summarise` from `report.py`.
- Produces:
  - `score_text(counts: Mapping[str, int]) -> str`
  - `render_report(records: Sequence[Record], *, palette: Palette, files: int, elapsed: float) -> str`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_humanreport.py`:

```python
from moonbuggy.humanreport import render_report, score_text


def test_score_shows_its_denominator():
    counts = {"KILLED": 15, "SURVIVED": 5, "TIMEOUT": 1, "SKIPPED": 1,
              "SUSPICIOUS": 0}
    assert score_text(counts) == "15/21 killed, 71%"


def test_score_is_not_a_number_when_everything_was_skipped():
    counts = {"KILLED": 0, "SURVIVED": 0, "TIMEOUT": 0, "SKIPPED": 3,
              "SUSPICIOUS": 0}
    assert score_text(counts) == "n/a"


def test_the_last_line_states_the_exit_code():
    # The reader's terminal comes to rest on the final line, and anyone wiring
    # this into a pre-commit hook needs the list connected to the red.
    report = render_report([rec()], palette=PLAIN, files=1, elapsed=9.4)
    assert report.splitlines()[-1] == "exit 1 -- survivors"


def test_a_clean_run_says_so():
    report = render_report([rec(status="KILLED")], palette=PLAIN, files=1,
                           elapsed=1.0)
    assert report.splitlines()[-1] == "exit 0 -- nothing survived"


def test_killed_mutants_appear_only_as_counts():
    report = render_report([rec(status="KILLED")], palette=PLAIN, files=1,
                           elapsed=1.0)
    assert "KILLED  comparison_swap" not in report
    assert "1 killed" in report


def test_timeouts_move_below_the_survivors():
    records = [rec(), rec(status="TIMEOUT", file="sample/loops.py", line=12,
                          nearest_test=None)]
    report = render_report(records, palette=PLAIN, files=2, elapsed=1.0)
    assert report.index("Problems with the run") > report.index("SURVIVED")


def test_suspicious_collapses_to_one_line():
    # humanize in the project's own OSS data is 84 SUSPICIOUS against 16
    # SURVIVED. Rendered in full the finding drowns in the plumbing.
    records = [rec(status="SUSPICIOUS", nearest_test=None) for _ in range(84)]
    report = render_report(records, palette=PLAIN, files=1, elapsed=1.0)
    assert "84 mutants could not be answered confidently (SUSPICIOUS)." in report
    assert report.count("SUSPICIOUS  comparison_swap") == 0


def test_groups_are_ordered_by_file_then_line():
    records = [
        rec(file="b.py", line=1),
        rec(file="a.py", line=9),
        rec(file="a.py", line=2),
    ]
    report = render_report(records, palette=PLAIN, files=2, elapsed=1.0)
    assert report.index("a.py:2") < report.index("a.py:9") < report.index("b.py:1")


def test_no_line_exceeds_the_report_width():
    report = render_report([rec()], palette=PLAIN, files=1, elapsed=9.4)
    # The node id is the one deliberate exception; it is a paste target.
    body = [line for line in report.splitlines() if "::" not in line]
    assert max(len(line) for line in body) <= 100
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_humanreport.py -k "score or report or exit or collapse" -v`
Expected: FAIL with `ImportError: cannot import name 'render_report'`.

- [ ] **Step 3: Write the implementation**

Add to `src/moonbuggy/humanreport.py`, extending the imports with
`from collections.abc import Mapping` and `from .report import summarise`.

```python
# The order counts appear in the footer. Survivors first because they are the
# work; killed and skipped last because they are context.
FOOTER_ORDER = ("SURVIVED", "TIMEOUT", "SUSPICIOUS", "KILLED", "SKIPPED")


def score_text(counts: Mapping[str, int]) -> str:
    """The kill rate, with the denominator visible.

    Suppressed mutants leave the denominator, because a mutant nobody could
    kill is not a test failure. The number appears in the footer rather than
    the header: at the top of a report a percentage reads as a target, and at
    the bottom it reads as an observation.

    Args:
        counts: per-status counts, as `report.summarise` returns.

    Returns:
        Something like "15/21 killed, 71%", or "n/a" when nothing was runnable.
    """
    total = sum(counts.values())
    runnable = total - counts.get("SKIPPED", 0)
    if runnable <= 0:
        return "n/a"
    killed = counts.get("KILLED", 0)
    return f"{killed}/{runnable} killed, {round(100 * killed / runnable)}%"


def render_report(
    records: Sequence[Record],
    *,
    palette: Palette,
    files: int,
    elapsed: float,
) -> str:
    """The whole human report.

    Args:
        records: every mutant's record, in any order.
        palette: the escape sequences to use, possibly empty.
        files: how many source files were mutated.
        elapsed: the run's wall clock, in seconds.

    Returns:
        The report, newline-separated, with no trailing newline.
    """
    counts = summarise(records)
    lines = [f"moonbuggy  {len(records)} mutants across {files} files", ""]

    survivors = [r for r in records if r["status"] == "SURVIVED"]
    timeouts = [r for r in records if r["status"] == "TIMEOUT"]
    suspicious = [r for r in records if r["status"] == "SUSPICIOUS"]

    for group in _group_by_location(survivors):
        lines.extend(render_group(group, palette))
        lines.append("")

    if timeouts or suspicious:
        lines.extend(["Problems with the run", ""])
        for group in _group_by_location(timeouts):
            lines.extend(render_group(group, palette))
            lines.append("")
        if suspicious:
            # One cause, not N findings. The documented action -- investigate
            # the run -- is the same however many mutants are affected.
            lines.append(
                f"{len(suspicious)} mutants could not be answered confidently "
                "(SUSPICIOUS)."
            )
            lines.append(
                f"This is usually one cause rather than {len(suspicious)}.  "
                "See docs/troubleshooting.md"
            )
            lines.append("")

    tally = ", ".join(
        f"{counts[status]} {status.lower()}"
        for status in FOOTER_ORDER
        if counts.get(status) or status in {"SURVIVED", "KILLED"}
    )
    lines.append(f"{tally} in {elapsed:.1f}s -- {score_text(counts)}")
    lines.append("Full records: .moonbuggy/results.jsonl")
    lines.append(
        "exit 1 -- survivors" if counts["SURVIVED"] else "exit 0 -- nothing survived"
    )
    return "\n".join(lines)


def _group_by_location(records: Sequence[Record]) -> list[list[Record]]:
    grouped: dict[tuple[str, int], list[Record]] = {}
    for record in records:
        grouped.setdefault((record["file"], record["line"]), []).append(record)
    return [grouped[key] for key in sorted(grouped)]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_humanreport.py -v`
Expected: PASS.

- [ ] **Step 5: Check the rendering against the spec by eye**

Run:

```bash
.venv/bin/python -c "
import json
from moonbuggy.humanreport import render_report
from moonbuggy.terminal import Palette
recs=[json.loads(l) for l in open('.moonbuggy/results.jsonl')]
print(render_report(recs, palette=Palette(), files=5, elapsed=9.4))
"
```

Expected: output matching the mockup in the spec's "Report layout" section. Generate `.moonbuggy/results.jsonl` first with
`PYTHONPATH=src .venv/bin/python -m moonbuggy.cli --project tests/fixtures/sample_project`
run from a scratch directory, or point the snippet at wherever you wrote it.

- [ ] **Step 6: Run the gates**

Run: `.venv/bin/python -m mypy && .venv/bin/ruff check . && .venv/bin/python -m interrogate -c pyproject.toml src/moonbuggy`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/moonbuggy/humanreport.py tests/test_humanreport.py
git commit -m "Human report: header, problems section, and footer"
```

---

### Task 8: Long lines and narrow terminals

Minified code, long string literals, and deeply indented statements all overflow. Wrapping is not the answer: a continuation line carries no `-`/`+` sigil, so it becomes ambiguous which side it belongs to.

**Files:**
- Modify: `src/moonbuggy/humanreport.py`
- Test: `tests/test_humanreport.py`

**Interfaces:**
- Consumes: `display_width`, `changed_span`.
- Produces: `window(text: str, start: int, end: int, budget: int) -> tuple[str, int, int]`. `render_group` and `render_report` gain a keyword-only `width: int` parameter, defaulting to `terminal.FALLBACK_WIDTH`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_humanreport.py`:

```python
from moonbuggy.humanreport import window


def test_a_short_line_is_returned_unchanged():
    assert window("return 0", 7, 8, 74) == ("return 0", 7, 8)


def test_a_long_line_is_windowed_around_the_change():
    # Never tail-truncated: the change may be at column 300.
    text = "x = " + "a" * 300 + " + CHANGED"
    start = text.index("CHANGED")
    got, new_start, new_end = window(text, start, start + 7, 40)
    assert "CHANGED" in got
    assert got.startswith("...")
    assert got[new_start:new_end] == "CHANGED"


def test_the_window_never_exceeds_its_budget():
    text = "y = " + "b" * 500
    got, _, _ = window(text, 100, 104, 40)
    assert len(got) <= 40


def test_deep_indentation_is_dedented():
    deep = " " * 20 + "return stock > 0"
    mutated = " " * 20 + "return stock >= 0"
    lines = render_group([rec(original=deep, mutated=mutated)], PLAIN, width=80)
    assert "    - return stock > 0" in lines


def test_a_narrow_terminal_still_renders():
    report = render_report([rec()], palette=PLAIN, files=1, elapsed=1.0, width=40)
    body = [line for line in report.splitlines() if "::" not in line]
    assert max(len(line) for line in body) <= 40
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_humanreport.py -k "window or narrow or dedent" -v`
Expected: FAIL with `ImportError: cannot import name 'window'`.

- [ ] **Step 3: Write `window` and thread `width` through**

Add to `src/moonbuggy/humanreport.py`:

```python
ELLIPSIS = "..."


def window(text: str, start: int, end: int, budget: int) -> tuple[str, int, int]:
    """Cut a long line down to a budget, keeping the changed span visible.

    Tail truncation is the specific wrong answer here, because the change may
    be at column 300. Wrapping is worse: a continuation line carries no `-` or
    `+` sigil, so it stops being clear which side of the diff it belongs to.

    Args:
        text: the line to fit.
        start: the changed span's first character index.
        end: the changed span's end index, exclusive.
        budget: how many cells are available.

    Returns:
        ``(windowed_text, start, end)`` with the indices rebased onto the
        returned text.
    """
    if display_width(text) <= budget:
        return text, start, end
    room = budget - 2 * len(ELLIPSIS)
    span = max(1, end - start)
    lead = max(0, (room - span) // 2)
    left = max(0, start - lead)
    right = min(len(text), left + room)
    left = max(0, right - room)
    cut = text[left:right]
    prefix = ELLIPSIS if left > 0 else ""
    suffix = ELLIPSIS if right < len(text) else ""
    shift = len(prefix) - left
    return prefix + cut + suffix, start + shift, min(end + shift, len(cut) + len(prefix))
```

In `render_group`, add a keyword-only `width: int = FALLBACK_WIDTH` parameter (import `FALLBACK_WIDTH` from `.terminal`), document it in the docstring's `Args:`, and:

- Before rendering, dedent: compute the shared leading whitespace of `original` and `mutated`, and when it exceeds `TAB_WIDTH` cells, strip that many characters from both. The group header already carries the location.
- Pass each line through `window(..., budget=width - SIGIL_WIDTH)` and use the returned indices for the ruler.

In `render_report`, add the same keyword-only `width: int = FALLBACK_WIDTH`, document it, and forward it to `render_group`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_humanreport.py -v`
Expected: PASS. The Task 7 tests still pass because `width` defaults.

- [ ] **Step 5: Run the gates**

Run: `.venv/bin/python -m mypy && .venv/bin/ruff check . && .venv/bin/python -m interrogate -c pyproject.toml src/moonbuggy && .venv/bin/pydoclint --style=google --config=pyproject.toml src/moonbuggy`
Expected: PASS. `pydoclint` will fail if the new `width` parameter is missing from either `Args:` section.

- [ ] **Step 6: Commit**

```bash
git add src/moonbuggy/humanreport.py tests/test_humanreport.py
git commit -m "Human report: window long lines and fit narrow terminals"
```

---

### Task 9: The live progress region

One physical row on stderr. No spinner, no bar, no cursor hiding.

The cursor is never hidden because `run()` uses `os._exit`, which skips `atexit` by design, so no exit path could reliably restore it — a Ctrl-C would leave the user's shell with an invisible cursor until `reset`.

**Files:**
- Modify: `src/moonbuggy/terminal.py`
- Test: `tests/test_terminal.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `LiveRegion` with

```python
def __init__(self, stream: IO[str], *, enabled: bool, clock: Callable[[], float]) -> None
def tick(self, text: str) -> None
def log(self, text: str) -> None
def close(self, final: str | None = None) -> None
```

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_terminal.py`:

```python
import io
import itertools

from moonbuggy.terminal import LiveRegion


def region(enabled=True, times=None):
    """A LiveRegion over a buffer, with a clock that never blocks the test."""
    clock = itertools.count(0, 10).__next__ if times is None else iter(times).__next__
    stream = io.StringIO()
    return stream, LiveRegion(stream, enabled=enabled, clock=clock)


def test_the_region_erases_before_each_repaint():
    stream, live = region()
    live.tick("15/22")
    live.tick("16/22")
    # \r plus a full erase, not padding with spaces, so a shrinking line
    # cannot leave a tail behind.
    assert stream.getvalue().count("\r\x1b[2K") == 2


def test_the_region_never_hides_the_cursor():
    # os._exit skips atexit, so nothing could reliably restore it after Ctrl-C.
    stream, live = region()
    live.tick("15/22")
    live.close("done")
    assert "\x1b[?25l" not in stream.getvalue()


def test_identical_frames_are_not_rewritten():
    stream, live = region()
    live.tick("15/22")
    live.tick("15/22")
    assert stream.getvalue().count("15/22") == 1


def test_repaints_are_rate_limited():
    # Two ticks 0.01s apart; only the first is drawn.
    stream, live = region(times=[0.0, 0.01, 0.02])
    live.tick("a")
    live.tick("b")
    assert "b" not in stream.getvalue()


def test_log_interleaves_without_corrupting_the_live_line():
    stream, live = region()
    live.tick("15/22")
    live.log("moonbuggy: skipping broken.py")
    written = stream.getvalue()
    # The message is committed with a newline, and the live line comes back.
    assert "moonbuggy: skipping broken.py\n" in written
    assert written.rstrip().endswith("15/22")


def test_close_commits_exactly_one_durable_line():
    stream, live = region()
    live.tick("15/22")
    live.close("moonbuggy: 22/22 settled")
    assert stream.getvalue().endswith("moonbuggy: 22/22 settled\n")


def test_a_disabled_region_emits_no_escapes_at_all():
    # Non-TTY, TERM=dumb, CI, or --no-progress.
    stream, live = region(enabled=False)
    live.tick("15/22")
    live.log("a message")
    live.close("final")
    written = stream.getvalue()
    assert "\x1b" not in written and "\r" not in written
    # log and close still commit; only the animation is suppressed.
    assert written == "a message\nfinal\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terminal.py -k region -v`
Expected: FAIL with `ImportError: cannot import name 'LiveRegion'`.

- [ ] **Step 3: Write the implementation**

Append to `src/moonbuggy/terminal.py`, extending the imports with
`from collections.abc import Callable` and `from typing import IO`.

```python
# Erase the whole line and return to column 0. Padding with spaces instead
# would leave a tail behind whenever the new frame is shorter than the old.
ERASE = "\r\x1b[2K"
# Ten frames a second. Faster buys nothing a reader can see, and every frame is
# a write that a terminal recording or a CI log capture keeps forever.
MIN_INTERVAL = 0.1


class LiveRegion:
    """A single-row progress line that other output can be written around.

    One physical row, deliberately. A multi-row region has to move the cursor
    up to repaint, and the number of physical rows a logical line occupies
    changes when the terminal is resized -- at which point the cursor
    arithmetic is wrong and the corruption is unrecoverable.

    The cursor is never hidden. `run()` exits through `os._exit`, which skips
    `atexit` by design, so there is no exit path that could reliably show it
    again; a Ctrl-C would leave the user's shell with an invisible cursor until
    they ran `reset`. Parking the cursor at column 0 costs nothing.

    While the region is open it is the only thing writing to its stream. Callers
    route their own messages through `log` so that each one erases the live line
    first and redraws it afterwards.
    """

    def __init__(
        self,
        stream: IO[str],
        *,
        enabled: bool,
        clock: Callable[[], float],
    ) -> None:
        self.stream = stream
        self.enabled = enabled
        self.clock = clock
        self._current = ""
        self._last_paint = float("-inf")

    def tick(self, text: str) -> None:
        """Repaint the live line, if anything has changed and it is time to.

        Args:
            text: the whole line to show, already fitted to the width.
        """
        if not self.enabled or text == self._current:
            return
        now = self.clock()
        if now - self._last_paint < MIN_INTERVAL:
            return
        self._last_paint = now
        self._current = text
        self.stream.write(ERASE + text)
        self.stream.flush()

    def log(self, text: str) -> None:
        """Commit a line of scrolling output above the live line.

        Args:
            text: the message, without a trailing newline.
        """
        if self.enabled:
            self.stream.write(ERASE)
        self.stream.write(text + "\n")
        if self.enabled and self._current:
            self.stream.write(self._current)
        self.stream.flush()

    def close(self, final: str | None = None) -> None:
        """Stop redrawing and leave one durable line in the scrollback.

        Args:
            final: the line to leave behind, or None to leave nothing.
        """
        if self.enabled:
            self.stream.write(ERASE)
        self._current = ""
        self.enabled = False
        if final is not None:
            self.stream.write(final + "\n")
        self.stream.flush()
```

Keeping the rendered line strictly shorter than the terminal width is the caller's job, done when it composes `text`. A line exactly as wide as the terminal leaves the cursor position ambiguous, because a terminal with auto-wrap writes the last cell and defers the wrap.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_terminal.py -v`
Expected: PASS.

- [ ] **Step 5: Run the gates**

Run: `.venv/bin/python -m mypy && .venv/bin/ruff check . && .venv/bin/python -m interrogate -c pyproject.toml src/moonbuggy && .venv/bin/pydoclint --style=google --config=pyproject.toml src/moonbuggy`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/moonbuggy/terminal.py tests/test_terminal.py
git commit -m "Terminal: single-row live progress region"
```

---

### Task 10: Wire it into the CLI

**Files:**
- Modify: `src/moonbuggy/cli.py` — `_add_run_arguments`, `_run`, `_collect_mutants`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 3, 4, 7, 8, 9.
- Produces: the flags `--report`, `--color`, `--width`, `--no-progress`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_human_report_renders_on_a_non_tty(tmp_path, capsys):
    """A human redirecting to a file still wants the human format.

    Without this, `moonbuggy | less` silently gets the agent format -- which is
    the trap TTY detection alone walks into.
    """
    project = "tests/fixtures/sample_project"
    code = main(["--project", project, "--output-dir",
                 str(tmp_path), "--report", "human"])
    out = capsys.readouterr().out
    assert code == 1
    assert "SURVIVED  comparison_swap" in out
    assert "\x1b" not in out  # no escapes on a non-tty


def test_agent_report_is_the_default_off_a_tty(tmp_path, capsys):
    main(["--project", "tests/fixtures/sample_project",
          "--output-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "nearest_test=" in out  # the key=value agent format


def test_report_flag_rejects_an_unknown_value(tmp_path):
    with pytest.raises(SystemExit):
        main(["--report", "fancy"])
```

Mark the first two `@pytest.mark.slow` if they take more than a second locally; check how the existing end-to-end tests in `tests/test_cli.py` are marked and follow that.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k report -v`
Expected: FAIL — `--report` is not a recognised argument.

- [ ] **Step 3: Add the flags**

In `_add_run_arguments`:

```python
    parser.add_argument(
        "--report",
        choices=["human", "agent"],
        default=None,
        help="output format: 'human' for a readable report with diffs, "
        "'agent' for one grep-friendly line per mutant "
        "(default: human at a terminal, agent when piped; "
        "MOONBUGGY_REPORT overrides)",
    )
    parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default=None,
        help="colour in the human report (default: auto; NO_COLOR is honoured)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="wrap the human report to this many columns (default: detected)",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="do not draw the live progress line",
    )
```

- [ ] **Step 4: Resolve the environment once, at the top of `_run`**

```python
    fmt = resolve_format(args.report, os.environ, sys.stdout.isatty())
    palette = palette_for(
        resolve_colour(args.color, os.environ, sys.stdout.isatty())
    )
    width = resolve_width(
        args.width, os.environ, sys.stdout.fileno() if sys.stdout.isatty() else None
    )
    # Progress is a separate decision from format: the report is the payload
    # and goes to stdout, progress is ephemeral and goes to stderr, so a human
    # redirecting the report still sees the run move.
    progress = LiveRegion(
        sys.stderr,
        enabled=(
            not args.no_progress
            and sys.stderr.isatty()
            and os.environ.get("TERM", "dumb") != "dumb"
            and not os.environ.get("CI")
        ),
        clock=time.perf_counter,
    )
```

`sys.stdout.fileno()` can raise `io.UnsupportedOperation` when stdout has been replaced in-process; wrap it in a `try`/`except (OSError, ValueError, io.UnsupportedOperation)` and pass `None`.

- [ ] **Step 5: Route the in-run messages through the region**

Every `print(..., file=sys.stderr)` between the region opening and closing becomes `progress.log(...)`. That is the two preamble lines in `_run` and the two messages in `_collect_mutants`. Pass `progress` into `_collect_mutants` as a parameter rather than reaching for a global.

This is the single-writer invariant: while the region is open, exactly one object writes to stderr.

- [ ] **Step 6: Tick on each result and close before the report**

Wrap the existing `on_result=stream.write` callbacks so they also tick:

```python
        counts_so_far: Counter[str] = Counter()

        def _settled(result: Result) -> None:
            stream.write(result)
            counts_so_far[result.status] += 1
            done = sum(counts_so_far.values())
            line = (
                f"moonbuggy  {done}/{len(mutants)}  "
                + "  ".join(
                    f"{status.lower()} {counts_so_far[status]}"
                    for status in ("KILLED", "SURVIVED", "TIMEOUT")
                    if counts_so_far[status]
                )
            )
            progress.tick(line[: width - 1])
            if result.status == "SURVIVED":
                # Survivors are rare and are the whole point, so they scroll
                # into the scrollback as they land. Killed mutants never do.
                progress.log(f"SURVIVED  {result.mutant.module}:{result.mutant.line}")
```

Pass `_settled` as `on_result` to both `run_mutants` and `run_session`. The clamp to `width - 1` matters: a line exactly as wide as the terminal leaves the cursor position ambiguous under auto-wrap.

Close the region before printing the report, inside a `finally` so an exception cannot leave it open:

```python
        progress.close()
```

Because `run()` exits via `os._exit`, which skips `atexit`, this `finally` inside `_run` is the only teardown that runs.

- [ ] **Step 7: Print the chosen report**

Replace the `if not args.quiet:` block that prints `render_line` per record:

```python
        if fmt == "human":
            if not args.quiet:
                print(
                    render_report(
                        records,
                        palette=palette,
                        files=len(source_files),
                        elapsed=time.perf_counter() - started,
                        width=width,
                    )
                )
        elif not args.quiet:
            for record in records:
                print(render_line(record))
```

Add `started = time.perf_counter()` near the top of `_run`.

The existing stderr summary line stays exactly as it is in agent mode. In human mode it is redundant with the footer, so skip it — but keep it whenever `fmt == "agent"`, byte for byte, because Task 11's golden test pins it.

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 9: Look at it yourself**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m moonbuggy.cli --project tests/fixtures/sample_project --report human
```

Expected: the report from the spec's mockup. Compare it against the spec section by eye — group order, the caret columns, the problems section, the three footer lines.

- [ ] **Step 10: Run the full gate**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m mypy && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m interrogate -c pyproject.toml src/moonbuggy`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/moonbuggy/cli.py tests/test_cli.py
git commit -m "CLI: select the human or agent report and drive progress"
```

---

### Task 11: Freeze the agent format, and document the mode

The agent format being unchanged is the constraint the whole feature rests on. Enforce it rather than trusting care.

**Files:**
- Create: `tests/test_agent_format_frozen.py`
- Modify: `README.md` (the Options block and the "Reading the output" section), `docs/reading-the-output.md`
- Test: the new file

**Interfaces:**
- Consumes: everything.
- Produces: nothing further.

- [ ] **Step 1: Write the golden test**

Create `tests/test_agent_format_frozen.py`:

```python
"""The agent format is a contract, pinned byte for byte.

Section 5.1's premise is that the reader is an agent grepping output. Every
line begins with one of five keywords and carries key=value tokens, so
`grep SURVIVED` works with no knowledge of the schema. The human reporter must
not have moved any of it. This test exists because "we were careful" is not a
mechanism.
"""

from moonbuggy.report import plaintext_from_records, render_line

GOLDEN = (
    "SURVIVED  sample/inventory.py:9 comparison_swap line=9 "
    "nearest_test=tests/test_inventory.py::test_discontinued tests_run=2 "
    "id=sample/inventory.py:9:comparison_swap:0"
)

RECORD = {
    "id": "sample/inventory.py:9:comparison_swap:0",
    "status": "SURVIVED",
    "file": "sample/inventory.py",
    "line": 9,
    "operator": "comparison_swap",
    "category": "comparison_swap",
    "nearest_test": "tests/test_inventory.py::test_discontinued",
    "tests_run": 2,
    "duration": 0.1,
    "module_level": False,
    "suppressed": False,
    "original": "return stock > 0 and not discontinued",
    "mutated": "return stock >= 0 and not discontinued",
    "diff": "- return stock > 0\n+ return stock >= 0",
}


def test_the_agent_line_is_unchanged():
    assert render_line(RECORD) == GOLDEN


def test_the_line_still_starts_with_a_bare_grep_keyword():
    assert render_line(RECORD).split()[0] == "SURVIVED"


def test_the_plaintext_view_is_one_line_per_record():
    text = plaintext_from_records([RECORD, RECORD])
    assert len(text.splitlines()) == 2
    assert "\n" not in render_line(RECORD)


def test_the_new_operand_fields_do_not_leak_into_the_line():
    # They exist for the human reporter. The agent line stays as it was.
    assert "original=" not in render_line(RECORD)
    assert "return stock" not in render_line(RECORD)
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_agent_format_frozen.py -v`
Expected: PASS immediately. It is a regression guard, not a driver — if it fails, an earlier task changed something it should not have.

- [ ] **Step 3: Update the README options block**

In `README.md`, in the fenced options list, after `--quiet`:

```
--report MODE        'human' for a readable report with diffs, 'agent' for
                     one grep-friendly line per mutant (default: human at a
                     terminal, agent when piped)
--color WHEN         auto, always, or never (default: auto)
--width N            wrap the human report to N columns
--no-progress        do not draw the live progress line
```

- [ ] **Step 4: Add a README paragraph under "Reading the output"**

After the sentence ending "Lines are one per mutant and never contain the diff, so they stay grep- and awk-friendly":

```markdown
That is the format you get when output is piped or redirected. At a terminal
you get a human report instead: survivors grouped by file and line, each with
the code delta and a caret under exactly what changed.

```bash
moonbuggy --report human
```

Set `MOONBUGGY_REPORT=agent` to pin the grep-friendly format everywhere,
including at a terminal — worth doing in an agent harness that allocates a pty,
where terminal detection would otherwise pick the human report.
```

- [ ] **Step 5: Update `docs/reading-the-output.md`**

Read the file first and add a section describing the human report, in the voice of the surrounding prose. Include the spec's mockup, and state which parts are stable to script against (nothing — the agent format is the contract) and which are not.

- [ ] **Step 6: Build the docs**

Run: `.venv/bin/python -m sphinx -b html -W --keep-going docs docs/_build/html`
Expected: PASS. `-W` turns warnings into errors, so a broken cross-reference fails here.

- [ ] **Step 7: Run the whole gate**

Run: `make test && make lint && make format-check && make typecheck && make docstring-coverage`
Expected: PASS.

- [ ] **Step 8: Run the correctness gate**

Run: `make check-oracle`
Expected: PASS. This is the one that proves the reporter changes did not disturb what moonbuggy actually reports.

- [ ] **Step 9: Commit**

```bash
git add tests/test_agent_format_frozen.py README.md docs/reading-the-output.md
git commit -m "Freeze the agent format and document the human report"
```

---

## Self-review notes

Checked against the spec section by section.

**Covered:** the operand fields (Task 1); encoding and interrupt hardening (Task 2); display width, sanitising, tabs, control characters, surrogates (Task 3); format, colour, and width precedence, the palette, no red/green (Task 4); the changed span with its clamp, token snap, and combining-mark extension, and the caret ruler (Task 5); location grouping, status words, the coverage sentence including the `tests_run=0` and module-level cases, whitespace-only mutations (Task 6); the header, the problems section, the SUSPICIOUS collapse, the footer, the score and its `n/a` case, the exit-code line (Task 7); dedenting, windowing, narrow terminals (Task 8); the live region, one row, no cursor hiding, rate limiting, identical-frame skipping, `log` interleaving, the disabled path (Task 9); flags, resolution, single-writer routing, survivor streaming, report selection (Task 10); the frozen agent format and the docs (Task 11).

**Two spec points deliberately deferred to the tasks that own them rather than given their own:** milestone progress lines for the disabled path are folded into Task 10's `progress.log` calls, since a disabled `LiveRegion` already commits `log` output as plain lines; and `--quiet` in human mode is handled by Task 10 Step 7, which skips the report body.

**One spec point not implemented, on purpose:** the spec mentions `AMBIGUOUS_WIDE` being driven from `LANG`/`LC_ALL` with a `MOONBUGGY_AMBIGUOUS_WIDTH` override. `char_width` takes the flag as a parameter (Task 3) but nothing sets it from the environment, because the report is ASCII and the only strings whose ambiguous-width characters could matter are source lines, which are never followed by anything aligned. If a user reports mis-drawn rulers on CJK source, wiring the parameter to the locale is a two-line change at that point.
