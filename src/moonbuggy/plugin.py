"""pytest plugin that applies the active mutant in every process that runs tests.

This is the answer to risk 2 in section 4.2: pytest-xdist workers are separate
processes that re-import from disk, so a mutation applied only in the controller
leaves workers running unmutated code and reporting a false SURVIVED. Silent,
and on a common configuration.

The fix is that the mutant's identity travels in the environment rather than in
controller memory. execnet gives each xdist worker the controller's environment,
and every worker runs its own pytest_configure -- so each one installs the same
mutation independently, before collection imports anything under test. No xdist
hook, no serialisation of our own state, and it degrades to the serial case for
free because the controller does exactly the same thing.
"""

import json
import os

from .inmemory import install

MUTANT_ENV_VAR = "MOONBUGGY_MUTANT"

# Regression probe. Set this and the mutation is applied only in the controller,
# reproducing the xdist bug described above. It exists so the xdist test can
# demonstrate it detects that bug -- a passing test proves nothing if it would
# pass with the mechanism broken. Never set in normal use.
CONTROLLER_ONLY_ENV_VAR = "MOONBUGGY_SPIKE_CONTROLLER_ONLY"


def pytest_configure(config):
    """Install the active mutant, if this process was told about one.

    Runs in every pytest process -- controller and each xdist worker -- which
    is what makes the xdist story work without any cross-process state.

    Args:
        config: the pytest config for this process.
    """
    payload = os.environ.get(MUTANT_ENV_VAR)
    controller_only = os.environ.get(CONTROLLER_ONLY_ENV_VAR)
    if payload and not (controller_only and _is_xdist_worker(config)):
        mutant = json.loads(payload)
        install(mutant["path"], mutant["line"], mutant["mutated"])


def _is_xdist_worker(config):
    # xdist sets `workerinput` on the config of each worker process only.
    return hasattr(config, "workerinput")
