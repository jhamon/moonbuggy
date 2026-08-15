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

from . import baseline, forkserver


class LineMap:
    """Which tests execute which lines."""

    def __init__(self, mapping, tests, project_dir):
        self._mapping = mapping
        self._tests = set(tests)
        self._project_dir = Path(project_dir)

    def tests_covering(self, module, line):
        """The node ids of tests that executed `line` of `module`."""
        return self._mapping.get((self._normalise(module), line), set())

    def all_tests(self):
        """Every test node id the instrumented run observed."""
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
        """The whole map as plain data, for serialising or inspecting."""
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
        "-q", "-p", "no:cacheprovider", "--rootdir", str(project_dir),
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


def run_baseline_pass(project_dir, source_dir, probes=1, python=None, timeout=600,
                      extra_args=()):
    """Coverage pass plus flakiness probe, for the paths that cannot fork.

    Same evidence as the warm session gathers (see
    :func:`moonbuggy.forkserver.run_warm_session`), collected the slow way: the
    suite runs ``1 + probes`` times as separate processes, each writing its
    per-test outcomes to a file the parent reads back.

    Args:
        project_dir: project root.
        source_dir: directory to measure coverage of.
        probes: extra unmutated runs used to detect flaky tests (M1.4.3).
        python: interpreter to run pytest with when forking is unavailable.
        timeout: seconds before one suite run is abandoned.
        extra_args: pytest arguments to add to every run.

    Returns:
        ``(linemap, flaky_test_ids)``.

    Raises:
        BaselineError: if no tests ran or the suite is already failing.
        CoveragePassError: if pytest could not complete at all.
    """
    project_dir = Path(project_dir)
    python = python or sys.executable

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        data_file = tmp / "coverage-data"
        runs = []

        for index in range(1 + probes):
            outcomes_file = tmp / f"outcomes-{index}.json"
            args = ["-q", "-p", "no:cacheprovider", "--rootdir", str(project_dir),
                    "-p", "moonbuggy.baseline"]
            if index == 0:
                args += [f"--cov={source_dir}", "--cov-context=test", "--cov-report="]
            else:
                # Probe runs need no instrumentation: they exist to observe
                # outcomes, and coverage would only slow them down.
                args += ["-p", "no:cov"]
            args += list(extra_args)

            env = {"COVERAGE_FILE": str(data_file), **baseline.probe_env(outcomes_file)}
            code = _run_pytest(project_dir, args, env, python, timeout)
            if code not in (0, 1, 5):
                raise CoveragePassError(code, "", "")
            runs.append(baseline.read_outcomes(outcomes_file))

        flaky = baseline.check(runs)
        linemap = read_coverage_data(data_file, project_dir, known_tests=runs[0])
        return linemap, flaky


def _run_pytest(project_dir, args, env, python, timeout):
    if forkserver.available():
        code = forkserver.run_pytest_in_fork(project_dir, args, env, timeout)
        if code is None:
            raise CoveragePassError(-1, "coverage pass timed out", "")
        return code

    import os

    full_env = dict(os.environ)
    full_env.update(env)
    proc = subprocess.run(
        [python, "-m", "pytest", *args],
        cwd=project_dir, capture_output=True, text=True, env=full_env,
    )
    return proc.returncode


class CoveragePassError(RuntimeError):
    """pytest could not complete the instrumented run at all."""

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


def read_coverage_data(data_file, project_dir, known_tests=()):
    """Build a LineMap from a coverage data file.

    Args:
        data_file: path to the coverage database the instrumented run wrote.
        project_dir: the project root, used to resolve module paths.
        known_tests: every test node id the run actually executed, from the
            outcome recorder. Coverage contexts only name tests that executed
            a *measured* line, so a module whose functions are never called
            during any test contributes no contexts at all -- and then
            `all_tests()` is empty, and a module-level mutant that widens to
            "the whole suite" runs nothing and is reported SURVIVED. Found by
            the M4 hunt; see tests/test_module_level_aliases.py.

    Returns:
        A :class:`LineMap` of which tests executed which lines.
    """
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

    # Union rather than replacement: the recorder knows which tests ran, the
    # contexts know which touched the source. Selection's stated bias is
    # toward the larger set, because a missing covering test is a false
    # SURVIVED and a spurious one only costs time.
    tests.update(t for t in known_tests if "::" in t and not t.endswith("::<collection>"))
    return LineMap(mapping, tests, project_dir)
