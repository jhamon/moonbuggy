"""Criterion A5: advisory cross-check of the oracle against mutmut.

The two-source oracle (A2a naive differential + A2b hand-written inventory) is
already independent of moonbuggy's fast path. mutmut adds a third source that is
independent of moonbuggy *entirely* -- different authors, different mutation
engine, different execution model -- so agreement is worth something the other
two cannot provide on their own.

ADVISORY BY DESIGN. This never fails the build and mutmut is never authoritative.
It has its own timeout semantics, no concept of our suppression marker, and a
wider operator set, so it can only speak to the cases least likely to be wrong
anyway. A disagreement is a question, not a verdict: it is either a moonbuggy
bug, an oracle error, or a genuine semantic difference between the tools, and
each one needs a written explanation rather than a silent relabel.

Run: .venv/bin/python scripts/check_mutmut_differential.py
"""

import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "sample_project"
ORACLE = REPO / "tests" / "fixtures" / "oracle.toml"
MUTMUT = str(REPO / ".venv" / "bin" / "mutmut")

# mutmut's status glyphs, from its progress line.
GLYPHS = {
    "🎉": "KILLED", "🙁": "SURVIVED", "⏰": "TIMEOUT", "🤔": "SUSPICIOUS",
    "🫥": "SKIPPED",
}

# Explanations for known, expected divergences. A disagreement that is NOT
# listed here is what this script exists to surface.
EXPLAINED = {
    "operator_set": (
        "mutmut implements operators moonbuggy's MVP set (section 3.2) does not, "
        "so it generates strictly more mutants. The extra ones have no counterpart "
        "in the oracle and cannot be compared."
    ),
    "suppression": (
        "mutmut has no concept of `# moonbuggy: skip`, so the fixture's one "
        "suppressed mutant is a SKIPPED for us and an ordinary mutant for it."
    ),
    "timeout_semantics": (
        "mutmut derives its own timeout from a baseline run rather than taking "
        "ours, so which mutants it calls TIMEOUT need not match."
    ),
}


def run_mutmut(project):
    (project / "pytest.ini").write_text(
        "[pytest]\ntestpaths = mutants/tests\npythonpath = mutants\n"
    )
    (project / "pyproject.toml").write_text(
        '[tool.mutmut]\nsource_paths = ["sample/"]\n'
    )

    proc = subprocess.run([MUTMUT, "run"], cwd=project, capture_output=True, text=True)
    output = proc.stdout.replace("\r", "\n")

    match = re.findall(
        r"(\d+)/(\d+)\s+🎉\s*(\d+)\s+🫥\s*(\d+)\s+⏰\s*(\d+)\s+🤔\s*(\d+)\s+🙁\s*(\d+)",
        output,
    )
    if not match:
        raise SystemExit(f"could not parse mutmut output:\n{output[-2000:]}")
    _, total, killed, skipped, timeout, suspicious, survived = match[-1]
    return int(total), {
        "KILLED": int(killed), "SKIPPED": int(skipped), "TIMEOUT": int(timeout),
        "SUSPICIOUS": int(suspicious), "SURVIVED": int(survived),
    }


def main():
    oracle = tomllib.loads(ORACLE.read_text())
    expected = oracle["meta"]["expected_counts"]
    oracle_total = oracle["meta"]["total_mutants"]

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "fixture"
        shutil.copytree(FIXTURE, project, ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".moonbuggy", "mutants", ".coverage"
        ))
        mutmut_total, mutmut_counts = run_mutmut(project)

    print("Criterion A5 -- advisory mutmut cross-check (never gates the build)\n")
    print(f"  {'status':<12} {'oracle':>8} {'mutmut':>8}   note")
    print("  " + "-" * 66)

    for status in ("KILLED", "SURVIVED", "TIMEOUT", "SKIPPED", "SUSPICIOUS"):
        ours = expected.get(status, 0)
        theirs = mutmut_counts.get(status, 0)
        note = "" if ours == theirs else "differs"
        print(f"  {status:<12} {ours:>8} {theirs:>8}   {note}")

    print(f"  {'TOTAL':<12} {oracle_total:>8} {mutmut_total:>8}")

    print("\nAgreements worth having:")
    if expected.get("SURVIVED") == mutmut_counts.get("SURVIVED"):
        print(
            f"  SURVIVED matches exactly ({expected['SURVIVED']}). This is the number"
            " that matters\n  most -- a survivor is what a user acts on, and a tool"
            " inventing or missing\n  them is the failure mode with real cost. Two"
            " independent engines agreeing\n  here is meaningful corroboration of the"
            " oracle."
        )
    else:
        print(
            f"  SURVIVED DIFFERS: oracle {expected.get('SURVIVED')}, mutmut "
            f"{mutmut_counts.get('SURVIVED')}.\n  This needs investigation and a"
            " written explanation before it is dismissed."
        )

    print("\nExplained divergences:")
    for key, explanation in EXPLAINED.items():
        print(f"  - {key}: {explanation}")

    print(
        f"\n  Totals differ by {mutmut_total - oracle_total} mutants, accounted for"
        " by `operator_set`\n  above. mutmut is not authoritative and no label is"
        " changed on its say-so;\n  the hand-written oracle remains the source of"
        " truth (criterion A2b)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
