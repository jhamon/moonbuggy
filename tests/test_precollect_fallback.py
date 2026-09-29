"""Regression: a failed precollect must leave the prebuilt config clean.

Bug (found on the pinned humanize 4.16.0 checkout during the M1.3.1
differential): when precollect's ``Session.from_config``/``perform_collect``
fails, the half-built Session stays registered on the config's plugin
manager under the name "session". Every grandchild then falls back to
``_run_prebuilt``, whose ``wrap_session`` tries to register its own Session
under that same name and raises ``ValueError: Plugin name already
registered`` -- mapped to CHILD_CRASHED, reported as SUSPICIOUS
execution_crash for every mutant. 202 of humanize's 204 mutants read
SUSPICIOUS while plain pytest killed them normally.

The contract here: after precollect returns None (whatever the reason),
the prebuilt config must be reusable -- a grandchild calling
``_run_prebuilt`` on it gets the mutant's real verdict.
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


@pytest.mark.skipif(
    not hasattr(os, "fork"), reason="the bug is specific to the fork paths"
)
def test_precollect_failure_leaves_config_reusable(tmp_path):
    """precollect returns None on an uncollectable id; the fallback grandchild
    must still reach the real verdict instead of crashing on plugin
    registration."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")

    driver = "import os, sys\n"
    driver += f"sys.path.insert(0, {str(SRC)!r})\n"
    driver += f"os.chdir({str(FIXTURE)!r})\n"
    driver += (
        "from moonbuggy import forkserver as FS\n"
        "config = FS.prebuild_mutant_config([])\n"
        "assert config is not None, 'prebuild failed'\n"
        # An id pytest cannot collect: precollect must return None, and the
        # config it leaves behind must still work for _run_prebuilt.
        "session = FS.precollect(config, ['tests/test_shout.py::no_such_test'])\n"
        "assert session is None, 'precollect should fail on an unknown id'\n"
        "plugin_names = [p[0] for p in config.pluginmanager.list_name_plugin()]\n"
        "assert 'session' not in plugin_names, (\n"
        "    'failed precollect left a Session registered; the fallback '\n"
        "    'grandchild would crash with Plugin name already registered')\n"
        "rc = FS._run_prebuilt(config, ['tests/test_shout.py::test_clamp_negative'])\n"
        "assert rc == 0, f'_run_prebuilt rc={rc}, expected a clean pass'\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", driver],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
