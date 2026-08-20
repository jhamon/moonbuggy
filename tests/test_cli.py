"""Criteria E3/E4/E7, F1/F3/F4, H2/H5 -- the tool as an evaluator meets it.

The zero-config test deliberately runs against a project generated here rather
than against tests/fixtures/sample_project. Passing on the fixture proves only
that moonbuggy works on the one layout it was developed against; criterion H2
asks whether it works on a project it has never seen.
"""

import builtins
import json
import subprocess
import sys
from pathlib import Path

import pytest

from moonbuggy import terminal
from moonbuggy.cli import main
from moonbuggy.report import RECORD_SCHEMA, STATUS_KEYWORDS

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"
SAMPLE_PROJECT = "tests/fixtures/sample_project"

pytestmark = pytest.mark.slow

PROJECT = """\
def clamp(value, ceiling):
    if value > ceiling:
        return ceiling
    return value


def total(values):
    running = 0
    for value in values:
        running += value
    return running
"""

TESTS = """\
from calc import clamp, total


def test_clamp_passes_small_values_through():
    assert clamp(1, 10) == 1


def test_clamp_caps_large_values():
    assert clamp(99, 10) == 10


def test_total_sums():
    assert total([1, 2, 3]) == 6
"""


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
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if expect is not None:
        assert proc.returncode == expect, (
            f"{proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        )
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
        assert record["status"] in STATUS_KEYWORDS


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

    records = [
        json.loads(line)
        for line in out.joinpath("results.jsonl").read_text().splitlines()
    ]
    expected = sum(1 for r in records if r["status"] == "SURVIVED")
    grepped = sum(
        1
        for line in out.joinpath("results.txt").read_text().splitlines()
        if line.startswith("SURVIVED")
    )

    assert grepped == expected


def test_show_prints_the_diff_for_a_mutant_id(throwaway):
    # Criterion E7: the diff is not in the plaintext view, so there must be a
    # lookup that produces it.
    moonbuggy(cwd=throwaway)
    records = [
        json.loads(line)
        for line in (throwaway / ".moonbuggy" / "results.jsonl")
        .read_text()
        .splitlines()
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
    (throwaway / "calc.py").write_text(
        PROJECT.replace("running = 0", "running = 0  # touched")
    )

    proc = moonbuggy(cwd=throwaway)

    cached = int(proc.stderr.split("cached=")[1].split()[0])
    assert cached == 0, "editing the mutated module must invalidate its entries"


def test_users_own_test_suite_still_passes_afterwards(throwaway):
    """Criterion D7, and the regression test for the worst bug found so far.

    moonbuggy's loader inherits SourceFileLoader, which writes compiled bytecode
    to __pycache__ as a side effect of importing -- stamped with the real file's
    mtime and size, so a mutated .pyc looks valid for the unmutated source. The
    user's next plain `pytest` then runs mutations they never asked for, with
    every .py file byte-identical and nothing pointing at moonbuggy.

    Hashing source files does not catch this. Running the user's suite does.
    """
    before = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=throwaway,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert before.returncode == 0, before.stdout

    moonbuggy(cwd=throwaway)

    after = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=throwaway,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert after.returncode == 0, (
        "The project's own suite fails after a moonbuggy run:\n" + after.stdout
    )


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


def test_a_bare_list_of_operator_names_is_still_an_exact_set(throwaway):
    """The compatibility promise of #15, end to end. Tiers and `+` are syntax
    layered on top of this; a bare list must not have acquired a tier's
    expanding behaviour."""
    moonbuggy("--no-cache", "--operators", "comparison_swap,boundary", cwd=throwaway)

    assert {r["operator"] for r in _records(throwaway)} <= {
        "comparison_swap",
        "boundary",
    }


def test_a_tier_selection_reports_its_resolved_set(throwaway):
    """`--operators all` is a claim about which operators ran, and `"all"` in
    the summary would not be one a consumer could act on."""
    moonbuggy("--no-cache", "--operators", "all", cwd=throwaway)

    config = json.loads((throwaway / ".moonbuggy" / "summary.json").read_text())[
        "config"
    ]

    assert config["operators_selector"] == "all"
    assert "comparison_swap" in config["operators"]
    assert config["operators"] == sorted(config["operators"])


def test_a_misspelled_operator_name_fails_instead_of_running_nothing(throwaway):
    """It used to produce a zero-mutant run that exited 0 -- a typo that reads
    exactly like a passing suite."""
    proc = moonbuggy("--operators", "compaison_swap", cwd=throwaway, expect=2)

    assert "comparison_swap" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_an_empty_tier_says_so_rather_than_reporting_a_clean_run(throwaway):
    """No operator declares itself `deep` in this version. Running nothing and
    exiting 0 would be the worst possible answer."""
    proc = moonbuggy("--operators", "deep", cwd=throwaway, expect=2)

    assert "deep" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_the_operators_listing_runs_outside_a_project(tmp_path):
    """It reads the registry and nothing else, so it must not require a pytest
    project -- an agent enumerating operators has not chosen a project yet."""
    proc = moonbuggy("operators", cwd=tmp_path, expect=0)

    assert "comparison_swap" in proc.stdout
    listing = json.loads(moonbuggy("operators", "--json", cwd=tmp_path).stdout)
    assert {entry["name"] for entry in listing["operators"]} == set(
        listing["tiers"]["all"]
    )


def test_exclude_filters_files(throwaway):
    (throwaway / "helper.py").write_text("def double(x):\n    return x * 2\n")

    moonbuggy("--no-cache", cwd=throwaway)
    with_helper = {r["file"] for r in _records(throwaway)}

    moonbuggy("--no-cache", "--exclude", "helper", cwd=throwaway)
    without = {r["file"] for r in _records(throwaway)}

    assert "helper.py" in with_helper
    assert "helper.py" not in without


def test_human_footer_names_a_non_default_output_dir(throwaway, capsys):
    """The human footer's artifact path must track `--output-dir`.

    Reproduces the reported bug: `render_footer` used to hardcode
    `.moonbuggy/results.jsonl`, so a run with a non-default `--output-dir`
    printed a footer naming a path that was never written. This runs through
    `main` in-process (rather than the `moonbuggy` subprocess helper) so it
    can capture stdout with `capsys`, and asserts the human footer names
    exactly `custom-out/results.jsonl` -- the same relative path the
    agent-mode summary would use for this run, via the same `_display_path`
    call.
    """
    code = main(
        [
            "--project",
            str(throwaway),
            "--output-dir",
            "custom-out",
            "--report",
            "human",
            "--quiet",
        ]
    )
    lines = capsys.readouterr().out.splitlines()

    assert code in (0, 1)
    assert (throwaway / "custom-out" / "results.jsonl").exists()
    assert lines[1] == "Full records: custom-out/results.jsonl"


def test_human_report_renders_on_a_non_tty(tmp_path, capsys):
    """A human redirecting to a file still wants the human format.

    Without this, `moonbuggy | less` silently gets the agent format -- which
    is the trap TTY detection alone walks into. These run the real mutation
    engine against the shared fixture, so they belong in this slow, e2e
    module rather than test_cli_unit.py.
    """
    project = "tests/fixtures/sample_project"
    code = main(
        ["--project", project, "--output-dir", str(tmp_path), "--report", "human"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "SURVIVED  comparison_swap" in out
    assert "\x1b" not in out  # no escapes on a non-tty


def test_agent_report_is_the_default_off_a_tty(tmp_path, capsys):
    main(
        [
            "--project",
            "tests/fixtures/sample_project",
            "--output-dir",
            str(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert "nearest_test=" in out  # the key=value agent format


def test_quiet_in_human_mode_prints_the_footer_and_nothing_else(tmp_path, capsys):
    """--quiet in human mode means the footer, not silence.

    The agent path still prints its stderr summary under --quiet, so without
    the footer here quiet-human would be the only mode that reports nothing at
    all -- a run that produced zero bytes and an exit code.
    """
    code = main(
        [
            "--project",
            SAMPLE_PROJECT,
            "--output-dir",
            str(tmp_path),
            "--report",
            "human",
            "--quiet",
        ]
    )
    lines = capsys.readouterr().out.splitlines()

    assert code == 1
    assert len(lines) == 3
    # The denominator is the point of the score, and it is here in full.
    assert lines[0].endswith("-- 21/28 killed, 75%")
    # tmp_path is absolute, so it discards project_dir under `/` (H5) and the
    # footer names the absolute path rather than a shortened relative one --
    # the same path the agent-mode summary would name for this run.
    assert lines[1] == f"Full records: {tmp_path / 'results.jsonl'}"
    # The fixture has both findings: survivors, and inventory.py:15, which no
    # test reaches. The closing line names both rather than only the first.
    assert lines[2] == "exit 1 -- survivors, and lines no test reaches"


UNCOVERED_PROJECT = """\
def used(value):
    return value + 1


def never_called(value):
    return value * 2
"""

UNCOVERED_TESTS = """\
from lib import used


def test_used():
    assert used(1) == 2
"""


def test_an_unreached_line_is_reported_as_no_coverage(tmp_path):
    """End to end: the status reaches the artifacts, and keeps the exit code.

    `never_called` is mutated but no test executes it. Before NO_COVERAGE
    existed this run was a SURVIVED with `tests_run=0`; the finding has not
    changed, only its name -- and a CI gate keyed on the exit code must not
    notice the difference.
    """
    (tmp_path / "lib.py").write_text(UNCOVERED_PROJECT)
    (tmp_path / "test_lib.py").write_text(UNCOVERED_TESTS)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
    )

    proc = moonbuggy(cwd=tmp_path, expect=1)

    records = [
        json.loads(line)
        for line in (tmp_path / ".moonbuggy" / "results.jsonl").read_text().splitlines()
    ]
    uncovered = [r for r in records if r["status"] == "NO_COVERAGE"]

    assert uncovered, [r["status"] for r in records]
    assert all(r["tests_run"] == 0 and r["nearest_test"] is None for r in uncovered)
    # Not a survivor any more, and not hidden either: still exit 1 (asserted by
    # `expect=1` above), still greppable, under its own keyword.
    assert not [r for r in records if r["status"] == "SURVIVED"]
    text = (tmp_path / ".moonbuggy" / "results.txt").read_text()
    assert [line for line in text.splitlines() if line.startswith("NO_COVERAGE")]
    assert "NO_COVERAGE=" in proc.stderr


DELETION_PROJECT = """\
def scaled(value):
    factor = 2
    return value * factor
"""

DELETION_TESTS = """\
from lib import scaled


def test_scaled():
    assert scaled(3) == 6
"""


def test_a_deep_tier_run_separates_crash_kills_from_assertion_kills(tmp_path):
    """End to end: `statement_deletion` is off by default, on under
    `--operators`, and its crash-kills are reported under their own keyword.

    Deleting `factor = 2` leaves `return value * factor` raising `NameError`.
    The suite notices -- it is a kill -- but nothing about it says the suite
    *checks* the multiplication, which is the whole distinction. Deleting the
    `return` instead makes `scaled` return None and the assertion objects, so
    the same file produces one of each.
    """
    (tmp_path / "lib.py").write_text(DELETION_PROJECT)
    (tmp_path / "test_lib.py").write_text(DELETION_TESTS)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
    )

    default_run = moonbuggy(cwd=tmp_path, expect=0)
    default_text = (tmp_path / ".moonbuggy" / "results.txt").read_text()
    assert "statement_deletion" not in default_text

    # A kill is not a finding, so this still exits 0.
    proc = moonbuggy("--operators", "+deep", cwd=tmp_path, expect=0)

    records = [
        json.loads(line)
        for line in (tmp_path / ".moonbuggy" / "results.jsonl").read_text().splitlines()
    ]
    by_id = {r["id"]: r["status"] for r in records}

    assert by_id["lib.py:2:statement_deletion:0"] == "KILLED_BY_ERROR"
    assert by_id["lib.py:3:statement_deletion:0"] == "KILLED"
    text = (tmp_path / ".moonbuggy" / "results.txt").read_text()
    assert [line for line in text.splitlines() if line.startswith("KILLED_BY_ERROR")]
    assert "KILLED_BY_ERROR=1" in proc.stderr
    # The default run above had no deep operator in it at all.
    assert "KILLED_BY_ERROR=0" in default_run.stderr


def test_a_non_tty_run_puts_no_bare_survived_line_on_stderr(tmp_path, capsys):
    """Survivor scrollback belongs to the live region, and only to it.

    A disabled region still commits whatever `log` is given, so an unguarded
    survivor message emitted `SURVIVED  path:line` on a piped run -- a line
    opening with a frozen contract keyword and carrying none of its key=value
    tokens, which `moonbuggy 2>&1 | grep SURVIVED` would return as a
    malformed duplicate.
    """
    main(["--project", SAMPLE_PROJECT, "--output-dir", str(tmp_path)])
    err = capsys.readouterr().err

    assert [line for line in err.splitlines() if line.startswith("SURVIVED")] == []


def test_human_mode_off_a_tty_leaves_exactly_one_durable_final_line(tmp_path, capsys):
    """There is no live region to watch, so the ending is committed, once.

    Twice was the bug: the last result's milestone line and the durable line
    `close` commits carry word-for-word the same text, so a run long enough to
    reach a milestone ended by saying the same thing twice.
    """
    main(
        [
            "--project",
            SAMPLE_PROJECT,
            "--output-dir",
            str(tmp_path),
            "--report",
            "human",
        ]
    )
    err = capsys.readouterr().err

    settled = [line for line in err.splitlines() if " settled" in line]
    assert len(settled) == 1
    assert settled[0].startswith("moonbuggy: 29/29 settled -- ")


def test_agent_mode_stderr_gains_no_progress_narration(tmp_path, capsys):
    """Progress belongs to the human report, and only to it.

    The agent format is frozen, and that includes what a plain `moonbuggy`
    run puts on stderr: the two preamble lines and the summary. An agent has
    no use for narration it would then have to filter out.
    """
    main(["--project", SAMPLE_PROJECT, "--output-dir", str(tmp_path)])
    err = capsys.readouterr().err.splitlines()

    assert [line for line in err if "settled" in line] == []
    assert err[0] == "moonbuggy: 29 mutants across 6 files"
    assert err[1] == "moonbuggy: running coverage pass..."
    assert len(err) == 3
    assert err[2].startswith("moonbuggy: KILLED=")


def test_no_bare_print_runs_while_the_live_region_is_open(tmp_path, monkeypatch):
    """The single-writer invariant, as a mechanism rather than as care.

    While the region is open, exactly one object writes to the terminal and
    everything else routes through `log`. A bare `print` in between would
    scroll straight through the live line and leave a fragment of it behind.
    """
    state = {"open": False}
    escaped: list[str] = []
    real_print = builtins.print
    real_init = terminal.LiveRegion.__init__
    real_close = terminal.LiveRegion.close

    def watching_print(*args, **kwargs):
        if state["open"]:
            escaped.append(" ".join(str(arg) for arg in args))
        real_print(*args, **kwargs)

    def opening_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        state["open"] = True

    def closing_close(self, final=None):
        state["open"] = False
        real_close(self, final)

    monkeypatch.setattr(terminal.LiveRegion, "__init__", opening_init)
    monkeypatch.setattr(terminal.LiveRegion, "close", closing_close)
    monkeypatch.setattr(builtins, "print", watching_print)

    main(["--project", SAMPLE_PROJECT, "--output-dir", str(tmp_path)])

    assert escaped == []


def test_the_agent_artifacts_are_unchanged_byte_for_byte(tmp_path):
    """Constraint 1, pinned against a real run rather than a synthetic record.

    `results.txt` is compared whole. `results.jsonl` carries a wall-clock
    `duration` per mutant, which is the one field a rerun cannot reproduce, so
    it is normalised to 0.0 and every other byte of every line is compared.
    """
    main(["--project", SAMPLE_PROJECT, "--output-dir", str(tmp_path)])

    text = (tmp_path / "results.txt").read_text(encoding="utf-8")
    assert text == (GOLDEN_DIR / "sample_project.results.txt").read_text(
        encoding="utf-8"
    )

    produced = [
        json.dumps({**json.loads(line), "duration": 0.0}, sort_keys=True)
        for line in (tmp_path / "results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    expected = (
        (GOLDEN_DIR / "sample_project.results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert produced == expected


def _records(project):
    path = project / ".moonbuggy" / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _records_without_timing(project):
    return [
        {k: v for k, v in record.items() if k != "duration"}
        for record in _records(project)
    ]


ADDED = "\n\ndef doubled(value):\n    return value * 2\n"


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def versioned(throwaway):
    """The throwaway project committed to git, with one new function on top.

    The added function is uncommitted on purpose: it is the state you are in
    when a fast run is worth most, and it is what `--since` has to see.
    """
    _git(throwaway, "init", "-q", "-b", "main")
    _git(throwaway, "config", "user.email", "test@example.com")
    _git(throwaway, "config", "user.name", "Test")
    _git(throwaway, "add", "-A")
    _git(throwaway, "commit", "-qm", "first")
    calc = throwaway / "calc.py"
    calc.write_text(calc.read_text() + ADDED)
    return throwaway


def test_since_mutates_only_the_changed_lines(versioned):
    changed_line = len((versioned / "calc.py").read_text().splitlines())

    proc = moonbuggy("--since", "main", cwd=versioned)

    records = [
        json.loads(line)
        for line in (versioned / ".moonbuggy" / "results.jsonl")
        .read_text()
        .splitlines()
    ]
    assert records, proc.stderr
    assert {r["line"] for r in records} == {changed_line}
    assert {r["file"] for r in records} == {"calc.py"}


def test_since_is_a_filter_not_a_different_mutant_set(versioned):
    # The scoped mutants must be exactly the full run's mutants for those
    # lines -- same ids, same operators. A `--since` that generated its own
    # mutants would report verdicts a full run could never reproduce.
    moonbuggy("--since", "main", "--output-dir", "scoped", cwd=versioned)
    moonbuggy("--output-dir", "full", cwd=versioned)

    def ids(where):
        return {
            json.loads(line)["id"]
            for line in (versioned / where / "results.jsonl").read_text().splitlines()
        }

    scoped, full = ids("scoped"), ids("full")
    assert scoped
    assert scoped < full


def test_a_diff_scoped_run_reuses_the_full_run_cache(versioned):
    # Reaching a mutant through a diff scope cannot change its answer, so
    # `--since` is deliberately not part of the cache fingerprint. If it were,
    # every PR run would start cold.
    first = moonbuggy(cwd=versioned)
    assert "cached=0" in first.stderr

    second = moonbuggy("--since", "main", cwd=versioned)

    assert int(second.stderr.split("cached=")[1].split()[0]) > 0


def test_the_report_says_the_run_was_diff_scoped(versioned):
    proc = moonbuggy("--since", "main", "--report", "human", cwd=versioned)

    assert "Diff-scoped: only lines changed since main" in proc.stdout
    assert "(diff-scoped since main)" in proc.stdout


def test_a_change_with_no_source_lines_is_a_pass_not_a_failure(versioned):
    # A pull request that touched only docs has nothing to mutate. Exiting 2
    # for it would teach everyone to stop running the gate.
    (versioned / "calc.py").write_text(
        (versioned / "calc.py").read_text().replace(ADDED, "")
    )
    (versioned / "README.md").write_text("docs only\n")

    proc = moonbuggy("--since", "main", cwd=versioned, expect=0)

    assert "nothing to mutate" in proc.stderr
    # Empty artifacts, not the previous run's verdicts: stale results are the
    # one outcome worse than none.
    assert (versioned / ".moonbuggy" / "results.jsonl").read_text() == ""


def test_since_composes_with_exclude(versioned):
    proc = moonbuggy("--since", "main", "--exclude", "calc", cwd=versioned, expect=0)

    assert "nothing to mutate" in proc.stderr


def _findings(project):
    """Every finding from the last run, in file order."""
    return [
        json.loads(line)
        for line in (project / ".moonbuggy" / "results.jsonl").read_text().splitlines()
        if json.loads(line)["status"] in ("SURVIVED", "NO_COVERAGE")
    ]


@pytest.fixture
def triaged(throwaway):
    """A project whose every finding has been accepted, the way triage ends."""
    moonbuggy(cwd=throwaway)
    findings = _findings(throwaway)
    assert findings, "the fixture is meant to leave findings to accept"
    for record in findings:
        moonbuggy(
            "accept",
            record["id"],
            "--reason",
            "reviewed: equivalent for every reachable input",
            cwd=throwaway,
            expect=0,
        )
    return throwaway


def test_an_accepted_run_can_be_green_under_the_gate(triaged):
    # The end state the ledger exists for: triage happened, the decisions are
    # on disk, and CI can be a gate rather than an audit somebody reads.
    proc = moonbuggy("--fail-on-unexplained", cwd=triaged, expect=0)

    assert "unexplained" in proc.stdout + proc.stderr


def test_the_same_run_without_the_flag_still_exits_1(triaged):
    # Adding a ledger must never silently turn an existing red gate green.
    moonbuggy(cwd=triaged, expect=1)


def test_accepted_mutants_still_run_and_are_still_reported(triaged):
    moonbuggy("--fail-on-unexplained", cwd=triaged, expect=0)

    findings = _findings(triaged)

    assert findings, "an accepted mutant must still be executed and recorded"
    assert all(record["accepted"] for record in findings)
    assert all(record["accept_reason"] for record in findings)


def test_an_edit_to_the_accepted_line_makes_the_gate_red_again(triaged):
    # Drift. The acceptance was a decision about code that no longer exists,
    # and honouring it silently is how a real regression sneaks in behind it.
    # Rewritten, not broken: the suite still passes and the mutant is still a
    # survivor. What changed is the line the acceptance was a decision about.
    source = triaged / "calc.py"
    source.write_text(
        source.read_text().replace("if value > ceiling:", "if ceiling < value:")
    )

    proc = moonbuggy("--fail-on-unexplained", "--no-cache", cwd=triaged, expect=1)

    assert "stale" in proc.stdout + proc.stderr


def test_an_insertion_above_does_not_lose_the_acceptance(triaged):
    # Id stability. Every id below the insertion shifts; the decisions must
    # not, or an unrelated edit silently empties the ledger.
    source = triaged / "calc.py"
    source.write_text(
        '"""A docstring nobody had written yet."""\n\n' + source.read_text()
    )

    moonbuggy("--fail-on-unexplained", "--no-cache", cwd=triaged, expect=0)


def test_accept_list_and_remove_round_trip(throwaway):
    moonbuggy(cwd=throwaway)
    mutant_id = _findings(throwaway)[0]["id"]
    moonbuggy("accept", mutant_id, "-r", "equivalent", cwd=throwaway, expect=0)

    listed = moonbuggy("accept", "--list", cwd=throwaway, expect=0)
    assert mutant_id in listed.stdout

    moonbuggy("accept", "--remove", mutant_id, cwd=throwaway, expect=0)
    assert mutant_id not in moonbuggy("accept", "--list", cwd=throwaway).stdout


# --- `moonbuggy run <id>`: the fix-verify loop -------------------------------


def _status_of(project, mutant_id):
    """The status the last full run recorded for one mutant id."""
    for line in (project / ".moonbuggy" / "results.jsonl").read_text().splitlines():
        record = json.loads(line)
        if record["id"] == mutant_id:
            return record["status"]
    raise AssertionError(f"no record for {mutant_id}")


def _poison_cache(project, status):
    """Rewrite every cached verdict, so a served answer is visibly a lie."""
    path = project / ".moonbuggy" / "cache.json"
    payload = json.loads(path.read_text())
    for entry in payload["entries"].values():
        entry["status"] = status
    path.write_text(json.dumps(payload))


KILLING_TEST = """\
from lib import never_called


def test_never_called_is_called_after_all():
    assert never_called(3) == 6
"""


@pytest.fixture
def uncovered(tmp_path):
    """A project with a function no test reaches -- the NO_COVERAGE shape."""
    (tmp_path / "lib.py").write_text(UNCOVERED_PROJECT)
    (tmp_path / "test_lib.py").write_text(UNCOVERED_TESTS)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
    )
    return tmp_path


def test_run_names_the_tests_it_selected_and_the_ones_that_failed(throwaway):
    # The two things `moonbuggy show` cannot say, because it never ran anything.
    moonbuggy(cwd=throwaway)
    killed = next(
        json.loads(line)
        for line in (throwaway / ".moonbuggy" / "results.jsonl")
        .read_text()
        .splitlines()
        if json.loads(line)["status"] == "KILLED"
    )

    proc = moonbuggy("run", "--report", "human", killed["id"], cwd=throwaway, expect=0)

    assert f"id           {killed['id']}" in proc.stdout
    assert "status       KILLED" in proc.stdout
    assert "selected     test_calc.py::" in proc.stdout
    assert "failed       test_calc.py::" in proc.stdout
    assert "  - " in proc.stdout and "  + " in proc.stdout


def test_run_reports_no_coverage_rather_than_survived(uncovered):
    # A mutant no test reaches is a finding under its own keyword here too --
    # exit 1, and not the word `SURVIVED`, which would send the reader looking
    # for an assertion to strengthen instead of a test to write.
    proc = moonbuggy("run", "lib.py:6:constant_int:0", cwd=uncovered, expect=1)

    assert proc.stdout.startswith("NO_COVERAGE")
    assert "tests_run=0" in proc.stdout


def test_run_sees_a_new_test_without_a_full_run(uncovered):
    """The whole point: write the test, ask whether it kills that mutant.

    The mutant is NO_COVERAGE until the test exists and KILLED the moment it
    does, and the answer arrives without re-running the other mutants or
    rewriting the run's artifacts.
    """
    moonbuggy(cwd=uncovered, expect=1)
    mutant_id = "lib.py:6:constant_int:0"
    assert _status_of(uncovered, mutant_id) == "NO_COVERAGE"

    (uncovered / "test_never.py").write_text(KILLING_TEST)
    proc = moonbuggy("run", mutant_id, cwd=uncovered, expect=0)

    assert proc.stdout.startswith("KILLED")
    # results.jsonl is the record of a run, and no run has happened since.
    assert _status_of(uncovered, mutant_id) == "NO_COVERAGE"
    assert "results.jsonl is unchanged" in proc.stderr


def test_run_re_measures_instead_of_serving_the_cache(throwaway):
    """A cached verdict must never answer the question `run` exists to ask.

    The cache is poisoned first and a full run is served the poison, which is
    what makes the rest of this a test rather than a coincidence.
    """
    moonbuggy(cwd=throwaway)
    killed_id = next(
        json.loads(line)["id"]
        for line in (throwaway / ".moonbuggy" / "results.jsonl")
        .read_text()
        .splitlines()
        if json.loads(line)["status"] == "KILLED"
    )
    _poison_cache(throwaway, "SURVIVED")

    moonbuggy(cwd=throwaway, expect=1)
    assert _status_of(throwaway, killed_id) == "SURVIVED"

    proc = moonbuggy("run", killed_id, cwd=throwaway, expect=0)
    assert proc.stdout.startswith("KILLED")

    # And the fresh verdict is kept, so the next full run does not pay for it
    # again -- and is not served the stale answer either.
    moonbuggy(cwd=throwaway, expect=1)
    assert _status_of(throwaway, killed_id) == "KILLED"


def test_run_takes_the_survivor_set_on_stdin(throwaway):
    # The workflow from the issue: re-run everything still outstanding, in one
    # command, after a round of new tests.
    moonbuggy(cwd=throwaway)
    findings = (throwaway / ".moonbuggy" / "results.txt").read_text().splitlines()
    piped = "\n".join(
        line for line in findings if line.startswith(("SURVIVED", "NO_COVERAGE"))
    )

    proc = subprocess.run(
        [sys.executable, "-m", "moonbuggy.cli", "run", "-"],
        cwd=throwaway,
        input=piped,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert proc.returncode == 1, proc.stderr
    assert len(proc.stdout.splitlines()) == len(piped.splitlines())
    assert all(
        line.startswith(("SURVIVED", "NO_COVERAGE"))
        for line in proc.stdout.splitlines()
    )


def test_run_reports_an_unknown_id_clearly(throwaway):
    proc = moonbuggy("run", "no-such-mutant", cwd=throwaway, expect=2)

    assert "is not a mutant id" in proc.stderr


def test_why_names_the_selected_tests_without_running_the_mutant(throwaway):
    """The selection half: which tests, how many, and where the set came from.

    `moonbuggy show` cannot say any of it, and `moonbuggy run` can only say it
    by spending a process per mutant. This spends none.
    """
    moonbuggy(cwd=throwaway)
    killed_id = next(
        json.loads(line)["id"]
        for line in (throwaway / ".moonbuggy" / "results.jsonl")
        .read_text()
        .splitlines()
        if json.loads(line)["status"] == "KILLED"
    )

    proc = moonbuggy("why", killed_id, cwd=throwaway, expect=0)

    assert f"id           {killed_id}" in proc.stdout
    assert "the coverage pass saw" in proc.stdout
    assert "selected     test_calc.py::" in proc.stdout
    assert "tests_run    " in proc.stdout
    # `show`'s diff, since the reader should not need both commands.
    assert "  - " in proc.stdout and "  + " in proc.stdout
    # And no verdict, because nothing was measured.
    assert "status" not in proc.stdout


def test_why_says_a_cache_hit_would_be_replayed(throwaway):
    """The other half, and the reason the issue exists.

    "My new test is being ignored" and "I am being served a stale verdict"
    look identical from a result line. After a full run the cache holds an
    entry for every mutant, and `why` says so in as many words.
    """
    moonbuggy(cwd=throwaway)
    survivor_id = next(
        json.loads(line)["id"]
        for line in (throwaway / ".moonbuggy" / "results.jsonl")
        .read_text()
        .splitlines()
        if json.loads(line)["status"] == "SURVIVED"
    )

    proc = moonbuggy("why", survivor_id, cwd=throwaway, expect=0)

    assert "cache        hit -- the next run replays SURVIVED" in proc.stdout
    assert "cache_key    " in proc.stdout
    # The files whose contents the key covers, so a reader knows what to edit
    # to invalidate it.
    assert "cache_covers calc.py" in proc.stdout
    assert "last_run     SURVIVED" in proc.stdout


def test_why_says_a_miss_after_the_test_file_changes(throwaway):
    """Editing a selected test file changes the key, so the hit disappears.

    This is the answer to "is my new test being ignored?": if the file it
    lives in is in `cache_covers`, the stale verdict cannot survive the edit.
    """
    moonbuggy(cwd=throwaway)
    survivor_id = next(
        json.loads(line)["id"]
        for line in (throwaway / ".moonbuggy" / "results.jsonl")
        .read_text()
        .splitlines()
        if json.loads(line)["status"] == "SURVIVED"
    )
    assert "cache        hit" in moonbuggy("why", survivor_id, cwd=throwaway).stdout

    tests = throwaway / "test_calc.py"
    tests.write_text(tests.read_text() + "\n\ndef test_added():\n    assert True\n")
    proc = moonbuggy("why", survivor_id, cwd=throwaway, expect=0)

    assert "cache        miss" in proc.stdout


def test_why_says_outright_when_no_test_reaches_the_line(uncovered):
    # The empty selection is the case the issue calls out by name: say so, and
    # say what it means, rather than leaving a bare `selected -`.
    proc = moonbuggy("why", "lib.py:6:constant_int:0", cwd=uncovered, expect=0)

    assert "the coverage pass saw no test execute lib.py:6" in proc.stdout
    assert "tests_run    0" in proc.stdout
    assert "selected     -" in proc.stdout
    assert "NO_COVERAGE" in proc.stdout
    # An explanation is not a finding: `why` in a CI script must not fail it.
    assert proc.returncode == 0


def test_why_emits_the_same_shape_as_json(uncovered):
    proc = moonbuggy("why", "--json", "lib.py:6:constant_int:0", cwd=uncovered)

    payload = json.loads(proc.stdout)
    assert payload["id"] == "lib.py:6:constant_int:0"
    assert payload["selected"] == []
    assert payload["tests_run"] == 0
    assert payload["next_run"] == "no_coverage"
    assert payload["cache_hit"] is False
    assert payload["run_inputs"]["timeout"] == 30.0
    assert payload["last_run_status"] is None


def test_why_explains_several_mutants_from_one_coverage_pass(throwaway):
    moonbuggy(cwd=throwaway)
    ids = [
        json.loads(line)["id"]
        for line in (throwaway / ".moonbuggy" / "results.jsonl")
        .read_text()
        .splitlines()
    ][:3]

    proc = moonbuggy("why", "--json", *ids, cwd=throwaway, expect=0)

    # One JSON object per line, the shape results.jsonl uses.
    assert [json.loads(line)["id"] for line in proc.stdout.splitlines()] == ids


def test_why_reports_an_unknown_id_clearly(throwaway):
    proc = moonbuggy("why", "no-such-mutant", cwd=throwaway, expect=2)

    assert "is not a mutant id" in proc.stderr
    assert "Traceback (most recent call last)" not in proc.stderr


def test_json_puts_exactly_one_object_on_stdout(throwaway):
    proc = moonbuggy("--json", cwd=throwaway)

    # The whole of stdout parses as one document. Anything else printed there
    # -- a report line, a prose note -- would break this.
    summary = json.loads(proc.stdout)
    assert summary["schema"] == 1
    assert sum(summary["counts"].values()) == summary["total"]
    assert summary["total"] == len(_records(throwaway))


def test_json_never_moves_the_plaintext_view(throwaway):
    plain = moonbuggy(cwd=throwaway)
    text = (throwaway / ".moonbuggy" / "results.txt").read_text()

    moonbuggy("--json", "--output-dir", ".mb-json", cwd=throwaway)

    # `grep SURVIVED` keeps working exactly as documented, with or without the
    # flag: --json adds a view, it does not replace one.
    assert (throwaway / ".mb-json" / "results.txt").read_text() == text
    assert plain.stdout != ""


def test_every_run_leaves_a_summary_whether_or_not_json_was_asked_for(throwaway):
    proc = moonbuggy(cwd=throwaway)

    summary = json.loads((throwaway / ".moonbuggy" / "summary.json").read_text())

    assert summary["exit_code"] == proc.returncode
    assert summary["counts"]["survived"] + summary["counts"]["no_coverage"] > 0
    assert summary["moonbuggy"]


def test_the_summary_file_and_json_stdout_are_the_same_object(throwaway):
    proc = moonbuggy("--json", cwd=throwaway)

    on_disk = json.loads((throwaway / ".moonbuggy" / "summary.json").read_text())

    assert on_disk == json.loads(proc.stdout)


def test_the_summary_counts_agree_with_the_records(throwaway):
    moonbuggy(cwd=throwaway)

    summary = json.loads((throwaway / ".moonbuggy" / "summary.json").read_text())
    records = _records(throwaway)

    for keyword in STATUS_KEYWORDS:
        expected = sum(1 for record in records if record["status"] == keyword)
        assert summary["counts"][keyword.lower()] == expected


def test_the_summary_reports_the_effective_configuration(throwaway):
    moonbuggy(
        "--json",
        "--operators",
        "constant_int",
        "--exclude",
        "nothing_matches_this",
        "--pytest-arg=-p",
        "--pytest-arg=no:cacheprovider",
        cwd=throwaway,
    )

    config = json.loads((throwaway / ".moonbuggy" / "summary.json").read_text())[
        "config"
    ]

    assert config["operators"] == ["constant_int"]
    assert config["exclude"] == ["nothing_matches_this"]
    assert config["include"] == []
    assert config["pytest_args"] == ["-p", "no:cacheprovider"]
    assert config["timeout"] == 30.0
    assert config["cache"] is True


def test_the_summary_splits_cached_from_measured(throwaway):
    moonbuggy(cwd=throwaway)

    moonbuggy(cwd=throwaway)
    summary = json.loads((throwaway / ".moonbuggy" / "summary.json").read_text())

    assert summary["cached"] > 0
    assert summary["measured"] == summary["total"] - summary["cached"]


def test_the_summary_says_what_the_run_was_scoped_against(versioned):
    moonbuggy("--since", "main", cwd=versioned)

    summary = json.loads((versioned / ".moonbuggy" / "summary.json").read_text())

    assert summary["scope"]["diff_scoped"] is True
    assert summary["scope"]["since"] == "main"
    assert summary["scope"]["changed_lines"] > 0


def test_a_full_run_says_it_was_not_diff_scoped(throwaway):
    moonbuggy(cwd=throwaway)

    summary = json.loads((throwaway / ".moonbuggy" / "summary.json").read_text())

    assert summary["scope"]["diff_scoped"] is False
    assert summary["scope"]["since"] is None


def test_an_empty_diff_scope_still_answers_in_json(versioned):
    # The PR that touched only docs. A consumer that asked for an object on
    # every run must not get an empty stream for it.
    (versioned / "calc.py").write_text(
        (versioned / "calc.py").read_text().replace(ADDED, "")
    )
    (versioned / "README.md").write_text("docs only\n")

    proc = moonbuggy("--since", "main", "--json", cwd=versioned, expect=0)

    summary = json.loads(proc.stdout)
    assert summary["total"] == 0
    assert summary["exit_code"] == 0
    assert summary["scope"]["diff_scoped"] is True
    assert summary == json.loads(
        (versioned / ".moonbuggy" / "summary.json").read_text()
    )


def test_the_summary_reports_the_ledger(triaged):
    moonbuggy("--fail-on-unexplained", cwd=triaged, expect=0)

    summary = json.loads((triaged / ".moonbuggy" / "summary.json").read_text())

    assert summary["acceptance"]["accepted"] > 0
    assert summary["acceptance"]["unexplained"] == 0
    assert summary["acceptance"]["fail_on_unexplained"] is True
    assert summary["exit_code"] == 0


def test_every_record_declares_its_schema(throwaway):
    moonbuggy(cwd=throwaway)

    records = _records(throwaway)

    assert records
    assert {record["schema"] for record in records} == {RECORD_SCHEMA}


LOGGING_PROJECT = """\
import logging

logger = logging.getLogger(__name__)


def backoff(attempt, base):
    if attempt > 3:
        logger.debug("giving up after %d tries", attempt * 2)
        return None
    return base * 2
"""

LOGGING_TESTS = """\
from backoff import backoff


def test_gives_up_eventually():
    assert backoff(4, 1) is None


def test_doubles_the_base():
    assert backoff(1, 5) == 10
"""


@pytest.fixture
def logging_project(tmp_path):
    """A project whose only survivors would be inside a log line."""
    (tmp_path / "backoff.py").write_text(LOGGING_PROJECT)
    (tmp_path / "test_backoff.py").write_text(LOGGING_TESTS)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
    )
    return tmp_path


def test_logging_mutants_are_skipped_and_the_guard_is_not(logging_project):
    # The whole policy in one run: line 8 is inside `logger.debug(...)` and is
    # settled without running; line 7 is the `if` around it and is a finding
    # like any other.
    moonbuggy(cwd=logging_project)

    records = _records(logging_project)
    inside = [r for r in records if r["line"] == 8]
    guard = [r for r in records if r["line"] == 7]

    assert inside
    assert {r["status"] for r in inside} == {"SKIPPED"}
    assert all(r["logging_call"] for r in inside)
    assert guard
    assert not any(r["logging_call"] for r in guard)
    assert "SKIPPED" not in {r["status"] for r in guard}


def test_the_report_says_it_suppressed_them(logging_project):
    proc = moonbuggy("--report", "human", cwd=logging_project)

    assert "inside logging calls" in proc.stdout
    assert "--include-logging-mutants" in proc.stdout


def test_including_logging_mutants_runs_them(logging_project):
    moonbuggy("--include-logging-mutants", cwd=logging_project)

    inside = [r for r in _records(logging_project) if r["line"] == 8]

    assert inside
    assert "SKIPPED" not in {r["status"] for r in inside}
    # Still tagged, so triage can filter them even when they are run.
    assert all(r["logging_call"] for r in inside)


def test_suppressed_logging_mutants_stay_in_the_denominator(logging_project):
    # The honesty rule: skipping must not flatter the score. SKIPPED leaves the
    # denominator, so the kill rate is the same whether or not they ran -- the
    # only thing that changes is how many mutants there are to review.
    moonbuggy(cwd=logging_project)
    default = _records(logging_project)
    moonbuggy("--include-logging-mutants", "--clear-cache", cwd=logging_project)
    included = _records(logging_project)

    assert [r["id"] for r in default] == [r["id"] for r in included]
    assert sum(r["status"] == "SKIPPED" for r in default) > 0


def test_why_explains_a_suppressed_logging_mutant(logging_project):
    moonbuggy(cwd=logging_project)
    target = next(
        r["id"] for r in _records(logging_project) if r["status"] == "SKIPPED"
    )

    proc = moonbuggy("why", target, cwd=logging_project, expect=0)

    assert "inside a logging call" in proc.stdout
    assert "--include-logging-mutants" in proc.stdout


def test_a_wrapped_logger_can_be_named(tmp_path):
    (tmp_path / "wrapped.py").write_text(
        "import logging\n\naudit = logging.getLogger('audit')\n\n\n"
        "def charge(amount):\n    audit.info('charging %d', amount * 2)\n"
        "    return amount\n"
    )
    (tmp_path / "test_wrapped.py").write_text(
        "from wrapped import charge\n\n\n"
        "def test_charge():\n    assert charge(3) == 3\n"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n"
    )

    moonbuggy(cwd=tmp_path)
    before = [r for r in _records(tmp_path) if r["line"] == 7]
    moonbuggy("--logger-name", "audit", "--clear-cache", cwd=tmp_path)
    after = [r for r in _records(tmp_path) if r["line"] == 7]

    assert before and not any(r["logging_call"] for r in before)
    assert after and all(r["status"] == "SKIPPED" for r in after)


def test_the_effective_config_records_the_logging_policy(logging_project):
    moonbuggy("--logger-name", "audit", cwd=logging_project)

    summary = json.loads((logging_project / ".moonbuggy" / "summary.json").read_text())

    assert summary["config"]["logger_names"] == ["audit"]
    assert summary["config"]["include_logging_mutants"] is False
