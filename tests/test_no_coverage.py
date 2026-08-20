"""`NO_COVERAGE`: no test reaches the line, which is not the same finding.

A mutant whose covering set is empty used to be reported `SURVIVED` with
`tests_run=0`. Both are findings, but they are different ones with different
fixes -- a survivor means the assertions are weak, an uncovered line means
nothing exercises it at all -- and the survivor list is the tool's primary
deliverable, so it should mean one thing.

The two places that decide it are `run_one` (the serial path) and `_plan` (the
batch path). Both are checked here, because a status emitted by only one of
them is a verdict that depends on `--jobs`.
"""

from dataclasses import replace

from moonbuggy.cache import CACHE_VERSION, ResultCache
from moonbuggy.coverage_pass import LineMap
from moonbuggy.mutant import Mutant
from moonbuggy.report import STATUS_KEYWORDS
from moonbuggy.runner import _plan, run_one

UNCOVERED = Mutant(
    id="calc.py:3:constant_int:0",
    module="calc.py",
    line=3,
    operator="constant_int",
    original="return 0",
    mutated="return 1",
)


def empty_map(project_dir):
    """A coverage map that reaches nothing -- every mutant is uncovered."""
    return LineMap({}, [], project_dir)


def test_the_serial_path_reports_no_coverage(tmp_path):
    result = run_one(tmp_path, UNCOVERED, empty_map(tmp_path), 30.0, "python")

    assert result.status == "NO_COVERAGE"
    assert result.tests_run == 0
    assert result.nearest_test is None


def test_the_batch_path_reports_no_coverage(tmp_path):
    plan = _plan(tmp_path, [UNCOVERED], empty_map(tmp_path), None)

    assert plan["to_run"] == []
    assert plan["results"][0].status == "NO_COVERAGE"


def test_it_is_never_handed_to_a_process(tmp_path):
    # Nothing can kill a mutant no test reaches, so there is nothing to run.
    # The status is settled without a fork, exactly as SURVIVED was before it.
    plan = _plan(tmp_path, [UNCOVERED], empty_map(tmp_path), None)

    assert not plan["to_run"]


def test_a_suppressed_uncovered_line_is_still_skipped(tmp_path):
    # SKIPPED wins: the user asked for the exclusion, and an exclusion is not a
    # finding however little covers the line.
    suppressed = replace(UNCOVERED, suppressed=True)

    result = run_one(tmp_path, suppressed, empty_map(tmp_path), 30.0, "python")

    assert result.status == "SKIPPED"


def test_it_is_a_first_class_status_keyword():
    # Every plaintext line begins with one of these, so a keyword the report
    # can emit but the vocabulary does not list is an unparseable line.
    assert "NO_COVERAGE" in STATUS_KEYWORDS


def test_the_cache_version_moved_past_the_rename(tmp_path):
    # Cached records written before the rename hold "SURVIVED" for these
    # mutants, and would replay under the old name for as long as the version
    # matched. Bumping it makes an old file ignored rather than misread.
    assert CACHE_VERSION >= 3

    stale = tmp_path / "cache.json"
    stale.write_text('{"version": 2, "entries": {"k": {"status": "SURVIVED"}}}')

    assert len(ResultCache(stale)) == 0


def test_the_result_is_cached_under_its_own_name(tmp_path):
    (tmp_path / "calc.py").write_text("def f():\n    return 0\n")
    cache = ResultCache(tmp_path / "cache.json")

    run_one(tmp_path, UNCOVERED, empty_map(tmp_path), 30.0, "python", cache=cache)
    replayed = run_one(
        tmp_path, UNCOVERED, empty_map(tmp_path), 30.0, "python", cache=cache
    )

    assert replayed.status == "NO_COVERAGE"
    assert replayed.from_cache
