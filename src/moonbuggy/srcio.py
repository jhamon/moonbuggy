"""Reading and writing Python source that is not necessarily clean UTF-8.

Milestone M1.4.7. `Path.read_text()` assumes UTF-8, so a file carrying a PEP 263
coding cookie (`# -*- coding: latin-1 -*-`) either raises a UnicodeDecodeError
that surfaced as a traceback, or -- worse -- decodes without error into the
wrong characters and gets mutated anyway. A mis-mutated file produces a status,
and a status derived from the wrong bytes is a lie.

Two rules here:

- Decode the way the interpreter does. `tokenize.detect_encoding` implements
  PEP 263 exactly: BOM first, then a cookie in either of the first two lines,
  then UTF-8. Using it means moonbuggy sees the same characters Python does.
- Refuse rather than guess. A file whose declared encoding cannot decode it is
  not a file we can safely mutate, so it raises SourceError and the caller skips
  it with a message naming the file.

The cookie also has to be neutralised before the text reaches `ast.parse` or
`compile`, which reject a coding declaration in an already-decoded `str`. That
is what `strip_coding_cookie` is for, and why it substitutes a same-length
replacement rather than deleting the line: every byte offset and line number in
the rest of the file has to stay exactly where it was, because mutant splicing
and line attribution are both offset-based.
"""

import contextlib
import os
import re
import tokenize

DEFAULT_ENCODING = "utf-8"

# The PEP 263 pattern, as the reference implementation states it.
_COOKIE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-_.a-zA-Z0-9]+)")


_ADVICE = (
    "moonbuggy will not mutate a file it cannot read exactly. Add a PEP 263 "
    "coding declaration to the file, or exclude it with --exclude."
)


class SourceError(RuntimeError):
    """A source file could not be read as Python text."""


def detect_encoding(path):
    """The encoding Python itself would use to read `path`.

    Args:
        path: filesystem path to a Python source file.

    Returns:
        an encoding name suitable for :func:`str.encode`.

    Raises:
        SourceError: if the file cannot be opened or declares an encoding that does not
            exist.
    """
    try:
        with open(path, "rb") as handle:
            encoding, _ = tokenize.detect_encoding(handle.readline)
        return encoding
    except (OSError, SyntaxError, LookupError, ValueError) as error:
        raise SourceError(
            f"cannot determine the encoding of {path}: {error}"
        ) from error


# Keyed by (path, mtime, size), so an edit during a run is a miss rather than a
# stale hit. Hypothesis H5 in docs/perf-hypotheses.md: every mutant reads its
# module's source, and every mutant runs in its own forked process -- so a
# process-local cache is worth nothing on its own. It pays only because the
# warm host fills it BEFORE forking, and every grandchild inherits it.
_SOURCE_CACHE = {}


def read_source(path):
    """Read a Python source file as text, honouring its declared encoding.

    Args:
        path: filesystem path to a Python source file.

    Returns:
        the file's contents as ``str``.

    Raises:
        SourceError: if the file cannot be read or cannot be decoded with the encoding
            it declares.
    """
    try:
        stat = os.stat(path)
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None

    if key is not None and key in _SOURCE_CACHE:
        return _SOURCE_CACHE[key]

    text = _read_source_uncached(path)
    if key is not None:
        _SOURCE_CACHE[key] = text
    return text


def prewarm(paths):
    """Fill the source cache for `paths`, ignoring any that cannot be read.

    Called in the warm host once the job list is known, so the cost of reading
    each module is paid once for the whole run rather than once per mutant.
    Failures are ignored here on purpose: this is an optimisation, and the
    grandchild that actually needs the file will raise a real SourceError with
    a message about that file.
    """
    for path in paths:
        with contextlib.suppress(SourceError):
            read_source(path)


def _read_source_uncached(path):
    try:
        with tokenize.open(str(path)) as handle:
            return handle.read()
    # UnicodeDecodeError is a ValueError, so it has to be caught first or the
    # broader clause below would swallow it and lose the byte offset.
    except UnicodeDecodeError as error:
        raise SourceError(
            f"cannot decode {path} as {error.encoding}: "
            f"byte {error.object[error.start]:#04x} at offset {error.start}. "
            f"{_ADVICE}"
        ) from error
    # `tokenize.detect_encoding` reports undeclared non-UTF8 bytes as a
    # SyntaxError about a missing encoding declaration, which is the same
    # problem wearing a different exception type.
    except SyntaxError as error:
        raise SourceError(f"cannot decode {path}: {error.msg}. {_ADVICE}") from error
    except (OSError, LookupError, ValueError) as error:
        raise SourceError(f"cannot read {path}: {error}") from error


def encode_source(text, encoding):
    """Encode source text back to the bytes an importer will decode.

    Args:
        text: the source as ``str``.
        encoding: the encoding the file declares.

    Returns:
        ``bytes``.

    Raises:
        SourceError: if the text cannot be represented in that encoding.
    """
    try:
        return text.encode(encoding)
    except (UnicodeEncodeError, LookupError) as error:
        raise SourceError(f"cannot re-encode source as {encoding}: {error}") from error


def replace_line(source, line, text):
    """Replace one line's content, keeping everything around it byte for byte.

    The single place mutation is applied to text. It was previously written
    twice -- once for the in-memory path and once for the naive runner -- and
    two copies of "preserve the indentation" is two chances to preserve
    slightly different things.

    Indentation and trailing whitespace both come back from the line being
    replaced, because `Mutant.original` and `Mutant.mutated` are stripped for
    display. Restoring only the indentation loses any trailing whitespace,
    which breaks the round-trip property M1.2.4 asserts -- and a mutation that
    quietly edits whitespace it was not asked to edit is a mutation whose
    effect is not fully described by its own diff.

    Args:
        source: the full source text.
        line: 1-based line number to replace.
        text: the replacement content, without indentation.

    Returns:
        the full source with that line replaced.
    """
    lines = source.splitlines(keepends=True)
    index = line - 1
    target = lines[index]

    newline = "\n" if target.endswith("\n") else ""
    body = target[: -len(newline)] if newline else target
    indent = body[: len(body) - len(body.lstrip())]
    # `body.rstrip()` also strips a lone `\r`, so CRLF endings survive intact.
    trailing = body[len(body.rstrip()) :]

    lines[index] = f"{indent}{text}{trailing}{newline}"
    return "".join(lines)


def strip_coding_cookie(text):
    """Neutralise a PEP 263 cookie so the text can be passed to `ast.parse`.

    `compile()` and `ast.parse()` refuse a coding declaration inside a `str`,
    since the string is already decoded. Rather than delete the line, the word
    `coding` is replaced by an equal-length word that the cookie pattern does
    not match, so every subsequent line number, column offset and byte position
    is unchanged. Splicing and line attribution both depend on that.

    Args:
        text: source text, possibly carrying a cookie in its first two lines.

    Returns:
        source text with any cookie neutralised.
    """
    lines = text.split("\n")
    for index in range(min(2, len(lines))):
        if _COOKIE.match(lines[index]):
            lines[index] = lines[index].replace("coding", "codinq", 1)
            break
    return "\n".join(lines)
