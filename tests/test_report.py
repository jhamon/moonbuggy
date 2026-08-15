"""Criterion E: reporting.

The design's premise (5.1) is that the reader is an agent grepping output, not a
human reading a dashboard. That makes the format itself the feature: a fixed
leading keyword so `grep SURVIVED` works with no knowledge of the schema, and
key=value tokens so naive whitespace splitting parses it.
"""

import json

import pytest

from moonbuggy.mutant import Mutant
from moonbuggy.report import (
    STATUS_KEYWORDS,
    plaintext_from_records,
    read_jsonl,
    record_for,
    render_line,
    write_jsonl,
)
from moonbuggy.runner import Result


def make(status, module="sample/inventory.py", line=9, nearest=None, suppressed=False):
    mutant = Mutant(
        id=f"{module}:{line}:comparison_swap:0",
        module=module,
        line=line,
        operator="comparison_swap",
        original="return stock > 0 and not discontinued",
        mutated="return stock >= 0 and not discontinued",
        suppressed=suppressed,
    )
    return Result(mutant, status, tests_run=3, duration=0.12, nearest_test=nearest)


def test_record_carries_every_required_field():
    record = record_for(make("SURVIVED", nearest="tests/test_inventory.py::test_x"))

    for key in ("id", "status", "file", "line", "operator", "category", "nearest_test", "diff"):
        assert key in record, key


def test_diff_shows_original_and_mutated():
    record = record_for(make("SURVIVED"))

    assert record["diff"] == (
        "- return stock > 0 and not discontinued\n"
        "+ return stock >= 0 and not discontinued"
    )


def test_jsonl_is_one_parseable_object_per_line(tmp_path):
    path = tmp_path / "out.jsonl"
    write_jsonl([make("KILLED"), make("SURVIVED")], path)

    lines = path.read_text().splitlines()

    assert len(lines) == 2
    assert all(json.loads(line)["id"] for line in lines)


def test_jsonl_is_written_streamingly(tmp_path):
    """Criterion E2. A run killed partway must leave complete lines, not a
    half-written final record that breaks every downstream parser."""
    path = tmp_path / "out.jsonl"

    def results():
        yield make("KILLED")
        yield make("SURVIVED")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        write_jsonl(results(), path)

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line) for line in lines)


def test_plaintext_line_starts_with_a_fixed_status_keyword():
    line = render_line(record_for(make("SURVIVED")))

    assert line.split()[0] == "SURVIVED"


def test_every_status_keyword_is_in_the_fixed_vocabulary():
    assert STATUS_KEYWORDS == {"KILLED", "SURVIVED", "TIMEOUT", "SUSPICIOUS", "SKIPPED"}


def test_plaintext_line_has_no_embedded_newlines():
    # Criterion E5. The diff stays out of the plaintext view; `moonbuggy show`
    # retrieves it. One line per mutant keeps grep and awk usable.
    line = render_line(record_for(make("SURVIVED")))

    assert "\n" not in line


def test_plaintext_uses_key_value_tokens():
    # Criterion E6. Naive whitespace splitting must parse it.
    line = render_line(record_for(make("SURVIVED", nearest="tests/test_inventory.py::test_x")))

    tokens = line.split()
    pairs = dict(t.split("=", 1) for t in tokens if "=" in t)

    assert pairs["nearest_test"] == "tests/test_inventory.py::test_x"
    assert pairs["line"] == "9"


def test_grep_count_matches_jsonl_survived_count(tmp_path):
    # Criterion E4, stated the way the criteria state it.
    results = [make("SURVIVED"), make("KILLED"), make("SURVIVED"), make("TIMEOUT")]
    path = tmp_path / "out.jsonl"
    write_jsonl(results, path)

    records = read_jsonl(path)
    plaintext = plaintext_from_records(records)

    grep_count = sum(1 for line in plaintext.splitlines() if "SURVIVED" in line)
    jsonl_count = sum(1 for r in records if r["status"] == "SURVIVED")

    assert grep_count == jsonl_count == 2


def test_plaintext_regenerated_from_jsonl_is_identical(tmp_path):
    """Criterion E3. The plaintext view is derived, not independently authored,
    so the two artifacts cannot drift apart."""
    results = [make("SURVIVED"), make("KILLED"), make("SKIPPED", suppressed=True)]
    path = tmp_path / "out.jsonl"

    during_run = plaintext_from_records([record_for(r) for r in results])
    write_jsonl(results, path)
    regenerated = plaintext_from_records(read_jsonl(path))

    assert regenerated == during_run


def test_one_plaintext_line_per_mutant(tmp_path):
    results = [make("SURVIVED"), make("KILLED"), make("TIMEOUT")]

    plaintext = plaintext_from_records([record_for(r) for r in results])

    assert len(plaintext.splitlines()) == len(results)


def test_survived_without_covering_tests_reports_no_nearest_test():
    line = render_line(record_for(make("SURVIVED", nearest=None)))

    assert "nearest_test=-" in line
