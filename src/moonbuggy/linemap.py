"""Line -> covering-tests map, built with sys.monitoring (PEP 669).

This is the input to coverage-guided test selection, the largest single speed
lever in the design (4.3): for a mutant on line N, run only the tests that
execute line N, instead of the whole suite.

Correctness matters more than speed here, and asymmetrically. A map that misses
a covering test makes moonbuggy run too few tests and report a false SURVIVED --
silent, and indistinguishable from a real finding. A map with a spurious extra
test only costs time. So the collector records every line executed during a
test, including lines reached indirectly through call chains, and filters only
by file.

On the "disable lines already seen" optimisation PEP 669 allows: it does not
apply to this pass. Returning DISABLE after first sighting would stop the line
being recorded for LATER tests, which is exactly the attribution the map is for.
It would make the pass faster and the results wrong.
"""

import sys
from contextlib import contextmanager
from pathlib import Path

# Tool ids 0-5 are shared with debuggers, profilers and coverage.py. Claiming a
# fixed one risks colliding with whatever else is in the process, so a free id
# is found at registration time.
_CANDIDATE_TOOL_IDS = (2, 3, 4, 5)


class LineCollector:
    """Records which tests execute which lines of a set of target files."""

    def __init__(self, target_files):
        self.targets = {str(Path(f).resolve()) for f in target_files}
        self.mapping = {}
        self._current_test = None
        self._tool_id = None
        # Resolving a path per line event is far too expensive, so the decision
        # is cached. Keyed on co_filename -- the actual input to the decision --
        # and NOT on the code object: CPython compares code objects by value
        # (name, flags, first line, bytecode, constants) and co_filename is not
        # among them, so two identical helpers in different files are equal and
        # hash equal. Caching on the code object silently merges their coverage.
        self._is_target = {}

    def tests_covering(self, path, line):
        return self.mapping.get((str(Path(path).resolve()), line), set())

    @contextmanager
    def session(self):
        """Hold the monitoring tool id for the whole block.

        Used for a real coverage pass: claiming and freeing a tool id per test
        would charge registration cost to every test in the suite.
        """
        self._start()
        try:
            yield self
        finally:
            self._stop()

    @contextmanager
    def for_test(self, test_id):
        """Attribute lines executed inside the block to test_id."""
        self._current_test = test_id
        try:
            yield self
        finally:
            self._current_test = None

    @contextmanager
    def recording(self, test_id):
        """session() and for_test() together, for a single-test recording."""
        with self.session(), self.for_test(test_id):
            yield self

    def _start(self):
        monitoring = sys.monitoring
        self._tool_id = self._claim_tool_id(monitoring)
        monitoring.register_callback(self._tool_id, monitoring.events.LINE, self._on_line)
        monitoring.set_events(self._tool_id, monitoring.events.LINE)

    def _stop(self):
        monitoring = sys.monitoring
        if self._tool_id is None:
            return
        monitoring.set_events(self._tool_id, 0)
        monitoring.register_callback(self._tool_id, monitoring.events.LINE, None)
        # Releasing matters: a leaked id makes every later collector fail to
        # register, so the coverage pass would die partway through a real suite.
        monitoring.free_tool_id(self._tool_id)
        self._tool_id = None

    @staticmethod
    def _claim_tool_id(monitoring):
        for tool_id in _CANDIDATE_TOOL_IDS:
            if monitoring.get_tool(tool_id) is None:
                monitoring.use_tool_id(tool_id, "moonbuggy")
                return tool_id
        raise RuntimeError(
            "No free sys.monitoring tool id: all of "
            f"{_CANDIDATE_TOOL_IDS} are claimed. A debugger, profiler or "
            "coverage tool is probably attached to this process."
        )

    def _on_line(self, code, line_number):
        # Inside a session but between tests there is no current test. Lines run
        # there (import-time work, fixture teardown between tests) belong to no
        # test, and recording them would put a phantom None into the map.
        if self._current_test is None:
            return
        filename = code.co_filename
        target = self._is_target.get(filename)
        if target is None:
            target = self._resolve(filename)
            self._is_target[filename] = target
        if not target:
            return
        self.mapping.setdefault((target, line_number), set()).add(self._current_test)

    def _resolve(self, filename):
        if not filename or filename.startswith("<"):
            return False
        resolved = str(Path(filename).resolve())
        return resolved if resolved in self.targets else False
