"""`moonbuggy run <id>`: re-measure one named mutant, on demand.

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
"""

import os
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .baseline import read_outcomes
from .cache import ResultCache
from .coverage_pass import run_baseline_pass
from .generate import GenerationError, generate_mutants
from .mutant import Mutant, parse_id
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


def resolve_targets(
    project_dir: str | os.PathLike[str], mutant_ids: Iterable[str]
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
            generated[module] = _mutants_in(project_dir, module)
        found = generated[module].get(mutant_id)
        if found is None:
            raise VerifyError(
                f"no mutant with id {mutant_id} in {module}. Its line may have "
                "moved or changed since the run that reported it, which gives "
                "it a new id -- run moonbuggy again to get the current one."
            )
        targets.append(found)
    return targets


def _mutants_in(project_dir: Path, module: str) -> dict[str, Mutant]:
    # Ids are unique within a module by construction (they carry an occurrence
    # index), so a dict loses nothing and makes the per-id lookup free.
    try:
        source = read_source(project_dir / module)
        found = generate_mutants(source, module=module)
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
