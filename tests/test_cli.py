"""Criteria E3/E4/E7, F1/F3/F4, H2/H5 -- the tool as an evaluator meets it.

The zero-config test deliberately runs against a project generated here rather
than against tests/fixtures/sample_project. Passing on the fixture proves only
that moonbuggy works on the one layout it was developed against; criterion H2
asks whether it works on a project it has never seen.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

PROJECT = '''\
def clamp(value, ceiling):
    if value > ceiling:
        return ceiling
    return value


def total(values):
    running = 0
    for value in values:
        running += value
    return running
'''

TESTS = '''\
from calc import clamp, total


def test_clamp_passes_small_values_through():
    assert clamp(1, 10) == 1


def test_clamp_caps_large_values():
    assert clamp(99, 10) == 10


def test_total_sums():
    assert total([1, 2, 3]) == 6
'''


@pytest.fixture
def throwaway(tmp_path):
    """A minimal pytest project moonbuggy has never seen, in a flat layout."""
    (tmp_path / "calc.py").write_text(PROJECT)
    (tmp_path / "test_calc.py").write_text(TESTS)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
    )
    return tmp_path


def moonbuggy(*args, cwd, expect=None):
    proc = subprocess.run(
        [sys.executable, "-m", "moonbuggy.cli", *args],
        cwd=cwd, capture_output=True, text=True, timeout=300,
    )
    if expect is not None:
        assert proc.returncode == expect, f"{proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    return proc


def test_zero_config_run_works_on_an_unseen_project(throwaway):
    # Criterion H2. No flags, no config file.
    proc = moonbuggy(cwd=throwaway)

    assert proc.returncode in (0, 1), proc.stderr
    assert (throwaway / ".moonbuggy" / "results.jsonl").exists()
    assert (throwaway / ".moonbuggy" / "results.txt").exists()


def test_jsonl_records_are_all_parseable(throwaway):
    moonbuggy(cwd=throwaway)

    lines = (throwaway / ".moonbuggy" / "results.jsonl").read_text().splitlines()

    assert lines
    for line in lines:
        record = json.loads(line)
        assert record["status"] in {"KILLED", "SURVIVED", "TIMEOUT", "SUSPICIOUS", "SKIPPED"}


def test_plaintext_has_one_line_per_jsonl_record(throwaway):
    moonbuggy(cwd=throwaway)
    out = throwaway / ".moonbuggy"

    jsonl_lines = out.joinpath("results.jsonl").read_text().splitlines()
    text_lines = out.joinpath("results.txt").read_text().strip().splitlines()

    assert len(text_lines) == len(jsonl_lines)


def test_grep_for_survived_matches_the_jsonl(throwaway):
    # Criterion E4, exactly as the criteria phrase it.
    moonbuggy(cwd=throwaway)
    out = throwaway / ".moonbuggy"

    records = [json.loads(line) for line in out.joinpath("results.jsonl").read_text().splitlines()]
    expected = sum(1 for r in records if r["status"] == "SURVIVED")
    grepped = sum(1 for line in out.joinpath("results.txt").read_text().splitlines()
                  if line.startswith("SURVIVED"))

    assert grepped == expected


def test_show_prints_the_diff_for_a_mutant_id(throwaway):
    # Criterion E7: the diff is not in the plaintext view, so there must be a
    # lookup that produces it.
    moonbuggy(cwd=throwaway)
    records = [
        json.loads(line)
        for line in (throwaway / ".moonbuggy" / "results.jsonl").read_text().splitlines()
    ]

    proc = moonbuggy("show", records[0]["id"], cwd=throwaway, expect=0)

    assert "diff" in proc.stdout
    assert "- " in proc.stdout and "+ " in proc.stdout


def test_show_reports_an_unknown_id_clearly(throwaway):
    moonbuggy(cwd=throwaway)

    proc = moonbuggy("show", "no-such-mutant", cwd=throwaway, expect=2)

    assert "no mutant with id" in proc.stderr


def test_second_run_is_served_from_cache(throwaway):
    # Criterion F1.
    moonbuggy(cwd=throwaway)

    proc = moonbuggy(cwd=throwaway)

    assert "cached=" in proc.stderr
    cached = int(proc.stderr.split("cached=")[1].split()[0])
    assert cached > 0


def test_cached_run_produces_identical_output(throwaway):
    """Criterion F3. Cached results must be indistinguishable from fresh ones,
    modulo timing -- otherwise the cache changes what the user is told."""
    moonbuggy(cwd=throwaway)
    first = _records_without_timing(throwaway)

    moonbuggy(cwd=throwaway)
    second = _records_without_timing(throwaway)

    assert first == second


def test_no_cache_flag_bypasses_the_cache(throwaway):
    moonbuggy(cwd=throwaway)

    proc = moonbuggy("--no-cache", cwd=throwaway)

    assert "cached=0" in proc.stderr


def test_clear_cache_flag_forces_a_cold_run(throwaway):
    # Criterion F4.
    moonbuggy(cwd=throwaway)

    proc = moonbuggy("--clear-cache", cwd=throwaway)

    assert "cached=0" in proc.stderr


def test_corrupt_cache_degrades_to_a_cold_run(throwaway):
    moonbuggy(cwd=throwaway)
    (throwaway / ".moonbuggy" / "cache.json").write_text("{corrupt")

    proc = moonbuggy(cwd=throwaway)

    assert proc.returncode in (0, 1), proc.stderr
    assert "cached=0" in proc.stderr


def test_editing_source_invalidates_only_that_files_entries(throwaway):
    # Criterion F2, end to end.
    moonbuggy(cwd=throwaway)
    (throwaway / "calc.py").write_text(PROJECT.replace("running = 0", "running = 0  # touched"))

    proc = moonbuggy(cwd=throwaway)

    cached = int(proc.stderr.split("cached=")[1].split()[0])
    assert cached == 0, "editing the mutated module must invalidate its entries"


def test_running_outside_a_pytest_project_gives_a_clear_error(tmp_path):
    # Criterion H5: actionable message, not a traceback.
    proc = moonbuggy(cwd=tmp_path, expect=2)

    assert "does not look like a pytest project" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_operator_selection_narrows_the_run(throwaway):
    # Criterion H4: advanced options exist but are never required.
    moonbuggy("--no-cache", cwd=throwaway)
    everything = len(_records(throwaway))

    moonbuggy("--no-cache", "--operators", "comparison_swap", cwd=throwaway)
    narrowed = _records(throwaway)

    assert 0 < len(narrowed) < everything
    assert {r["operator"] for r in narrowed} == {"comparison_swap"}


def test_exclude_filters_files(throwaway):
    (throwaway / "helper.py").write_text("def double(x):\n    return x * 2\n")

    moonbuggy("--no-cache", cwd=throwaway)
    with_helper = {r["file"] for r in _records(throwaway)}

    moonbuggy("--no-cache", "--exclude", "helper", cwd=throwaway)
    without = {r["file"] for r in _records(throwaway)}

    assert "helper.py" in with_helper
    assert "helper.py" not in without


def _records(project):
    path = project / ".moonbuggy" / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _records_without_timing(project):
    return [
        {k: v for k, v in record.items() if k != "duration"}
        for record in _records(project)
    ]
