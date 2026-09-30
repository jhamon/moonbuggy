"""Forked coverage-pass children honor PYTHONPATH like spawned children do.

A spawned `python -m pytest` subprocess applies PYTHONPATH at interpreter
startup. A forked child skips that startup, so before the fix its sys.path
silently lacked PYTHONPATH and its imports diverged from the subprocess
path's. The concrete failure (spike Finding 4's rig): an -E-pinned parent —
the M1.1 self-mutation rig — forks a coverage pass that imports the runner's
installed moonbuggy instead of the PYTHONPATH target, and the coverage pass
fails (or worse, measures the wrong tree) while the same run's subprocess
mutant passes measure the right one.

These tests fork a real child (POSIX) and assert the child's import
resolution matches a spawned child's under the same environment.
"""

import os
import sys

import pytest

from moonbuggy.forkserver import run_pytest_in_fork

pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is POSIX-only")


def _probe_module(tmp_path, pythonpath_dir):
    """A package only importable via PYTHONPATH, plus a one-test pytest file
    that imports it. The forked child runs this file through pytest.main; the
    spawned child runs it as `pytest <file>`. Import resolution fails the test
    at collection, so exit code 0 ⇔ the child resolved the PYTHONPATH target."""
    pkg = pythonpath_dir / "rigprobe"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("HERE = __file__\n")
    probe = tmp_path / "test_probe_rigprobe.py"
    probe.write_text(
        "import rigprobe\n\n\ndef test_imports():\n    assert rigprobe.HERE\n"
    )
    return probe


def _run_fork_probe(probe, pythonpath_dir, tmp_path):
    """Fork a child that mimics the coverage pass: capture stdout to a file
    (the real child redirects to devnull, so we capture via pytest exit
    semantics instead — a collection/import failure yields a distinctive
    code)."""
    # run_pytest_in_fork returns pytest's exit code. For a module-level import
    # the child cannot resolve, pytest exits 2; for a resolved one it exits 0.
    args = [str(probe)]
    env_updates = {"PYTHONPATH": str(pythonpath_dir)}
    old_env = {k: os.environ.get(k) for k in env_updates}
    os.environ.update(env_updates)
    try:
        code = run_pytest_in_fork(
            tmp_path, args, {"PYTHONPATH": str(pythonpath_dir)}, 60
        )
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return code


def test_fork_child_imports_pythonpath_target(tmp_path):
    """The forked child resolves a PYTHONPATH-only package (exit 0), matching
    what a spawned subprocess does."""
    probe = _probe_module(tmp_path, tmp_path / "pp")
    code = _run_fork_probe(probe, tmp_path / "pp", tmp_path)
    assert code == 0, "forked child could not import the PYTHONPATH package"


def test_fork_child_matches_subprocess_under_e_parent(tmp_path):
    """The -E-rig scenario: PYTHONPATH present in the environment. The forked
    child must behave like the spawned child — both import the target. Before
    the fix the forked child's sys.path lacked PYTHONPATH and the import
    failed (pytest exit 2) while the spawned child succeeded."""
    pp = tmp_path / "pp"
    probe = _probe_module(tmp_path, pp)

    # spawned child (what the subprocess path does):
    import subprocess

    spawned = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(pp)},
    )
    assert spawned.returncode == 0

    # forked child (what the coverage pass does on POSIX):
    forked = _run_fork_probe(probe, pp, tmp_path)
    assert forked == 0, (
        "forked child's imports diverge from the spawned child's under "
        "PYTHONPATH — the -E self-mutation rig measures the wrong tree"
    )
