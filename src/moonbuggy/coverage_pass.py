"""The coverage pass: one instrumented run producing a line -> covering-tests map.

This is the input to coverage-guided test selection, the largest single speed
lever in the design (4.3). Spike B settled the mechanism: pytest-cov's per-test
contexts, which are both the fastest of the candidates measured and the only one
recording real pytest node ids -- and node ids are what selection has to hand
back to pytest. See docs/spike-b-findings.md.

Correctness here is asymmetric. A map missing a covering test makes moonbuggy
run too few tests and report a false SURVIVED, which looks exactly like a real
finding. A map with a spurious extra test only costs time. Every judgement call
below therefore favours the larger set.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from . import forkserver


class LineMap:
    """Which tests execute which lines."""

    def __init__(self, mapping, tests, project_dir):
        self._mapping = mapping
        self._tests = set(tests)
        self._project_dir = Path(project_dir)

    def tests_covering(self, module, line):
        return self._mapping.get((self._normalise(module), line), set())

    def all_tests(self):
        return set(self._tests)

    def select_for(self, mutant):
        """The tests to run for one mutant.

        Module-level mutants get the whole suite. Their line runs at import
        time, not inside any test body, so the map attributes it to nothing --
        and "no covering tests" is indistinguishable from "genuinely uncovered"
        without this. Running everything is wasteful for a rare case, and
        wrong-and-fast is not a trade worth making on a silent failure mode.
        """
        if mutant.module_level:
            return self.all_tests()
        return self.tests_covering(mutant.module, mutant.line)

    def _normalise(self, module):
        path = Path(module)
        if not path.is_absolute():
            path = self._project_dir / path
        return str(path.resolve())

    def to_dict(self):
        return {
            "tests": sorted(self._tests),
            "lines": [
                {"file": path, "line": line, "tests": sorted(tests)}
                for (path, line), tests in sorted(self._mapping.items())
            ],
        }


def run_coverage_pass(project_dir, source_dir, python=None, extra_args=(),
                      use_fork=None, timeout=600):
    """Run the suite once under coverage, returning a LineMap."""
    project_dir = Path(project_dir)
    python = python or sys.executable

    args = [
        "-q", "-p", "no:cacheprovider",
        f"--cov={source_dir}", "--cov-context=test", "--cov-report=",
        *extra_args,
    ]

    with tempfile.TemporaryDirectory() as tmp:
        data_file = Path(tmp) / "coverage-data"

        if use_fork is None:
            use_fork = forkserver.available()

        if use_fork:
            # Forking skips a full interpreter startup. Measured at a third of
            # total run time before this change.
            code = forkserver.run_pytest_in_fork(
                project_dir, args, {"COVERAGE_FILE": str(data_file)}, timeout
            )
            if code is None:
                raise CoveragePassError(-1, "coverage pass timed out", "")
            stdout = stderr = ""
        else:
            proc = subprocess.run(
                [python, "-m", "pytest", *args],
                cwd=project_dir, capture_output=True, text=True,
                env=_env_with_data_file(data_file),
            )
            code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr

        # Exit code 1 means tests failed, which is fine here: the map is still
        # valid, and a suite that is already red is the user's problem to see
        # reported rather than a reason to refuse to run.
        if code not in (0, 1):
            raise CoveragePassError(code, stdout, stderr)
        return read_coverage_data(data_file, project_dir)


class CoveragePassError(RuntimeError):
    def __init__(self, returncode, stdout, stderr):
        super().__init__(
            f"coverage pass failed (pytest exit {returncode}).\n{stdout}\n{stderr}"
        )
        self.returncode = returncode


def _env_with_data_file(data_file):
    import os

    env = dict(os.environ)
    env["COVERAGE_FILE"] = str(data_file)
    return env


def read_coverage_data(data_file, project_dir):
    import coverage

    data = coverage.CoverageData(basename=str(data_file))
    data.read()

    mapping = {}
    tests = set()
    for filename in data.measured_files():
        resolved = str(Path(filename).resolve())
        for line, contexts in data.contexts_by_lineno(filename).items():
            # Contexts are `<nodeid>|<phase>`. The phase suffix has to go: the
            # id is handed back to pytest to select with. Phases are unioned
            # rather than filtered to `run`, so a line executed in a fixture is
            # credited to the tests that use that fixture.
            covering = {c.split("|")[0] for c in contexts if c and "::" in c}
            if covering:
                mapping.setdefault((resolved, line), set()).update(covering)
                tests.update(covering)

    return LineMap(mapping, tests, project_dir)
