"""One named mutant: re-measured (`moonbuggy run`), or explained (`moonbuggy why`).

`moonbuggy show <id>` prints a mutant's record and diff but cannot run it, so
the fix-verify loop -- "I think this test kills that mutant, let me check" --
had no cheap path and ended in hand-applied mutations and a manual pytest
invocation. This module is the missing half: the same generation, the same
coverage-guided selection and the same runner, pointed at one mutant instead of
all of them.

Three decisions worth stating, because each of them could reasonably have gone
the other way.

**The cache is written but never read.** The whole point of the command is to
re-measure, so serving the previous verdict would answer the one question the
user is asking. Refusing to *store* the fresh verdict would be a different
mistake: the measurement is a real one, keyed on the same
:func:`~moonbuggy.cache.run_fingerprint` a full run uses, so the next full run
can honour it instead of paying for it again. That is the payoff of the loop --
verify a fix here, and the run in CI is already shorter.

**Verdicts are fresh, artifacts are not.** `results.jsonl` stays exactly as the
last full run left it. It is the canonical record *of a run*, and rewriting one
line of it from a single-mutant measurement would leave a file whose summary no
longer describes its contents.

**The coverage pass is not narrowed to the target's module.** The issue asked
for it and coverage.py cannot do it: `--cov=path/to/one.py` reports
`module-not-imported` and collects nothing, since coverage's source list takes
directories and importable names, not files. It would buy little anyway -- the
suite still has to run, and instrumentation scope is not what that costs.

:func:`explain` is the other half of the same machinery, and the one thing it
does *not* do is run the mutant. "Selection never picked up my new test" and
"this verdict came from the cache" produce the identical symptom -- a survivor
that will not die -- and the only way to tell them apart used to be an
experiment. So `why` reports the decision instead of the outcome: the same
coverage pass and the same :meth:`~moonbuggy.coverage_pass.LineMap.select_for`,
plus a lookup of the cache key those inputs produce. Re-measuring is what
`run` is for, and a `why` that also measured would be a slower `run` rather
than a different command.

There is no line->test map on disk to read this out of, and this deliberately
does not add one. Persisting the map would be a cache with all of a cache's
staleness problems and none of the results cache's key discipline, and the
answer a user wants from `why` is about the source as it stands *now* -- which
is exactly what a stored map cannot be.
"""

import os
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .baseline import read_outcomes
from .cache import CacheRecord, ResultCache
from .coverage_pass import run_baseline_pass
from .generate import GenerationError, generate_mutants
from .logging_policy import LoggingPolicy
from .mutant import Mutant, parse_id
from .report import Record
from .runner import Result, check_selection_is_runnable, run_one
from .srcio import SourceError, read_source

# Statuses that say nothing about this mutation and so are not worth storing:
# SKIPPED is a fact about the source (a suppression marker), SUSPICIOUS is a
# fact about the suite (a flaky test in the selection). `_plan` declines to
# cache both for the same reason.
_NOT_CACHEABLE = frozenset({"SKIPPED", "SUSPICIOUS"})


class VerifyError(RuntimeError):
    """A named mutant cannot be run, and the user has to fix something.

    Raised rather than reported, so the CLI's single failure funnel turns it
    into an exit 2 with a message and never a traceback.
    """


@dataclass(frozen=True)
class Verification:
    """One mutant, re-measured, with the evidence behind the verdict.

    The evidence is the reason this is not just a :class:`~moonbuggy.runner.Result`:
    a full run reports one line per mutant and cannot afford to name every
    selected test, but a user asking about a single mutant is asking exactly
    which tests were consulted and which of them objected.
    """

    result: Result
    """The runner's verdict, unchanged."""

    selected: tuple[str, ...]
    """Every test node id selection chose, sorted."""

    failed: tuple[str, ...]
    """The selected tests that failed under the mutation, sorted. Empty for a
    survivor by definition, and empty for a TIMEOUT because the run was killed
    before it could report anything."""

    reason: str | None = None
    """The accepted-equivalents ledger's reason for this mutant, if a live
    acceptance covers it."""

    @property
    def mutant(self) -> Mutant:
        """The mutant this verifies."""
        return self.result.mutant

    @property
    def status(self) -> str:
        """The fresh verdict."""
        return self.result.status

    def summary(self) -> dict[str, object]:
        """The verification as JSON-serialisable data.

        Follows :func:`moonbuggy.diffscope.scope_summary` and
        :meth:`moonbuggy.accepted.Acceptance.summary`: one place that decides
        which keys a structured consumer sees, so the human view and a future
        `--json` cannot describe the same measurement differently.

        Returns:
            A mapping with `id`, `status`, `file`, `line`, `operator`,
            `original`, `mutated`, `tests_run`, `selected`, `failed`,
            `nearest_test`, `duration`, `accepted` and `accept_reason`.
        """
        return {
            "id": self.mutant.id,
            "status": self.status,
            "file": self.mutant.module,
            "line": self.mutant.line,
            "operator": self.mutant.operator,
            "original": self.mutant.original,
            "mutated": self.mutant.mutated,
            "tests_run": self.result.tests_run,
            "selected": list(self.selected),
            "failed": list(self.failed),
            "nearest_test": self.result.nearest_test,
            "duration": round(self.result.duration, 4),
            "accepted": self.reason is not None,
            "accept_reason": self.reason,
        }


@dataclass(frozen=True)
class Explanation:
    """Why one mutant would be handled the way it would be, without running it.

    Everything here is a *decision* rather than a measurement: which tests
    selection picks and on what grounds, and whether the results cache already
    holds an answer for those inputs. Between them they settle the question
    `why` exists for -- "is my new test being ignored, or am I being served a
    stale verdict?" -- which no single field can answer on its own.
    """

    mutant: Mutant
    """The mutant, regenerated from the source as it stands now."""

    selected: tuple[str, ...]
    """Every test node id selection would choose, sorted. Empty means no test
    reaches the line, which is what makes a run report NO_COVERAGE."""

    selection: str
    """How that set was arrived at: `coverage` (tests the instrumented pass
    saw execute the line), `module_level` (an import-time line, which widens to
    the whole suite) or `suppressed` (a `no mutate` marker, so nothing runs)."""

    flaky: tuple[str, ...] = ()
    """Selected tests that disagreed with themselves between unmutated runs.
    Always empty unless probes were requested -- `why` measures nothing by
    default, and a flakiness probe is a measurement."""

    cache_key: str | None = None
    """The :meth:`~moonbuggy.cache.ResultCache.key_for` digest these inputs
    produce, or None when the caller asked for no cache."""

    cached: CacheRecord | None = None
    """The entry stored under `cache_key`, if there is one. Present means the
    next full run replays this instead of measuring."""

    last_run: Record | None = None
    """This mutant's record in `results.jsonl`, if the last run reported it.
    A *historical* verdict, and deliberately separate from `cached`: the two
    disagree exactly when something has changed since, which is worth seeing."""

    fingerprint_inputs: dict[str, object] | None = None
    """The run inputs mixed into the cache key by
    :func:`~moonbuggy.cache.run_fingerprint` -- `pytest_args`, `timeout` and
    `python`. Named here because a key nobody can see the inputs of is not an
    explanation."""

    reason: str | None = None
    """The accepted-equivalents ledger's reason for this mutant, if a live
    acceptance covers it."""

    @property
    def cache_covers(self) -> tuple[str, ...]:
        """Every file whose contents change this mutant's cache key.

        The mutated module plus one entry per selected test file. Edit any of
        them and the key changes, which is precisely why a new test cannot be
        hidden behind a stale hit -- provided selection picked it up, which is
        the other half of this report.
        """
        files = {self.mutant.module}
        files.update(node_id.split("::")[0] for node_id in self.selected)
        return tuple(sorted(files))

    @property
    def next_run(self) -> str:
        """What a full run would do with this mutant now, without doing it.

        Mirrors :func:`moonbuggy.runner._plan`'s order of decisions, which is
        the only way this can be a prediction rather than a guess: `skipped`
        for a suppressed mutant, then `suspicious` for a flaky selection, then
        `cache` when an entry exists, then `no_coverage` for an empty
        selection, and `measure` when a process really would be spent on it.
        """
        if self.mutant.suppressed:
            return "skipped"
        if self.flaky:
            return "suspicious"
        if self.cached is not None:
            return "cache"
        if not self.selected:
            return "no_coverage"
        return "measure"

    def summary(self) -> dict[str, object]:
        """The explanation as JSON-serialisable data.

        Follows :func:`moonbuggy.diffscope.scope_summary` and
        :meth:`Verification.summary`: one place decides which keys a structured
        consumer sees, so the human block and `--json` cannot describe the same
        mutant differently.

        Returns:
            A mapping with `id`, `file`, `line`, `operator`, `original`,
            `mutated`, `selection`, `selected`, `tests_run`, `flaky`,
            `next_run`, `cache_key`, `cache_covers`, `cache_hit`,
            `cached_status`, `cached_tests_run`, `run_inputs`,
            `last_run_status`, `last_run_tests_run`, `accepted` and
            `accept_reason`.
        """
        return {
            "id": self.mutant.id,
            "file": self.mutant.module,
            "line": self.mutant.line,
            "operator": self.mutant.operator,
            "original": self.mutant.original,
            "mutated": self.mutant.mutated,
            "selection": self.selection,
            "selected": list(self.selected),
            # The same name the result line uses, because explaining that
            # token is one of the things this command is for.
            "tests_run": len(self.selected),
            "flaky": list(self.flaky),
            "next_run": self.next_run,
            "cache_key": self.cache_key,
            "cache_covers": list(self.cache_covers),
            "cache_hit": self.cached is not None,
            "cached_status": None if self.cached is None else self.cached["status"],
            "cached_tests_run": (
                None if self.cached is None else self.cached["tests_run"]
            ),
            "run_inputs": self.fingerprint_inputs or {},
            "last_run_status": (
                None if self.last_run is None else self.last_run["status"]
            ),
            "last_run_tests_run": (
                None if self.last_run is None else self.last_run["tests_run"]
            ),
            "accepted": self.reason is not None,
            "accept_reason": self.reason,
        }


def explain(
    project_dir: str | os.PathLike[str],
    mutants: list[Mutant],
    source_dir: str | os.PathLike[str],
    probes: int = 0,
    extra_args: Iterable[str] = (),
    python: str | None = None,
    cache: ResultCache | None = None,
    fingerprint_inputs: dict[str, object] | None = None,
    reasons: Mapping[str, str] | None = None,
    records: Mapping[str, Record] | None = None,
) -> list[Explanation]:
    """Work out how each mutant would be handled, and run none of them.

    One coverage pass answers the selection question for every target at once,
    exactly as :func:`verify` does. What follows it is lookups: the cache key
    those inputs produce, and whether anything is stored under it.

    `probes` defaults to 0 rather than 1 here, unlike `verify`. Each probe is
    another whole unmutated suite run, and it buys only the SUSPICIOUS
    prediction -- a poor trade for a command whose selling point is that it
    answers without measuring. Pass a positive value to have flaky tests in the
    selection reported.

    Args:
        project_dir: the project root.
        mutants: the targets, in the order to report them.
        source_dir: the directory to measure coverage of.
        probes: extra unmutated suite runs used to detect flaky tests. 0 skips
            the detection rather than skipping the coverage pass.
        extra_args: pytest arguments added to the coverage pass. The same ones
            the run being explained uses, or this explains a different suite.
        python: interpreter for the coverage pass; None uses this one.
        cache: the :class:`~moonbuggy.cache.ResultCache` a run would consult,
            or None. Only ever read from -- `why` stores nothing, because it
            measures nothing worth storing.
        fingerprint_inputs: the `pytest_args`/`timeout`/`python` behind
            `cache`'s fingerprint, for reporting. Not used to derive anything.
        reasons: accepted-equivalents reasons by mutant id, as
            :meth:`moonbuggy.accepted.Resolution.reasons` returns.
        records: the last run's records by mutant id, as
            :func:`moonbuggy.report.read_jsonl` supplies them.

    Returns:
        One :class:`Explanation` per mutant, in the input order.

    Raises:
        BaselineError: if the suite is already failing or collects nothing.
            Selection derived from a red suite would not describe any run
            moonbuggy is willing to make.
        CoveragePassError: if the instrumented run could not complete.
    """
    project_dir = Path(project_dir)
    reasons = reasons or {}
    records = records or {}

    linemap, flaky = run_baseline_pass(
        project_dir,
        source_dir,
        probes,
        python=python or sys.executable,
        extra_args=extra_args,
    )
    check_selection_is_runnable(project_dir, linemap.all_tests())

    explanations = []
    for mutant in mutants:
        if mutant.suppressed:
            selected: tuple[str, ...] = ()
            selection = "suppressed"
        else:
            selected = tuple(sorted(linemap.select_for(mutant)))
            selection = "module_level" if mutant.module_level else "coverage"
        key: str | None = None
        cached: CacheRecord | None = None
        if cache is not None:
            key = cache.key_for(mutant, project_dir, selected)
            cached = cache.get(key)
        explanations.append(
            Explanation(
                mutant=mutant,
                selected=selected,
                selection=selection,
                flaky=tuple(sorted(flaky.intersection(selected))),
                cache_key=key,
                cached=cached,
                last_run=records.get(mutant.id),
                fingerprint_inputs=fingerprint_inputs,
                reason=reasons.get(mutant.id),
            )
        )
    return explanations


def resolve_targets(
    project_dir: str | os.PathLike[str],
    mutant_ids: Iterable[str],
    logging_policy: LoggingPolicy | None = None,
) -> list[Mutant]:
    """Find the mutant each id names, by regenerating the module it points at.

    Regeneration rather than a lookup in `results.jsonl`, deliberately. The
    caller is in the middle of editing, and the mutant it needs is the one that
    stands in the source *now* -- the previous run's record carries the source
    line as it was, which is the one thing a fix-verify loop must not be
    measured against. It also means the command works before any run has
    happened.

    Args:
        project_dir: the project root. Ids name modules relative to it.
        mutant_ids: ids as printed in `id=...`, in the order to run them.
        logging_policy: the run's logging policy. Pass the same one the full
            run used: it decides whether a mutant inside a logging call comes
            back suppressed, and an explanation built under a different policy
            describes a run nobody made.

    Returns:
        One :class:`~moonbuggy.mutant.Mutant` per id, in the same order.

    Raises:
        VerifyError: if an id is malformed, names a module that cannot be read,
            or names a mutant that module no longer produces.
    """
    project_dir = Path(project_dir)
    generated: dict[str, dict[str, Mutant]] = {}
    targets = []

    for mutant_id in mutant_ids:
        parsed = parse_id(mutant_id)
        if parsed is None:
            raise VerifyError(
                f"{mutant_id!r} is not a mutant id. Ids look like "
                "`path/to/file.py:14:comparison_swap:0` -- the `id=...` token "
                "on each result line."
            )
        module = parsed[0]
        if module not in generated:
            generated[module] = _mutants_in(project_dir, module, logging_policy)
        found = generated[module].get(mutant_id)
        if found is None:
            raise VerifyError(
                f"no mutant with id {mutant_id} in {module}. Its line may have "
                "moved or changed since the run that reported it, which gives "
                "it a new id -- run moonbuggy again to get the current one."
            )
        targets.append(found)
    return targets


def _mutants_in(
    project_dir: Path, module: str, logging_policy: LoggingPolicy | None
) -> dict[str, Mutant]:
    # Ids are unique within a module by construction (they carry an occurrence
    # index), so a dict loses nothing and makes the per-id lookup free.
    try:
        source = read_source(project_dir / module)
        found = generate_mutants(source, module=module, logging_policy=logging_policy)
    except (SourceError, GenerationError) as error:
        raise VerifyError(f"cannot read {module}: {error}") from error
    return {mutant.id: mutant for mutant in found}


def verify(
    project_dir: str | os.PathLike[str],
    mutants: list[Mutant],
    source_dir: str | os.PathLike[str],
    timeout: float = 30.0,
    workers: int = 0,
    probes: int = 1,
    extra_args: Iterable[str] = (),
    python: str | None = None,
    cache: ResultCache | None = None,
    reasons: Mapping[str, str] | None = None,
) -> list[Verification]:
    """Re-measure each mutant against the tests that cover it.

    One coverage pass serves every target, so verifying ten survivors costs
    barely more than verifying one. Each mutant then runs in its own pytest
    subprocess rather than a fork, because a forked child reports a single
    exit-code byte and this command has to name the tests that failed.

    Args:
        project_dir: the project root.
        mutants: the targets, in the order to report them.
        source_dir: the directory to measure coverage of.
        timeout: seconds before a mutant is called TIMEOUT.
        workers: pytest-xdist workers within each mutant's run.
        probes: extra unmutated suite runs used to detect flaky tests.
        extra_args: pytest arguments added to every run, baseline included.
        python: interpreter for the mutant runs; None uses this one.
        cache: a :class:`~moonbuggy.cache.ResultCache` to *write* fresh
            verdicts into, or None. Never read from -- see the module
            docstring.
        reasons: accepted-equivalents reasons by mutant id, as
            :meth:`moonbuggy.accepted.Resolution.reasons` returns.

    Returns:
        One :class:`Verification` per mutant, in the input order.

    Raises:
        BaselineError: if the suite is already failing or collects nothing.
        CoveragePassError: if the instrumented run could not complete.
    """
    project_dir = Path(project_dir)
    python = python or sys.executable
    reasons = reasons or {}

    linemap, flaky = run_baseline_pass(
        project_dir, source_dir, probes, python=python, extra_args=extra_args
    )
    check_selection_is_runnable(project_dir, linemap.all_tests())

    verifications = []
    with tempfile.TemporaryDirectory() as tmp:
        for index, mutant in enumerate(mutants):
            outcomes_file = Path(tmp) / f"outcomes-{index}.json"
            result = run_one(
                project_dir,
                mutant,
                linemap,
                timeout,
                python,
                xdist_workers=workers,
                # cache=None is the point of the command, not an oversight:
                # a hit here would answer the question with the previous
                # answer. The fresh verdict is stored below.
                cache=None,
                flaky=flaky,
                outcomes=outcomes_file,
            )
            selected = (
                () if mutant.suppressed else tuple(sorted(linemap.select_for(mutant)))
            )
            failed = tuple(
                sorted(
                    node_id
                    for node_id, outcome in read_outcomes(outcomes_file).items()
                    if outcome == "failed"
                )
            )
            if cache is not None and result.status not in _NOT_CACHEABLE:
                cache.put(
                    cache.key_for(mutant, project_dir, selected),
                    {
                        "status": result.status,
                        "tests_run": result.tests_run,
                        "nearest_test": result.nearest_test,
                    },
                )
            verifications.append(
                Verification(result, selected, failed, reasons.get(mutant.id))
            )
    return verifications
