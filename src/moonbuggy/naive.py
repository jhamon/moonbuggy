"""The naive reference implementation: mutate to disk, run the whole suite.

This is deliberately the slow, obvious way to do mutation testing, and it exists
for two reasons:

1. It is the generated half of the oracle (criterion A2a). Because it shares no
   code with the fast path -- no import hook, no coverage-guided selection, no
   cache, no xdist -- agreement between the two is real evidence rather than a
   tautology. The only thing they have in common is mutant generation, which is
   exactly the gap the hand-written inventory (A2b) covers.
2. It is the naive baseline the benchmark measures against (criterion G1).

Nothing here should ever be optimised. If it grows a coverage map or an import
hook it stops being an independent check and starts being a second copy of the
thing under test.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# pytest exit codes. 1 means tests failed, which is a kill. 2-5 mean pytest
# itself could not complete -- a collection error, an internal error, a usage
# error, or nothing collected. Those are not clean kills, and section 5.4
# reserves SUSPICIOUS for exactly that ambiguity.
PYTEST_OK = 0
PYTEST_TESTS_FAILED = 1


def run_naive(project_dir, mutants, timeout=30, python=None):
    """Run every mutant against the full suite. Returns {Mutant: status}."""
    python = python or sys.executable
    results = {}
    for mutant in mutants:
        if mutant.suppressed:
            results[mutant] = "SKIPPED"
            continue
        results[mutant] = _run_one(project_dir, mutant, timeout, python)
    return results


def _run_one(project_dir, mutant, timeout, python):
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "project"
        shutil.copytree(project_dir, tree, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
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


def _apply(path, mutant):
    """Replace the mutated line, preserving its original indentation.

    Mutant.mutated is stripped, so the indentation has to come back from the
    line being replaced -- writing it flush-left would be a syntax error inside
    any function body.
    """
    lines = path.read_text().splitlines(keepends=True)
    index = mutant.line - 1
    original = lines[index]
    indent = original[: len(original) - len(original.lstrip())]
    newline = "\n" if original.endswith("\n") else ""
    lines[index] = f"{indent}{mutant.mutated}{newline}"
    path.write_text("".join(lines))
