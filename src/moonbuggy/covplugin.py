"""pytest plugin that builds the line->test map during a coverage pass.

Separate from plugin.py because the two never run together: the coverage pass
happens once, before any mutant exists, and the mutation plugin runs once per
mutant afterwards. Keeping them apart means neither pays the other's cost.

Target files and the output path arrive by environment variable, for the same
reason the mutant does -- it survives the trip into an xdist worker without any
cross-process plumbing of our own.
"""

import json
import os

import pytest

from .linemap import LineCollector

TARGETS_ENV_VAR = "MOONBUGGY_COVERAGE_TARGETS"
OUTPUT_ENV_VAR = "MOONBUGGY_COVERAGE_OUTPUT"


class CoveragePlugin:
    def __init__(self, targets, output_path):
        self.collector = LineCollector(targets)
        self.output_path = output_path
        self._session = None

    def pytest_sessionstart(self, session):
        self._session = self.collector.session()
        self._session.__enter__()

    def pytest_sessionfinish(self, session, exitstatus):
        if self._session is None:
            return
        self._session.__exit__(None, None, None)
        self._session = None
        self._write()

    # wrapper=True is load-bearing, not decoration. Without it pytest treats
    # this as an ordinary hook, never iterates the generator, and the body
    # silently never runs -- producing an empty map with a green test run.
    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item):
        # Only the call phase: attributing setup and teardown would credit every
        # test sharing a fixture with lines it never itself exercised, inflating
        # the selected set for those lines.
        with self.collector.for_test(item.nodeid):
            return (yield)

    def _write(self):
        payload = [
            {"file": path, "line": line, "tests": sorted(tests)}
            for (path, line), tests in sorted(self.collector.mapping.items())
        ]
        with open(self.output_path, "w") as handle:
            json.dump(payload, handle, indent=2)


def pytest_configure(config):
    targets = os.environ.get(TARGETS_ENV_VAR)
    output = os.environ.get(OUTPUT_ENV_VAR)
    if not targets or not output:
        return
    config.pluginmanager.register(CoveragePlugin(json.loads(targets), output))
