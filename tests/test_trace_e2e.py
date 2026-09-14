"""End-to-end: `moonbuggy run <id> --trace-json` on a real project.

Slow, because each test pays a coverage pass plus a pytest subprocess per
mutant -- the real path the trace must describe. The fast shape tests live in
`tests/test_trace_cli.py` and `tests/test_trace_properties.py`.
"""

import json

import pytest

pytestmark = pytest.mark.slow

PROJECT = """\
def clamp(value, ceiling):
    if value > ceiling:
        return ceiling
    return value
"""

TESTS = """\
from calc import clamp


def test_clamp_passes_small_values_through():
    assert clamp(1, 10) == 1


def test_clamp_caps_large_values():
    assert clamp(99, 10) == 10
"""


@pytest.fixture
def throwaway(tmp_path):
    (tmp_path / "calc.py").write_text(PROJECT)
    (tmp_path / "test_calc.py").write_text(TESTS)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
    )
    return tmp_path


def _run(project, *args, expect=0):
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "moonbuggy.cli", *args],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == expect, f"{proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    return proc


def test_trace_json_links_a_kill_to_the_assert_that_died(throwaway):
    import subprocess
    import sys

    full = subprocess.run(
        [sys.executable, "-m", "moonbuggy.cli"],
        cwd=throwaway,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert full.returncode in (0, 1), full.stderr
    killed = next(
        json.loads(line)
        for line in (throwaway / ".moonbuggy" / "results.jsonl")
        .read_text()
        .splitlines()
        if json.loads(line)["status"] == "KILLED"
    )

    proc = _run(throwaway, "run", killed["id"], "--trace-json")

    # stdout: one JSON line, the verdict plus the exact assertion that died.
    trace = json.loads(proc.stdout)
    assert trace["verdict"]["status"] == "KILLED"
    assert trace["verdict"]["assert"] and all(
        node.startswith("test_calc.py::") for node in trace["verdict"]["assert"]
    )
    assert trace["verdict"]["killreason"] == "assertion_failed"
    assert trace["id"] == killed["id"]

    # Persisted: the same line is in traces.jsonl, so the evidence outlives
    # the terminal.
    persisted = [
        json.loads(line)
        for line in (throwaway / ".moonbuggy" / "traces.jsonl").read_text().splitlines()
    ]
    assert trace in persisted


def test_trace_json_survivor_names_the_mutation_not_a_test(throwaway):
    import subprocess
    import sys

    full = subprocess.run(
        [sys.executable, "-m", "moonbuggy.cli"],
        cwd=throwaway,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert full.returncode in (0, 1), full.stderr
    survivor = next(
        json.loads(line)
        for line in (throwaway / ".moonbuggy" / "results.jsonl")
        .read_text()
        .splitlines()
        if json.loads(line)["status"] == "SURVIVED"
    )

    proc = _run(throwaway, "run", survivor["id"], "--trace-json", expect=1)

    trace = json.loads(proc.stdout)
    assert trace["verdict"]["status"] == "SURVIVED"
    assert trace["verdict"]["assert"] == []
    assert trace["verdict"]["crash"] is None
    # The evidence for a survivor is the mutation itself.
    assert trace["mutated"] == survivor["mutated"]
    assert trace["selection"]["selected"]


def test_trace_json_appends_across_re_measures(throwaway):
    import subprocess
    import sys

    full = subprocess.run(
        [sys.executable, "-m", "moonbuggy.cli"],
        cwd=throwaway,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert full.returncode in (0, 1), full.stderr
    survivor = next(
        json.loads(line)
        for line in (throwaway / ".moonbuggy" / "results.jsonl")
        .read_text()
        .splitlines()
        if json.loads(line)["status"] == "SURVIVED"
    )

    _run(throwaway, "run", survivor["id"], "--trace-json", expect=1)
    _run(throwaway, "run", survivor["id"], "--trace-json", expect=1)

    path = throwaway / ".moonbuggy" / "traces.jsonl"
    lines = path.read_text().splitlines()
    assert len(lines) == 2  # appended, not overwritten
    assert json.loads(lines[0]) == json.loads(lines[1])
