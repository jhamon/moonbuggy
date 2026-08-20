"""The run-level summary: one versioned JSON object an agent can read.

Per-mutant data is JSONL, because there is one object per mutant and a reader
streams them. A run has exactly one summary, so it is a single object rather
than a line in that file -- see :func:`moonbuggy.report.run_summary`. These
tests pin the shape a consumer keys off, and the schema version that lets them
know when it moved.
"""

import json

from test_report import make

from moonbuggy.report import (
    RECORD_SCHEMA,
    STATUS_KEYWORDS,
    SUMMARY_SCHEMA,
    read_jsonl,
    record_for,
    run_summary,
    write_jsonl,
    write_summary,
)
from moonbuggy.runner import Result


def summary_for(results, **kwargs):
    options = {
        "elapsed": 1.5,
        "cached": 0,
        "config": {"operators": None},
        "scope": {"diff_scoped": False},
        "acceptance": {"accepted": 0, "unexplained": 0},
        "exit_code": 0,
    }
    options.update(kwargs)
    return run_summary([record_for(r) for r in results], **options)


def test_the_summary_is_one_object_not_a_jsonl_line():
    summary = summary_for([make("KILLED")])

    assert isinstance(summary, dict)
    # Round-trips as a single JSON document, which is what `--json` prints.
    assert json.loads(json.dumps(summary, sort_keys=True)) == summary


def test_counts_cover_every_status_even_the_absent_ones():
    summary = summary_for([make("SURVIVED"), make("KILLED"), make("KILLED")])

    assert summary["counts"] == {keyword.lower(): 0 for keyword in STATUS_KEYWORDS} | {
        "survived": 1,
        "killed": 2,
    }


def test_every_status_keyword_has_a_count_key():
    # A new keyword must appear here rather than silently going unreported.
    counts = summary_for([])["counts"]

    assert set(counts) == {keyword.lower() for keyword in STATUS_KEYWORDS}


def test_totals_split_cached_from_measured():
    summary = summary_for([make("KILLED"), make("KILLED"), make("SURVIVED")], cached=2)

    assert summary["total"] == 3
    assert summary["cached"] == 2
    assert summary["measured"] == 1


def test_the_summary_carries_both_schema_versions():
    summary = summary_for([make("KILLED")])

    assert summary["schema"] == SUMMARY_SCHEMA
    # The results file's own version, so a consumer holding a summary knows
    # what shape the records beside it are in.
    assert summary["record_schema"] == RECORD_SCHEMA


def test_the_effective_configuration_is_carried_through_verbatim():
    config = {
        "operators": ["comparison_swap"],
        "include": ["sample/"],
        "exclude": [],
        "pytest_args": ["-p", "no:randomly"],
        "timeout": 30.0,
    }

    summary = summary_for([make("KILLED")], config=config)

    assert summary["config"] == config


def test_the_scope_and_ledger_summaries_are_carried_through_verbatim():
    scope = {"diff_scoped": True, "since": "origin/main", "merge_base": "abc123"}
    acceptance = {"accepted": 2, "unexplained": 1, "stale": 0}

    summary = summary_for([make("SURVIVED")], scope=scope, acceptance=acceptance)

    assert summary["scope"] == scope
    assert summary["acceptance"] == acceptance


def test_wall_time_and_exit_code_are_reported():
    summary = summary_for([make("SURVIVED")], elapsed=12.3456, exit_code=1)

    assert summary["elapsed"] == 12.346
    assert summary["exit_code"] == 1


def test_write_summary_leaves_one_json_object_on_disk(tmp_path):
    path = tmp_path / "summary.json"

    write_summary(summary_for([make("KILLED")]), path)

    assert json.loads(path.read_text(encoding="utf-8"))["total"] == 1
    # Ends with a newline, so `cat` and shell pipelines behave.
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_the_summary_never_lands_in_the_results_file(tmp_path):
    # The discriminator question, settled by not needing one: every line of
    # results.jsonl is a mutant record, so `wc -l` is still the mutant count.
    path = tmp_path / "results.jsonl"
    results = [make("KILLED"), make("SURVIVED")]
    write_jsonl(results, path)

    write_summary(summary_for(results), tmp_path / "summary.json")

    records = read_jsonl(path)
    assert len(records) == len(results)
    assert all("id" in record for record in records)


def test_a_record_declares_the_schema_it_was_written_in():
    record = record_for(make("SURVIVED"))

    assert record["schema"] == RECORD_SCHEMA


def test_older_records_are_upgraded_when_they_are_read(tmp_path):
    # A results.jsonl written before the acceptance keys existed. Reading it
    # must produce today's shape, so a consumer -- moonbuggy's own human
    # report included -- can index every key instead of guessing with .get().
    path = tmp_path / "results.jsonl"
    legacy = {
        "id": "sample/inventory.py:9:comparison_swap:0",
        "status": "SURVIVED",
        "file": "sample/inventory.py",
        "line": 9,
        "operator": "comparison_swap",
        "category": "comparison_swap",
        "nearest_test": None,
        "tests_run": 2,
        "duration": 0.1,
        "module_level": False,
        "suppressed": False,
        "original": "a > 0",
        "mutated": "a >= 0",
        "diff": "- a > 0\n+ a >= 0",
    }
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    record = read_jsonl(path)[0]

    assert record["schema"] == 1
    assert record["accepted"] is False
    assert record["accept_reason"] is None
    # Every later schema's keys, not just the next one's.
    assert record["logging_call"] is False
    # Nothing the old file did say is overwritten.
    assert record["status"] == "SURVIVED"


def test_a_schema_2_record_gains_only_what_schema_3_added(tmp_path):
    # The half-way case: a file written after the ledger but before the
    # logging policy. It says nothing about logging calls, and "false" is the
    # honest fill -- that version recognised none of them.
    path = tmp_path / "results.jsonl"
    result = Result(make("SURVIVED").mutant, "SURVIVED", tests_run=3, duration=0.12)
    older = {**record_for(result), "schema": 2}
    del older["logging_call"]
    path.write_text(json.dumps(older) + "\n", encoding="utf-8")

    record = read_jsonl(path)[0]

    assert record["schema"] == 2
    assert record["logging_call"] is False
    assert record["accept_reason"] is None


def test_a_current_record_round_trips_unchanged(tmp_path):
    path = tmp_path / "results.jsonl"
    result = Result(make("SURVIVED").mutant, "SURVIVED", tests_run=3, duration=0.12)
    write_jsonl([result], path)

    assert read_jsonl(path)[0] == record_for(result)
