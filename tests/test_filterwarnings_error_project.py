"""Regression: fork paths must survive a project that sets
``filterwarnings = ["error"]``.

Bug (found running the M1.3.1 differential against the pinned humanize
4.16.0 checkout, which sets ``filterwarnings = ["error"]``): the forked
child inherits the parent's ``sys.modules``, so ``moonbuggy.baseline`` (or
``moonbuggy.killreason``) is already imported when pytest's
``-p moonbuggy.baseline`` asks the assertion-rewrite hook to mark it for
rewriting. pytest then emits ``PytestAssertRewriteWarning`` -- which the
project's ``filterwarnings = error`` escalates to an exception during
config parsing. pytest.main dies with a usage error, the fork reports
CHILD_CRASHED, the coverage pass fails, and every mutant collapses to
SUSPICIOUS (execution_crash) even though plain ``pytest`` kills them
normally.

A wrong verdict class en masse is worse than a refusal, so the fork paths
must not manufacture SUSPICIOUS out of a warning the project escalates.
The fix: the fork child must not pass ``-p`` for a moonbuggy plugin module
that is already in ``sys.modules`` -- or must pre-register it as a plugin
object so pytest never goes through the entry-point/``-p`` import path
whose rewrite check fires.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
FIXTURE = Path(__file__).parent / "fixtures" / "filterwarnings_error_project"

pytestmark = pytest.mark.slow


def _run_moonbuggy(tmp_output: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "moonbuggy.cli",
            "--no-cache",
            "--quiet",
            "--output-dir",
            str(tmp_output),
            "--source",
            str(FIXTURE / "widget"),
            "--timeout",
            "30",
        ],
        cwd=FIXTURE,
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )


def _statuses(tmp_output: Path) -> list[str]:
    lines = (tmp_output / "results.jsonl").read_text().splitlines()
    import json

    return [json.loads(line)["status"] for line in lines if line.strip()]


@pytest.mark.skipif(
    not hasattr(os, "fork"), reason="the bug is specific to the fork paths"
)
def test_fork_paths_do_not_collapse_to_suspicious_under_filterwarnings_error(
    tmp_path,
):
    """The project's own suite kills the mutant (plain pytest, exit 1); the
    fork path must agree, not manufacture SUSPICIOUS out of an escalated
    rewrite warning."""
    # Ground truth first: plain pytest under the fixture's config kills the
    # mutant the fixture carries. If this assertion fails, the fixture is
    # wrong, not moonbuggy.
    config = (FIXTURE / "pyproject.toml").read_text()
    assert 'filterwarnings = ["error"]' in config

    output = tmp_path / "out"
    proc = _run_moonbuggy(output)
    # exit 1, not 0: the fixture's SURVIVED mutants are findings, and findings
    # drive exit 1. The failure to catch is returncode 2 ("no results"/no
    # mutants) and any nonzero exit whose stderr says the coverage pass died.
    assert proc.returncode == 1, proc.stderr[-2000:]

    statuses = _statuses(output)
    assert statuses, "moonbuggy produced no results"
    assert "SUSPICIOUS" not in statuses, (
        "fork path escalated the project's filterwarnings=error into a "
        "config-time crash: every mutant read SUSPICIOUS (execution_crash) "
        f"instead of its real verdict; statuses were {statuses}"
    )
    assert "KILLED" in statuses
