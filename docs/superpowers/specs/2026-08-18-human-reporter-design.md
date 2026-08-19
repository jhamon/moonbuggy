# Human reporter mode

## The problem

moonbuggy has one output format, built for agents that grep. Every line starts
with a fixed status keyword and carries `key=value` tokens:

```
SURVIVED  sample/inventory.py:9 comparison_swap line=9 nearest_test=tests/test_inventory.py::test_discontinued_item_is_not_available tests_run=2 id=sample/inventory.py:9:comparison_swap:0
```

That format is good at what it is for and does not change.

For a human it omits the one thing that matters: a survivor line says *where*
but never *what*. The reader learns that something at line 9 changed and no test
noticed, then has to run `moonbuggy show <id>` per mutant to find out what the
change was. With forty survivors that is forty context switches.

The delta is already in the canonical record:

```json
"diff": "- return stock > 0 and not discontinued\n+ return stock >= 0 and not discontinued"
```

Nothing new has to be computed. This is a rendering feature plus the small
number of correctness fixes that rendering source text to a terminal forces.

## Decisions

Settled with the maintainer before design, and again after three UX reviews:

- Live progress during the run and a rich report at the end.
- Survivors shown in full; `KILLED` and `SKIPPED` collapse to counts. `TIMEOUT`
  and `SUSPICIOUS` move below the survivors, and `SUSPICIOUS` collapses to one
  line -- a narrowing of the original decision, argued for below.
- Activation by TTY detection, overridable by an explicit flag.
- The delta renders as a stacked `-`/`+` pair with a caret ruler under the
  changed span.
- The score appears in the footer with its denominator, never in the header.
- Scope is the reporter. Workflow features that the reviews argued for --
  equivalent-mutant dismissal, previous-run deltas, triage memory, `--since`,
  `--id` -- are out, each deserving its own spec.

## Constraints

1. `results.jsonl` and `results.txt` keep their current content and format. Only
   what is printed to the terminal changes. A golden-output test enforces the
   agent path byte for byte rather than leaving it to care.
2. `moonbuggy | grep SURVIVED` keeps working.
3. No new dependencies, and no measurable time added to a run.
4. The reporter never fights the runner for the terminal.
5. Everything degrades to plain text losslessly. No meaning may live only in a
   colour.

Constraint 5 is why the caret ruler exists. Highlighting the changed span with
reverse video alone would lose it under `NO_COLOR`, in a pipe, in `less` without
`-R`, and for a reader with a colour vision deficiency.

## Report layout

Groups are keyed by `file:line`, printed at column 0 so the location stays a
contiguous `path:line` token that terminals and `$EDITOR +N` can act on. The `-`
line is a property of the location and prints once per group. Each mutant
contributes a status line, a `+` line, and a ruler.

The mockup below is the real output of `tests/fixtures/sample_project`,
generated from its `results.jsonl` rather than hand-drawn, so the counts and
every caret column are accurate. Its widest line is 72 columns.

```
moonbuggy  22 mutants across 5 files

sample/inventory.py:9
  SURVIVED  comparison_swap
    - return stock > 0 and not discontinued
    + return stock >= 0 and not discontinued
                   ^^
  SURVIVED  constant_int
    + return stock > 1 and not discontinued
                     ^
  2 tests run this line; first is
  tests/test_inventory.py::test_discontinued_item_is_not_available

sample/inventory.py:13
  SURVIVED  comparison_swap
    - if stock < target:
    + if stock <= target:
               ^^
  1 test runs this line; first is
  tests/test_inventory.py::test_restock_fills_to_target

sample/inventory.py:15
  SURVIVED  constant_int
    - return 0
    + return 1
             ^
  no test runs this line at all

sample/loops.py:10
  SURVIVED  comparison_swap
    - while n > 0:
    + while n >= 0:
              ^^
  2 tests run this line; first is
  tests/test_loops.py::test_countdown_of_zero_is_zero

Problems with the run

sample/loops.py:12
  TIMEOUT  arithmetic_swap  (timed out after 30s)
    - n -= 1
    + n += 1
        ^^

5 survived, 1 timeout, 15 killed, 1 skipped in 9.4s -- 15/21 killed, 71%
Full records: .moonbuggy/results.jsonl
exit 1 -- survivors
```

Grouping by location rather than by file does four things at once: the source
line prints once, the path stays clickable, adjacent survivors that one test
could kill together appear together, and `nearest_test` stops being duplicated.
That last one is structural. `runner.py` computes it as `sorted(selected)[0]`
over the line-to-test map, so every mutant on a line has the same value by
construction; rendering it per mutant renders a per-line property in the wrong
place.

### What each status does

`SURVIVED` is the body of the report, grouped as above. `KILLED` and `SKIPPED`
appear only as footer counts.

`TIMEOUT` moves below the survivors under a `Problems with the run` heading.
The documentation treats a timeout as killed-ish, so rendering it inline with
survivors would tell the reader it is work when it mostly is not.

`SUSPICIOUS` collapses to a single line under the same heading:

```
Problems with the run

84 mutants could not be answered confidently (SUSPICIOUS).
This is usually one cause rather than 84.  See docs/troubleshooting.md
```

This is a departure from the original "survivors and problems in full"
decision, made because the project's own OSS data has humanize at 84
SUSPICIOUS against 16 SURVIVED and sqlparse at 37 against 29. Rendered in
full, the finding drowns in the plumbing. A suspicious result is a fact about
the run, not a mutant to go fix, and its documented action -- investigate the
run -- is the same one action however many mutants are affected.

### The footer

`5 survived, 1 timeout, 15 killed, 1 skipped in 9.4s -- 15/21 killed, 71%`

Counts first, score last, denominator visible. A number at the top of a report
reads as a target; the same number at the bottom reads as an observation. It is
never coloured and never drawn as a bar. When every mutant was skipped the
denominator is zero and the score renders as `n/a` rather than raising.

The footer is last because on any report longer than a screen the reader's
terminal comes to rest on the final line, and because `moonbuggy | head` should
still show something useful.

The very last line states the exit code and its reason -- `exit 1 -- survivors`,
or `exit 0 -- nothing survived`. Users wiring this into a pre-commit hook need
the link between the list on screen and the red they just got.

### Status words, not symbols

The five status keywords are the vocabulary of `results.txt`, the documentation,
and every grep a user will write. The human report reuses them rather than
introducing a parallel set of glyphs.

This also settles a rendering problem. Almost nothing outside ASCII has a
determinate width: `unicodedata.east_asian_width` reports most symbol glyphs as
Ambiguous, meaning the terminal decides, and several terminals have a
user-visible toggle for it. `U+23F1` has emoji presentation and renders at two
cells in modern terminals. `U+2298` is missing from many monospace fonts and
falls back to a proportional face with a non-cell advance width, which no amount
of code can compensate for. Since the layout is whitespace alignment, there is
no glyph budget. The report is ASCII.

### Colour

Colour is redundant reinforcement of information the plain text already carries.

- The `-` and `+` sigils carry side. Never omitted, never colour-only.
- Non-hue emphasis: `-` dim (SGR 2), `+` bold (SGR 1).
- Hue, decorative: cyan for `-`, amber for `+`. Not red/green, which is both the
  worst pair for the most common colour vision deficiency and a pair of
  mid-luminance colours that also fails in greyscale.
- The changed span in the `+` line gets reverse video (SGR 7), which survives
  every palette and both background polarities. The caret ruler prints whether
  or not escapes are available, so the span is never colour-only.

Depth: truecolor when `COLORTERM` is `truecolor` or `24bit`, 256 when `TERM`
contains `256color`, otherwise 8. Under `TERM=dumb` or a non-TTY, no escape
sequences of any kind -- not colour, not bold, not reverse.

### Nothing is right-aligned

Right-aligned trailing text pins the layout to one terminal width, produces a
ragged left edge when the left-hand string varies (`line 9` and `line 13` would
start in different columns), forces a braille display user to pan across the
line to reach it, and shears whenever the text before it contains a character
whose width the terminal disagrees about. Fields go on their own line instead.

## Activation

Format resolution, first match wins:

1. `--report=human|agent`
2. `MOONBUGGY_REPORT=human|agent`
3. `CI` set in the environment -- agent
4. `sys.stdout.isatty()` -- human when true, agent when false

The env var sits above TTY detection because TTY detection has a silent failure
mode in the direction that matters: an agent harness that allocates a pty gets
the human format and its `grep SURVIVED` returns nothing, with no error. A
harness or CI config pins the var once.

Colour is a separate decision with its own precedence:

1. `--color=always|never|auto`
2. `NO_COLOR` present at all, whatever its value -- never
3. `CLICOLOR_FORCE` set and not `0` -- always
4. `FORCE_COLOR`: `0` is never, otherwise always (`1`, `2`, `3` select depth)
5. `CLICOLOR=0` -- never
6. `TERM` unset or `dumb` -- never
7. `stdout.isatty()`

`TERM` unset is not hypothetical; it is unset in some ordinary subprocess
environments.

Stream assignment: the report goes to stdout, because it is the payload and must
survive redirection. Progress goes to stderr, because it is ephemeral and must
not pollute a redirected report. The two TTY tests are independent -- stdout's
selects the format, stderr's selects the progress display.

`--report=human` forced on a non-TTY must render: no redraw, no colour, width
80. Without that, `moonbuggy | less` silently gets the agent format, which is
exactly the trap TTY detection alone walks into.

`--quiet` in human mode means the footer only.

## Progress

```
moonbuggy  15/22  killed 10  survived 4  timeout 1  0:07
```

One physical row, on stderr. The live region opens after any preamble has
scrolled past, so no cursor-up sequence is ever needed -- multi-row live regions
become unrecoverable when a resize changes how many physical rows a logical line
occupies.

Repaint is driven by result arrival. `run_mutants` and `run_session` already
take an `on_result` callback, which `cli.py` wires to the streaming JSONL
writer. Hanging the repaint on the same callback means no timer, no thread, and
nothing competing with the runner, which is also why there is no spinner: an
animation is the only thing here that would need a clock of its own.

No progress bar. It duplicates `15/22` in 25 columns, its block glyphs are
Ambiguous-width, and read linearly by a screen reader it is two dozen
meaningless symbols on a line that changes ten times a second.

Mechanics:

- Erase with `\r` then `ESC[2K`, never by padding with spaces.
- Keep the rendered line strictly shorter than the width. A terminal with
  auto-wrap writes the last cell and defers the wrap, leaving the cursor
  position ambiguous.
- Re-measure width on every repaint rather than handling `SIGWINCH`, which does
  not exist on Windows.
- Skip the write when the rendered string is identical to the last one.
- Rate-limit to 10 Hz.
- Never hide the cursor. `run()` uses `os._exit`, which skips `atexit` by
  design, so there is no exit path that reliably restores a hidden cursor; a
  Ctrl-C would leave the user's shell with an invisible cursor until `reset`.
  Parking the cursor at column 0 costs nothing and removes the whole class.
- On close, commit exactly one `\n`-terminated line so scrollback keeps a record.

Survivors scroll into scrollback as they land; killed mutants never do.
Survivors are rare, so this does not reproduce the agent format, and on a long
run the reader can start work before the run ends.

Suppressed entirely when stderr is not a TTY, `TERM` is unset or `dumb`, `CI` is
set, or `--no-progress` is passed. Those cases get milestone lines instead --
committed, greppable, at most one per ten seconds:

```
moonbuggy: 11/22 settled -- 9 killed, 2 survived, 0:05
```

### One writer

While the live region is open, exactly one object writes to the terminal. It
exposes `log(text)`, which erases the live line, writes `text` and a newline,
then redraws; and `tick(state)`. Every existing in-run `print` in `cli.py` --
the per-file skip messages, the deeply-nested-site warnings, "running coverage
pass" -- routes through `log`. A test asserts no bare `print` executes between
the region opening and closing.

The runner's own children are already safe: every fork site in `forkserver.py`
redirects fds 1 and 2 to `/dev/null` before running pytest, including the warm
session host, from which the flakiness probe child is forked and therefore
inherits the redirection. This is a property worth stating so a future fork site
does not quietly break it.

## Rendering source text safely

`mutant.original` and `mutant.mutated` are raw source lines, stripped of leading
and trailing whitespace by `generate.py` but otherwise arbitrary.

- **Tabs.** A `\t` expands to the next multiple of eight from the terminal's
  current column, not the file's, so an indented line renders at a different
  visual indent than it has on disk and any following column is destroyed.
  `expandtabs(8)` on the raw line before adding the report's indent, always.
- **Control characters.** `\x0c` is legal Python whitespace and appears in real
  files. `\x1b` appears in string literals. Without handling, the report replays
  a freshly cloned repository's escape sequences into the reader's terminal.
  Every character whose `unicodedata.category` is `Cc`, and every lone
  surrogate, becomes `\xNN` before measuring or printing. This exposure exists
  today through `nearest_test` in the agent format and is worth fixing there
  too.
- **Trailing whitespace.** `rstrip` both sides for display. When the two lines
  are equal afterwards, print `(differs only in trailing whitespace)` rather
  than two lines that look identical.
- **Deep indentation.** When the shared leading whitespace exceeds eight
  columns, dedent both lines by it. The group header already carries the
  location.
- **Long lines.** Window-truncate centred on the changed span, with `...` at the
  cut ends. Never tail-truncate: the change may be at column 300. Never wrap: a
  continuation line carries no `-`/`+` sigil, so it becomes ambiguous which side
  it belongs to.
- **Node ids and mutant ids.** Never truncated, never wrapped by us, always on
  their own line. They are paste targets; the head carries the path and the tail
  disambiguates, so neither end is safe to cut. Let the terminal soft-wrap.

### The caret ruler

The span is the difference between the two lines after a common-prefix and
common-suffix comparison, with three corrections:

- **Clamp.** The two spans can overlap -- `x = 11` against `x = 1` gives a
  prefix of 5 and a suffix of 1 against a shorter length of 5 -- which without
  clamping yields a negative-length span. Bound the suffix by
  `min(len(a), len(b)) - prefix_len`.
- **Snap outward to token boundaries.** Raw comparison of
  `return stock > 0 ...` against `return stock >= 0 ...` yields a span
  containing only `=`, because the `>` is common to both. A caret under the `=`
  alone is legible but understates the change; snapping to the surrounding
  non-space run gives `>=`. Likewise `-=` against `+=` rather than `-` against
  `+`.
- **Grapheme safety.** Extend the span while either boundary sits on a combining
  mark, so a highlight never begins with an orphaned diacritic.

Ruler columns are computed in display cells, not characters, by a small
`east_asian_width` helper: zero for combining marks and `Mn`/`Me`/`Cf`
categories, two for `W` and `F`, and for `A` two when the locale is CJK. The
helper undercounts emoji ZWJ sequences, which is documented rather than solved
and is one more reason nothing is right-aligned after source text.

## Width

Resolution order:

1. `--width N`
2. `COLUMNS`, when it parses to a positive integer. It is rarely exported, so
   its presence is a deliberate override.
3. `os.get_terminal_size(fd)` in a `try`/`except OSError`, where `fd` belongs to
   the stream the report is going to. `shutil.get_terminal_size` is the wrong
   call because it silently substitutes 80, leaving no way to distinguish a
   measurement from a guess.
4. Undetectable: 80.

Then `width = min(detected, 100)`. Nothing needs more, and a 300-column diff
line requires head-turning.

Because nothing is right-aligned, width affects only diff windowing and where
long values move to their own line. Below 60 columns the layout is fully
stacked: indent drops to 2, and diff windows are `width - 4`.

## Encoding and interruption

Two pre-existing defects that the reporter makes reachable on ordinary input.

`srcio.py` deliberately supports non-UTF-8 source through
`tokenize.detect_encoding` and PEP 263, so `mutant.original` can hold characters
that stdout cannot encode. `print` uses `errors="strict"`, so under a `C` locale
this raises `UnicodeEncodeError` -- inside `_run`, past `main`'s handler, which
catches only the four anticipated error types. It escapes as a traceback,
violating the project's own criterion that anticipated failures produce an
actionable message, and it escapes before `run()`'s explicit flush, so a partly
buffered report is lost.

- `sys.stdout.reconfigure(errors="backslashreplace")` and the same for stderr,
  at startup. A stray character degrades to `\xe9` and the run survives.
- Pin `encoding="utf-8"` on `results.txt` and on the JSONL reads and writes.
  `results.txt` is an agent artifact and must not depend on the locale.
  `results.jsonl` currently survives only because `json.dumps` defaults to
  `ensure_ascii=True`.
- Glyph capability is an encoding question before it is a font question. Since
  the report is ASCII this is moot for the report itself, but
  `sys.stdout.encoding` is `None` when stdout is not a real file -- the in-process
  test path -- and must be read as `ascii`.

There is no `KeyboardInterrupt` handler today, so Ctrl-C prints a traceback.
`main` catches it, erases the live line, prints
`moonbuggy: interrupted after N/M mutants; partial results in
.moonbuggy/results.jsonl`, and returns 130. Because `os._exit` skips `atexit`,
terminal teardown lives in a `finally` inside `_run`, with a defensive re-emit
in `run()` before the exit.

## Structure

Two new modules, both built so the interesting logic is testable without a
terminal.

`terminal.py` owns everything that depends on the environment: width
resolution, colour and format resolution, display-width measurement, and the
live region. Its surface is a `Terminal` object plus the two resolver functions,
so a test constructs one with an explicit width and colour depth.

`humanreport.py` owns rendering. Pure functions from a list of `Record` to a
string: no I/O, no environment reads, width and colour passed in. Every
alignment, truncation, ruler, and encoding case is a unit test on a string.

`report.py` gains `original` and `mutated` on `Record`. Additive JSON keys break
no existing reader, and without them the human reporter would have to split its
own rendered `diff` back into operands -- a reporter parsing its own output
format -- because `cli.py` deliberately renders from records read back off disk
so the two artifacts cannot drift.

`cli.py` gains `--report`, `--color`, `--width`, and `--no-progress`, routes its
in-run prints through the live region, and handles the interrupt and encoding
setup.

## Testing

- A golden-output test pinning the agent format byte for byte, including
  `results.txt` and `results.jsonl`, so constraint 1 is enforced rather than
  trusted.
- Renderer unit tests over `humanreport`: multi-mutant groups, absent
  `nearest_test`, module-level mutants, `tests_run=0`, tabs, control characters,
  combining marks, double-width characters, whitespace-only mutations, long
  lines, long node ids, and widths of 40, 80, 100, and 200.
- Span unit tests: the overlap clamp, the token snap on the `>`/`>=` case, and
  the combining-mark extension.
- Resolver unit tests over the full precedence tables for format and colour.
- A live-region test asserting one physical row, that identical frames are
  skipped, that `log` interleaves without corruption, and that no bare `print`
  runs while the region is open.
- An end-to-end run against `tests/fixtures/sample_project` with
  `--report=human` on a non-TTY, asserting the report renders.

## Out of scope

Named because the reviews argued for them and the reasoning should not be lost.
Each needs its own spec.

- **Equivalent-mutant dismissal as a first-class action.** Roughly 40% of
  survivors in the project's own OSS triage were equivalent or intentional. A
  punch list whose items cannot be checked off is not a punch list, and the
  report has no way to distinguish "write a test" from "suppress this". The
  report should eventually show both exits symmetrically, and make the
  `# moonbuggy: skip` mechanism frictionless while keeping its justification
  mandatory.
- **Previous-run delta.** Keeping `results.prev.jsonl` and leading with what
  changed. Ids are stable for unchanged source, so the join is trivial. This is
  what makes the incremental run -- "did the test I just wrote kill the thing I
  aimed at" -- a three-line answer instead of a reprint.
- **Triage memory.** A record of survivors examined and deliberately deferred,
  which cannot live in a source comment without writing a justification the
  author does not have.
- **`--since <ref>` and `--id <id>`.** Scoping a run to a branch's changes, and
  re-running one mutant to confirm a fix. Neither is a reporter feature, and
  both are what make the report's advice followable.
