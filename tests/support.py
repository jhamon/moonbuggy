"""Shared helpers for tests that drive the real CLI against a real project.

Projects are built in `tmp_path` rather than checked in under tests/fixtures.
Two reasons: several of the robustness scenarios are files that are deliberately
not valid Python or not valid UTF-8, which are unpleasant things to keep in a
repository and impossible to keep in one without some tool trying to fix them;
and building a project in the test keeps the scenario and its assertion in one
place, where a reader can see what is being claimed.
"""

import json
import subprocess
import sys
from pathlib import Path

CONFTEST = (
    "import sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, str(Path(__file__).parent))\n"
)


def write_project(root, files):
    """Materialise a project from a {relative path: contents} mapping.

    :param root: directory to write into, usually `tmp_path`.
    :param files: str contents are written as UTF-8 text; bytes are written
        verbatim, which is how the non-UTF8 scenario gets its bytes intact.
    :returns: `root`, for chaining.
    """
    root = Path(root)
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            path.write_bytes(contents)
        else:
            path.write_text(contents)
    if not (root / "pytest.ini").exists():
        (root / "pytest.ini").write_text("[pytest]\n")
    if not (root / "conftest.py").exists():
        (root / "conftest.py").write_text(CONFTEST)
    return root


def moonbuggy(*args, cwd, expect=None, timeout=300):
    """Run the CLI as a subprocess, the way a user does.

    In-process calls to `main()` would share the test runner's imported
    modules, which is exactly the state the fork-based runner depends on NOT
    having. A subprocess is the only faithful way to exercise it.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "moonbuggy.cli", *args],
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )
    if expect is not None:
        assert proc.returncode == expect, (
            f"expected exit {expect}, got {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


def assert_no_traceback(proc):
    """Criterion M1.4.12: a traceback is never the user-facing output.

    A traceback tells the user about moonbuggy's internals when what they need
    is what to do about their project. It is also indistinguishable, to anyone
    reading a CI log, from moonbuggy having crashed.
    """
    combined = proc.stdout + proc.stderr
    assert "Traceback (most recent call last)" not in combined, combined


def records(project, output_dir=".moonbuggy"):
    """Every JSONL record from the last run in `project`."""
    path = Path(project) / output_dir / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def status_of(project, id_fragment):
    """The status of the single record whose id contains `id_fragment`."""
    matches = [r for r in records(project) if id_fragment in r["id"]]
    assert len(matches) == 1, f"{id_fragment} matched {[m['id'] for m in matches]}"
    return matches[0]["status"]


def status_of_mutation(project, original, mutated):
    """The status of the mutant that rewrites `original` as `mutated`.

    Keyed on the diff rather than on file:line, because a line number in a test
    is a second copy of the fixture that silently rots the moment a comment is
    added to it. The diff is what the assertion is actually about.
    """
    wanted = f"- {original}\n+ {mutated}"
    matches = [r for r in records(project) if r["diff"] == wanted]
    assert len(matches) == 1, (
        f"expected exactly one mutant rewriting {original!r} as {mutated!r}; "
        f"found {[r['diff'] for r in matches]}"
    )
    return matches[0]["status"]
