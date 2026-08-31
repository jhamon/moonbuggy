"""The naive reference implementation: mutate to disk, run the whole suite.

This is deliberately the slow, obvious way to do mutation testing, and it exists
for two reasons:

1. It is the independent half of the oracle. Because it shares no
   code with the fast path -- no import hook, no coverage-guided selection, no
   cache, no xdist -- agreement between the two is real evidence rather than a
   tautology. The only thing they have in common is mutant generation, which is
   exactly the gap a hand-written inventory of the operators covers.
2. It is the naive baseline the benchmark measures against.

Nothing here should ever be optimised. If it grows a coverage map or an import
hook it stops being an independent check and starts being a second copy of the
thing under test.
"""

import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from .mutant import Mutant
from .srcio import detect_encoding, encode_source, read_source, replace_line

# pytest exit codes. 1 means tests failed, which is a kill. 2-5 mean pytest
# itself could not complete -- a collection error, an internal error, a usage
# error, or nothing collected. Those are not clean kills, and section 5.4
# reserves SUSPICIOUS for exactly that ambiguity.
PYTEST_OK = 0
PYTEST_TESTS_FAILED = 1

# Mirrors report.py's STATUS_KEYWORDS, minus NO_COVERAGE and KILLED_BY_ERROR.
# Not imported from
# there because report.py keeps that set untyped as plain strings for the
# grep-friendly format; this is the one place a mutant's fate is decided, so it
# is worth a Literal here even though the two are not (yet) the same
# declaration.
#
# NO_COVERAGE is absent on purpose rather than by omission: the naive runner has
# no coverage map and runs the entire suite for every mutant, so "no test
# reaches this line" is not a distinction it is able to draw. An uncovered line
# whose mutation nothing notices is a SURVIVED here, and that disagreement with
# the fast path is a known, labelled one -- see `fast_path_status` in
# tests/fixtures/oracle.toml.
#
# KILLED_BY_ERROR is absent for a different reason, and a stronger one. Drawing
# it needs `moonbuggy.killreason` loaded into the process running the tests,
# and loading moonbuggy's own machinery into the reference implementation is
# exactly what the module docstring above forbids: the oracle's value is that
# it shares no code with the thing under test. So a crash-kill is a KILLED
# here, and where the two engines therefore differ the oracle labels it with
# `fast_path_status`, the same mechanism NO_COVERAGE already uses.
Status = Literal["SKIPPED", "SURVIVED", "KILLED", "TIMEOUT", "SUSPICIOUS"]


def run_naive(
    project_dir: Path,
    mutants: Iterable[Mutant],
    timeout: float = 30,
    python: str | None = None,
) -> dict[Mutant, Status]:
    """Run every unsuppressed mutant against the full suite.

    A suppressed mutant is settled SKIPPED without being run. Returns
    {Mutant: status}.
    """
    python = python or sys.executable
    results: dict[Mutant, Status] = {}
    for mutant in mutants:
        if mutant.suppressed:
            results[mutant] = "SKIPPED"
            continue
        results[mutant] = _run_one(project_dir, mutant, timeout, python)
    return results


def _run_one(project_dir: Path, mutant: Mutant, timeout: float, python: str) -> Status:
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "project"
        shutil.copytree(
            project_dir,
            tree,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
        _apply(tree / mutant.module, mutant)
        try:
            proc = subprocess.run(
                [python, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                cwd=tree,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return "TIMEOUT"

    if proc.returncode == PYTEST_OK:
        return "SURVIVED"
    if proc.returncode == PYTEST_TESTS_FAILED:
        return "KILLED"
    return "SUSPICIOUS"


def _apply(path: Path, mutant: Mutant) -> None:
    """Write the mutated line into the copied tree.

    Shares `replace_line` with the fast path, which is the one piece of the
    naive runner that is allowed to be common code: if the two applied
    mutations differently, a disagreement between them would be about text
    handling rather than about the thing the oracle is checking.
    """
    encoding = detect_encoding(path)
    mutated = replace_line(read_source(path), mutant.line, mutant.mutated)
    # Written back in the encoding the file declares, so a mutated latin-1
    # module still matches its own coding cookie.
    path.write_bytes(encode_source(mutated, encoding))
