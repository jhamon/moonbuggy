"""Baseline health: is the suite green, and is any of it flaky?

Two milestone criteria meet in one mechanism, because they are answered by the
same evidence.

**M1.4.4 -- an already-red suite.** Mutation results are meaningless against a
failing baseline. Every mutant covered by a test that already fails is reported
KILLED, and the run looks like an excellent score. That is worse than no result,
so moonbuggy refuses rather than reporting.

**M1.4.3 -- a flaky test.** A test that fails at random makes some mutants
KILLED for reasons that have nothing to do with the mutation, and can equally
make a genuinely-killed mutant look SURVIVED. Neither status is honest, and
`SUSPICIOUS` exists precisely for "we cannot tell".

The distinguishing evidence is the same in both cases: run the unmutated suite
more than once and compare per-test outcomes.

    failed every time  -> genuinely red. Refuse the run.
    outcome varied     -> flaky. Every mutant that selects it is SUSPICIOUS.
    passed every time  -> trustworthy.

One extra unmutated run is the default (``--flaky-probe 1``). It happens inside
the warm host, where every test module is already imported, so it costs test
execution time and nothing else. It can be turned off with ``--flaky-probe 0``
for a project known to be deterministic, at the cost of the M1.4.3 guarantee.

The probe cannot be perfect, and does not claim to be: a test that fails one
run in a thousand will usually pass both. Detecting flakiness is a sampling
problem, and more probes buy more confidence linearly. What the probe does
guarantee is that a flake it *observes* never becomes a confident status.

This module doubles as a pytest plugin. It is loaded with ``-p
moonbuggy.baseline`` and writes its outcomes to the path named by
``MOONBUGGY_OUTCOMES``, which is what lets the in-process (forked) and
out-of-process (subprocess) paths collect identical evidence.
"""

import json
import os

OUTCOMES_ENV_VAR = "MOONBUGGY_OUTCOMES"

# Phases pytest reports per test. A failure in setup or teardown is still a
# failure of that test for our purposes: it means the test did not pass.
_FAILING = {"failed", "error"}


class BaselineError(RuntimeError):
    """The suite is not in a state where mutation results would mean anything."""


class OutcomeRecorder:
    """Collects one outcome per test node id from a pytest run.

    Registered as a plugin rather than reading pytest's exit code, because the
    exit code says only "something failed" -- and knowing *which* tests failed
    is what separates a red baseline from a flake.
    """

    def __init__(self):
        self.outcomes = {}

    def pytest_runtest_logreport(self, report):
        """Record the worst outcome seen for this test across its three phases."""
        current = self.outcomes.get(report.nodeid)
        if report.outcome in _FAILING:
            self.outcomes[report.nodeid] = "failed"
        elif current is None:
            self.outcomes[report.nodeid] = report.outcome

    def pytest_collectreport(self, report):
        """A module that fails to import produces no tests at all.

        Left unrecorded, a collection error would look like an empty but green
        suite, and moonbuggy would report SURVIVED for everything it covers.
        """
        if report.outcome in _FAILING:
            self.outcomes[f"{report.nodeid}::<collection>"] = "failed"


def pytest_configure(config):
    """Plugin hook: install a recorder when MOONBUGGY_OUTCOMES names a file."""
    path = os.environ.get(OUTCOMES_ENV_VAR)
    if not path:
        return
    recorder = OutcomeRecorder()
    config.pluginmanager.register(recorder, "moonbuggy-outcomes")
    config._moonbuggy_recorder = recorder


def pytest_sessionfinish(session, exitstatus):
    """Plugin hook: write the recorded outcomes where the parent can read them."""
    path = os.environ.get(OUTCOMES_ENV_VAR)
    recorder = getattr(session.config, "_moonbuggy_recorder", None)
    if not path or recorder is None:
        return
    with open(path, "w") as handle:
        json.dump(
            {"exit_status": int(exitstatus), "outcomes": recorder.outcomes}, handle
        )


def classify(runs):
    """Split tests into consistently-failing and inconsistent, given several runs.

    Args:
        runs: a list of ``{node_id: outcome}`` mappings, one per unmutated run of the
            suite. The first is authoritative for which tests exist.

    Returns:
        ``(failing, flaky)``, both sets of node ids. A test appears in at most one of
            them.
    """
    if not runs:
        return set(), set()

    seen = {}
    for run in runs:
        for node_id, outcome in run.items():
            seen.setdefault(node_id, set()).add(outcome)

    failing = set()
    flaky = set()
    for node_id, outcomes in seen.items():
        # A test present in one run and absent from another is not stable
        # either -- collection itself varied, which is a flake by any useful
        # definition and certainly not something to report a status against.
        appeared_everywhere = all(node_id in run for run in runs)
        if len(outcomes) > 1 or not appeared_everywhere:
            flaky.add(node_id)
        elif outcomes == {"failed"}:
            failing.add(node_id)
    return failing, flaky


def check(runs, allow_empty=False):
    """Raise if the baseline cannot support a mutation run.

    Args:
        runs: as for :func:`classify`.
        allow_empty: when True, a suite with no tests is permitted. Only used by
            callers that have already reported the emptiness themselves.

    Returns:
        the set of flaky test node ids, which callers use to mark affected mutants
            SUSPICIOUS.

    Raises:
        BaselineError: if no tests ran, or if any test fails consistently.
    """
    failing, flaky = classify(runs)

    if not runs or (not runs[0] and not allow_empty):
        raise BaselineError(
            "no tests ran. moonbuggy needs a passing test suite to mutate "
            "against -- there is nothing for a mutant to survive. Check that "
            "pytest collects your tests from this directory."
        )

    if failing:
        listed = "\n".join(f"  {node_id}" for node_id in sorted(failing)[:10])
        more = "" if len(failing) <= 10 else f"\n  ... and {len(failing) - 10} more"
        raise BaselineError(
            f"the test suite is already failing before any mutation "
            f"({len(failing)} of {len(runs[0])} tests):\n{listed}{more}\n"
            "Mutation results against a red baseline are meaningless -- every "
            "mutant those tests cover would be reported KILLED regardless of "
            "the mutation. Fix the suite, then run moonbuggy again. "
            "No mutation results were produced."
        )

    return flaky


def probe_env(path):
    """Environment additions that make a pytest run record its outcomes."""
    return {OUTCOMES_ENV_VAR: str(path)}


def read_outcomes(path):
    """Read one run's recorded outcomes back.

    Args:
        path: the file named by :data:`OUTCOMES_ENV_VAR` for that run.

    Returns:
        ``{node_id: outcome}``, empty if the file was never written -- which is itself
            meaningful, since it means the run did not finish.
    """
    try:
        with open(path) as handle:
            return json.load(handle)["outcomes"]
    except (OSError, ValueError, KeyError):
        return {}
