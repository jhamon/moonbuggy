"""Fast unit tests for `moonbuggy run <id>` and `moonbuggy why <id>`'s parts.

The end-to-end behaviour -- a real coverage pass and a real pytest subprocess
per mutant -- lives in `test_cli.py` under `pytest.mark.slow`. What is here
needs no process: id parsing, target resolution, the structured summaries, the
prediction `why` makes from them, and the argument handling that turns a
pipeline into a list of ids.
"""

import json

import pytest

from moonbuggy.cli import _build_parser, _clean_id, _target_ids
from moonbuggy.mutant import Mutant, make_id, parse_id
from moonbuggy.runner import Result
from moonbuggy.verify import (
    Explanation,
    Verification,
    VerifyError,
    resolve_targets,
)

MODULE = """\
BULK = 10


def is_bulk(quantity):
    return quantity >= BULK
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "shipping.py").write_text(MODULE)
    return tmp_path


def test_ids_round_trip_through_parse():
    mutant_id = make_id("app/pricing.py", 14, "comparison_swap", 0)
    assert mutant_id == "app/pricing.py:14:comparison_swap:0"
    assert parse_id(mutant_id) == ("app/pricing.py", 14, "comparison_swap", 0)


def test_a_windows_drive_letter_survives_the_round_trip():
    # rsplit, not split: the module path is allowed to contain a colon.
    assert parse_id(r"C:\src\pricing.py:14:comparison_swap:0") == (
        r"C:\src\pricing.py",
        14,
        "comparison_swap",
        0,
    )


@pytest.mark.parametrize(
    "bad",
    ["", "no-such-mutant", "app/pricing.py:14", "app/pricing.py:x:op:0", ":14:op:0"],
)
def test_a_non_id_parses_as_none_rather_than_raising(bad):
    assert parse_id(bad) is None


def test_resolve_targets_finds_the_mutant_the_id_names(project):
    targets = resolve_targets(project, ["shipping.py:5:comparison_swap:0"])

    assert len(targets) == 1
    assert targets[0].module == "shipping.py"
    assert targets[0].line == 5
    assert targets[0].original == "return quantity >= BULK"


def test_resolve_targets_reads_each_module_once_for_many_ids(project):
    targets = resolve_targets(
        project, ["shipping.py:5:comparison_swap:0", "shipping.py:1:constant_int:0"]
    )

    assert [t.line for t in targets] == [5, 1]


def test_a_malformed_id_says_what_an_id_looks_like(project):
    with pytest.raises(VerifyError, match="is not a mutant id"):
        resolve_targets(project, ["shipping.py:5"])


def test_an_id_for_a_missing_module_names_the_module(project):
    with pytest.raises(VerifyError, match="cannot read nowhere.py"):
        resolve_targets(project, ["nowhere.py:5:comparison_swap:0"])


def test_an_id_the_module_no_longer_produces_suggests_a_rerun(project):
    # The shape of a stale id: the line moved under the user after an edit.
    with pytest.raises(VerifyError, match="run moonbuggy again"):
        resolve_targets(project, ["shipping.py:99:comparison_swap:0"])


def _verification(status="SURVIVED", reason=None):
    mutant = Mutant(
        id="shipping.py:5:comparison_swap:0",
        module="shipping.py",
        line=5,
        operator="comparison_swap",
        original="return quantity >= BULK",
        mutated="return quantity > BULK",
    )
    result = Result(mutant, status, tests_run=2, duration=0.5, nearest_test="t.py::a")
    return Verification(result, ("t.py::a", "t.py::b"), (), reason)


def test_summary_is_json_serialisable_data():
    summary = _verification().summary()

    assert summary["id"] == "shipping.py:5:comparison_swap:0"
    assert summary["status"] == "SURVIVED"
    assert summary["selected"] == ["t.py::a", "t.py::b"]
    assert summary["failed"] == []
    assert summary["accepted"] is False
    assert summary["accept_reason"] is None


def test_summary_carries_an_acceptance_without_changing_the_status():
    summary = _verification(reason="cache size only").summary()

    assert summary["status"] == "SURVIVED"
    assert summary["accepted"] is True
    assert summary["accept_reason"] == "cache size only"


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("app/p.py:14:op:0", "app/p.py:14:op:0"),
        ("  app/p.py:14:op:0  ", "app/p.py:14:op:0"),
        ("id=app/p.py:14:op:0", "app/p.py:14:op:0"),
        (
            "SURVIVED  app/p.py:14 op line=14 nearest_test=- tests_run=3 "
            "id=app/p.py:14:op:0",
            "app/p.py:14:op:0",
        ),
    ],
)
def test_clean_id_accepts_every_shape_a_pipeline_produces(token, expected):
    assert _clean_id(token) == expected


def test_target_ids_drops_blanks_and_duplicates():
    assert _target_ids(["a:1:op:0", "a:1:op:0", "  "]) == ["a:1:op:0"]


def test_stdin_ids_are_read_for_a_dash(monkeypatch):
    import io
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO("a:1:op:0\n\nb:2:op:0\n"))

    assert _target_ids(["-"]) == ["a:1:op:0", "b:2:op:0"]


def test_run_is_a_subcommand_and_bare_moonbuggy_is_still_a_full_run():
    parser = _build_parser()

    # `moonbuggy run <id>` must not shadow bare `moonbuggy`, which is still the
    # full run -- hence the internal name `run-one` for the subcommand.
    assert parser.parse_args([]).command not in {"run-one", "show", "accept"}
    assert parser.parse_args(["run", "a:1:op:0"]).command == "run-one"
    assert parser.parse_args(["run", "a:1:op:0"]).mutant_id == ["a:1:op:0"]


def test_run_honours_the_flags_a_full_run_honours():
    args = _build_parser().parse_args(
        ["run", "a:1:op:0", "--pytest-arg=-W", "--timeout", "5", "-n", "2"]
    )

    assert args.pytest_arg == ["-W"]
    assert args.timeout == 5.0
    assert args.workers == 2


def _explanation(**overrides):
    mutant = Mutant(
        id="shipping.py:5:comparison_swap:0",
        module="shipping.py",
        line=5,
        operator="comparison_swap",
        original="return quantity >= BULK",
        mutated="return quantity > BULK",
    )
    fields = {
        "mutant": mutant,
        "selected": ("t.py::a", "t.py::b"),
        "selection": "coverage",
        "cache_key": "deadbeef",
        "fingerprint_inputs": {"pytest_args": [], "timeout": 30.0, "python": "py"},
    }
    fields.update(overrides)
    return Explanation(**fields)


def test_explanation_summary_is_json_serialisable_data():
    summary = _explanation().summary()

    assert json.loads(json.dumps(summary)) == summary
    assert summary["id"] == "shipping.py:5:comparison_swap:0"
    assert summary["selection"] == "coverage"
    assert summary["selected"] == ["t.py::a", "t.py::b"]
    # The same name the result line uses, which is the token being explained.
    assert summary["tests_run"] == 2
    assert summary["cache_hit"] is False
    assert summary["run_inputs"]["timeout"] == 30.0


def test_an_empty_selection_predicts_no_coverage():
    explanation = _explanation(selected=())

    assert explanation.next_run == "no_coverage"
    assert explanation.summary()["tests_run"] == 0


def test_a_cache_hit_predicts_a_replay_rather_than_a_measurement():
    explanation = _explanation(
        cached={"status": "SURVIVED", "tests_run": 2, "nearest_test": None}
    )

    assert explanation.next_run == "cache"
    assert explanation.summary()["cached_status"] == "SURVIVED"


def test_the_prediction_follows_the_planner_s_order_of_decisions():
    # `runner._plan` settles suppressed and flaky mutants BEFORE it looks in
    # the cache, so a hit does not describe what would happen to either.
    hit = {"status": "SURVIVED", "tests_run": 2, "nearest_test": None}
    suppressed = Mutant(
        id="s.py:1:constant_int:0",
        module="s.py",
        line=1,
        operator="constant_int",
        original="x = 1",
        mutated="x = 2",
        suppressed=True,
    )

    assert _explanation(mutant=suppressed, cached=hit).next_run == "skipped"
    assert _explanation(flaky=("t.py::a",), cached=hit).next_run == "suspicious"


def test_cache_covers_names_the_module_and_every_selected_test_file():
    explanation = _explanation(selected=("b.py::two", "a.py::one", "a.py::three"))

    # What the reader has to edit to invalidate a stale hit -- deduplicated,
    # because two node ids in one file are one file.
    assert explanation.cache_covers == ("a.py", "b.py", "shipping.py")


def test_a_module_level_mutant_says_the_whole_suite_was_selected():
    module_level = Mutant(
        id="s.py:1:constant_int:0",
        module="s.py",
        line=1,
        operator="constant_int",
        original="BULK = 10",
        mutated="BULK = 11",
        module_level=True,
    )
    explanation = _explanation(mutant=module_level, selection="module_level")

    assert explanation.summary()["selection"] == "module_level"
    assert explanation.next_run == "measure"


def test_why_is_a_subcommand_taking_the_same_ids_run_takes():
    parser = _build_parser()
    args = parser.parse_args(["why", "a:1:op:0", "--json"])

    assert args.command == "why"
    assert args.mutant_id == ["a:1:op:0"]
    assert args.json is True
    # No probe by default: `why` answers without measuring, and a probe is a
    # measurement.
    assert args.flaky_probe == 0
