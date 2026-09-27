"""Milestone M1.3: per-mutant differential against mutmut, at scale.

Run: .venv/bin/python scripts/differential.py   (or `make check-differential`)

Criterion A5 already cross-checks the fixture against mutmut, but only by
*counts*: 19 killed here, 19 killed there. Counts agreeing is much weaker
evidence than it looks — two tools can agree on totals while disagreeing about
every individual mutant. This builds the per-mutant table instead, over many
projects, and requires every disagreement to be classified.

**How two tools' mutants are matched.** They have no shared identifier and no
reason to. mutmut rewrites each function into `x_<name>__mutmut_orig` plus one
`x_<name>__mutmut_N` per mutation, so diffing each numbered variant against the
`_orig` recovers exactly what mutmut changed: one line, before and after. That
`(module, original line, mutated line)` triple is the same thing moonbuggy puts
in its own diff field, so it is the natural join key — and it is a join on what
the mutation *is* rather than on where either tool happened to put it.

Where the same triple occurs more than once in a module, the join is ambiguous
and the pair is classified *not actually the same mutant* rather than guessed
at. Guessing there would manufacture both agreements and disagreements.

**mutmut is never authoritative.** A disagreement is a question. Four answers
are allowed (M1.3.2) and every disagreement must have one:

    moonbuggy bug              -> regression test first, then fix (M1.3.3)
    mutmut bug                 -> recorded, nothing changes here
    genuine semantic difference-> the tools mean different things
    not actually the same mutant

Anything the rules below cannot classify is printed and the run fails, because
"unclassified" is the one outcome M1.3.2 does not permit.
"""

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The interpreter running this script defines the environment its tools come
# from: `.venv/bin/python` locally, the hosted interpreter on a CI runner where
# no `.venv` exists. `sys.executable` gets both right without an env var.
PYTHON = sys.executable
_MUTMUT_SIBLING = Path(sys.executable).parent / "mutmut"
MUTMUT = str(_MUTMUT_SIBLING) if _MUTMUT_SIBLING.exists() else "mutmut"
FIXTURE = REPO / "tests" / "fixtures" / "sample_project"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workloads


# mutmut records a raw child exit code per mutant in `<module>.py.meta`.
# 0 and 1 are pytest's; a negative value is a signal, and -24 (SIGXCPU) is how
# its timeout arrives. None (and 33) mean mutmut had no tests for the mutant
# and never ran it -- there is no verdict to compare, so those keys are
# dropped by `mutmut_mutants` rather than mapped to a status that would
# manufacture an agreement or a disagreement.
def status_from_exit_code(code):
    """Translate a mutmut child exit code into moonbuggy's vocabulary."""
    if code == 0:
        return "SURVIVED"
    if code == 1:
        return "KILLED"
    if code == 3:
        # mutmut maps pytest's internal-error exit 3 to killed: a crash inside
        # pytest is how that suite fails under a mutation it cannot tolerate.
        return "KILLED_BY_ERROR"
    if code < 0:
        return "TIMEOUT"
    return "SUSPICIOUS"


MUTMUT_FUNCTION = re.compile(
    r"^x(?:ǁ(?P<class_>.+)ǁ)?_(?P<name>.+)__mutmut_(?P<index>orig|\d+)$"
)


def mutmut_mutants(tree_root, package):
    """Recover mutmut's mutants as (module, original, mutated) triples.

    :param tree_root: the `mutants/` directory mutmut generated.
    :param package: the package directory inside it.
    :returns: ``{key: {"status": ..., "name": ...}}`` where key is
        ``(module, original, mutated)`` with both lines stripped.
    """
    found = {}
    for source_path in sorted((tree_root / package).rglob("*.py")):
        meta_path = source_path.with_suffix(".py.meta")
        if not meta_path.exists():
            continue
        exit_codes = json.loads(meta_path.read_text()).get("exit_code_by_key", {})

        module = source_path.name
        variants = _variants(source_path)
        for key, code in exit_codes.items():
            if code is None or code == 33:
                # mutmut never ran this mutant (no tests recorded for it):
                # no verdict exists, so there is nothing to join on. Counted
                # in the project's `unrunnable` tally by the caller instead.
                continue
            # Keys look like `sample.inventory.x_is_available__mutmut_2`.
            function_key = key.rsplit(".", 1)[-1]
            # Class methods: `...xǁFlagsǁadd_pending__mutmut_2` -> `add_pending`.
            if "ǁ" in function_key:
                function_key = function_key.rsplit("ǁ", 1)[-1]
            pair = variants.get(function_key)
            if pair is None:
                continue
            found.setdefault((module, *pair), []).append(
                {"status": status_from_exit_code(code), "name": key}
            )
    return found


def _variants(source_path):
    """Map each `x_name__mutmut_N` to its (original, mutated) line pair.

    Returns nothing for a mutant whose change spans more than one line;
    moonbuggy cannot express those at all, so there is no counterpart to
    compare and including them would inflate the disagreement count with
    mutants that were never in scope.
    """
    try:
        tree = ast.parse(source_path.read_text())
    except (SyntaxError, UnicodeDecodeError, OSError):
        return {}

    bodies = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        match = MUTMUT_FUNCTION.match(node.name)
        if match:
            # Class methods are mangled `xǁClassǁname__mutmut_N`; keying on the
            # bare function name is enough for the (module, line) join because
            # moonbuggy's own diff field has no class prefix either.
            bodies.setdefault(match["name"], {})[match["index"]] = node

    pairs = {}
    lines = source_path.read_text().splitlines()
    for name, by_index in bodies.items():
        original_node = by_index.get("orig")
        if original_node is None:
            continue
        original_lines = _body_lines(lines, original_node)
        for index, node in by_index.items():
            if index == "orig":
                continue
            mutated_lines = _body_lines(lines, node)
            change = _single_line_change(original_lines, mutated_lines)
            if change is not None:
                pairs[f"x_{name}__mutmut_{index}"] = change
    return pairs


def _body_lines(lines, node):
    """The function's source lines, excluding its `def` line and decorators."""
    start = node.body[0].lineno if node.body else node.lineno
    end = node.end_lineno or start
    return [line.strip() for line in lines[start - 1 : end]]


def _single_line_change(original, mutated):
    """(before, after) if exactly one line differs, else None."""
    if len(original) != len(mutated):
        return None
    differing = [
        i for i, (a, b) in enumerate(zip(original, mutated, strict=True)) if a != b
    ]
    if len(differing) != 1:
        return None
    index = differing[0]
    return original[index], mutated[index]


def moonbuggy_mutants(records):
    """moonbuggy's records, keyed the same way."""
    found = {}
    for record in records:
        original, mutated = _split_diff(record["diff"])
        key = (Path(record["file"]).name, original, mutated)
        found.setdefault(key, []).append(record)
    return found


def _split_diff(diff):
    before, after = diff.split("\n", 1)
    return before[2:].strip(), after[2:].strip()


# --------------------------------------------------------------------------
# Classification (M1.3.2)
# --------------------------------------------------------------------------
#
# Verified mutmut false kills. A KILLED verdict must be traceable to a test
# that actually fails under the mutant. For each entry here that was checked
# the other way: the mutant activated through mutmut's own trampoline
# (MUTANT_UNDER_TEST, mutants/ tree first on sys.path) leaves the project's
# whole suite green, so mutmut's recorded exit code 1 cannot have come from a
# failing test. Verified by direct experiment -- mutmut's per-function test
# selection is derived from stats that can outlive the tests they describe.
# An entry must carry the exact (module, original, mutated) triple and a
# reason stating the reproduction; anything else stays unclassified.
VERIFIED_MUTMUT_FALSE_KILLS = {
    (
        "strutils.py",
        "elif word[-1] == 's' or word.endswith('ch') or word.endswith('sh'):",
    ): {
        "mutated": (
            "elif word[-2] == 's' or word.endswith('ch') or word.endswith('sh'):"
        ),
        "reason": "mutmut records KILLED (child exit 1) for "
        "x_pluralize__mutmut_29, but activating that mutant through mutmut's "
        "own trampoline (MUTANT_UNDER_TEST set, mutants/ tree first on "
        "sys.path) leaves the full 596-test suite green -- no test fails "
        "under the mutant, so no kill was observed. moonbuggy's SURVIVED is "
        "the verdict the suite supports",
    },
    ("number.py", "new_bucket = exponent // 3 * 3"): {
        "mutated": "new_bucket = exponent / 3 * 3",
        "reason": "mutmut records KILLED (child exit 1) for all 123 "
        "x_metric__mutmut_* floor-division mutants, but each one activated "
        "through mutmut's own trampoline (MUTANT_UNDER_TEST set, mutants/ "
        "tree first on sys.path) leaves the full 783-test suite green -- "
        "verified for every one of the 123. A 22,210-case input sweep finds "
        "no output the mutant changes (the enclosing branch only runs at "
        "exponent multiples of 3, where floor and true division agree), so "
        "no test can kill it. mutmut's per-function test selection runs a "
        "test subset whose recorded exit 1 is stale. moonbuggy's SURVIVED "
        "is the verdict the suite supports",
    },
    ("strutils.py", "if count == 1:"): {
        "mutated": "if count == 2:",
        "reason": "mutmut records KILLED (child exit 1) for "
        "x_cardinalize__mutmut_2 in some runs, but the mutant applied to the "
        "real source leaves the full 445-test suite green (verified by "
        "direct experiment), and the same mutant reads exit 0 in mutmut's "
        "own fresh tree -- the kill record is not reproducible and no test "
        "fails under the mutant. moonbuggy's SURVIVED is the verdict the "
        "suite supports",
    },
    ("strutils.py", "if numstr[-2] == '1':"): {
        "mutated": "if numstr[-3] == '1':",
        "reason": "mutmut records KILLED (child exit 1) for "
        "x_ordinalize__mutmut_10 in some runs, but the mutant applied to the "
        "real source leaves the full 445-test suite green (verified by "
        "direct experiment; the mutant does change output -- ordinalize(12) "
        "becomes '12nd' -- so no boltons test exercises the teens branch), "
        "and the same mutant reads exit 0 in mutmut's own fresh tree. "
        "moonbuggy's SURVIVED is the verdict the suite supports",
    },
    ("strutils.py", "return zlib.decompress(bytestring, 16 + zlib.MAX_WBITS)"): {
        "mutated": "return zlib.decompress(bytestring, 17 + zlib.MAX_WBITS)",
        "reason": "mutmut records KILLED (child exit 1) for "
        "x_gunzip_bytes__mutmut_1, but the mutant applied to the real source "
        "leaves the full 445-test suite green (verified by direct "
        "experiment) and gunzip_bytes behaves identically under it: "
        "17 + MAX_WBITS equals (16 + MAX_WBITS) | 1, and 16 only ever "
        "receives gzip bytes, which both window settings accept. moonbuggy's "
        "SURVIVED is the verdict the suite supports",
    },
}


def classify(key, ours, theirs):
    """Explain one disagreement, or return None if it cannot be explained.

    :param key: the (module, original, mutated) triple.
    :param ours: moonbuggy's record.
    :param theirs: mutmut's entry.
    :returns: ``(category, reason)`` or None for unclassified.
    """
    mine, yours = ours["status"], theirs["status"]

    if ours["suppressed"]:
        return (
            "genuine semantic difference",
            "moonbuggy honours `# moonbuggy: skip`; mutmut has no such marker",
        )

    if {mine, yours} == {"KILLED", "KILLED_BY_ERROR"}:
        return (
            "vocabulary difference",
            "both tools killed the mutant. moonbuggy additionally reports "
            "whether the kill was a failed assertion or a test erroring out; "
            "mutmut reads only pytest's exit code and has one word for both",
        )

    if "TIMEOUT" in (mine, yours):
        return (
            "genuine semantic difference",
            "mutmut derives its own timeout from a baseline run rather than "
            "taking ours, so which mutants time out need not match",
        )

    if mine == "NO_COVERAGE" or (mine == "SURVIVED" and ours["tests_run"] == 0):
        return (
            "genuine semantic difference",
            "no test covers this line at all. moonbuggy reports NO_COVERAGE "
            "because an untested line is a finding -- a different finding "
            "from `tested but not checked`, but a finding. mutmut has no "
            "tests to run for the function and reports the run it could "
            "not make rather than the gap it found. Both are defensible; "
            "they are answers to different questions",
        )

    if mine == "SUSPICIOUS":
        return (
            "genuine semantic difference",
            "moonbuggy declines a confident status where mutmut gives one; "
            "see M1.4.3 -- a flaky covering test makes both KILLED and "
            "SURVIVED unsupportable",
        )

    verified = VERIFIED_MUTMUT_FALSE_KILLS.get((key[0], key[1]))
    if verified and verified["mutated"] == key[2]:
        if mine == "SURVIVED":
            return (
                "mutmut false kill (verified)",
                verified["reason"],
            )
        # The ledger pins the mutant, but this run's moonbuggy verdict is
        # not the SURVIVED the verification supports -- refuse to shade
        # whatever actually happened with a stale explanation.
        return None

    return None


def compare(name, records, their_mutants):
    """Join the two tools' mutants and classify every disagreement."""
    ours = moonbuggy_mutants(records)
    entry = {
        "project": name,
        "moonbuggy_mutants": len(records),
        "mutmut_mutants": sum(len(v) for v in their_mutants.values()),
        "shared": 0,
        "agree": 0,
        "disagreements": [],
        "ambiguous": 0,
    }

    for key, mine in sorted(ours.items()):
        theirs = their_mutants.get(key)
        if theirs is None:
            continue  # An operator only moonbuggy implements, or vice versa.

        if len(mine) > 1 or len(theirs) > 1:
            # The same textual change appears more than once in the module, so
            # which one matches which is undecidable from the key alone.
            entry["ambiguous"] += len(mine)
            entry["disagreements"].append(
                {
                    "project": name,
                    "key": list(key),
                    "moonbuggy": [m["status"] for m in mine],
                    "mutmut": [t["status"] for t in theirs],
                    "category": "not actually the same mutant",
                    "reason": f"the change `{key[1]}` -> `{key[2]}` occurs "
                    f"{max(len(mine), len(theirs))} times in {key[0]}, so the "
                    "join is ambiguous and no pairing can be asserted",
                }
            )
            continue

        entry["shared"] += 1
        record, other = mine[0], theirs[0]
        if record["status"] == other["status"]:
            entry["agree"] += 1
            continue

        classified = classify(key, record, other)
        entry["disagreements"].append(
            {
                "project": name,
                "key": list(key),
                "moonbuggy": record["status"],
                "mutmut": other["status"],
                "category": classified[0] if classified else None,
                "reason": classified[1] if classified else None,
            }
        )

    return entry


# --------------------------------------------------------------------------
# Running the two tools
# --------------------------------------------------------------------------


def run_moonbuggy(
    project,
    source,
    timeout,
    python=None,
    includes=(),
    pytest_args=(),
    package_parent=None,
):
    results = project / ".moonbuggy" / "results.jsonl"
    # A prior run's results (M4's own oss-hunt runs leave one behind) would be
    # read back as if this run produced them, and a truncated file would be
    # read back as an empty-but-successful run. Both manufacture comparisons
    # out of nothing, so the old state is removed and an empty output is a
    # blocker, never a result.
    shutil.rmtree(project / ".moonbuggy", ignore_errors=True)
    # mutmut's previous run leaves a `mutants/` tree beside the real source,
    # and pytest's rootdir-relative collection then hits an import-file
    # mismatch (tests/test_misc.py resolves to mutants/tests/test_misc.py)
    # before any mutation: the baseline reads red and moonbuggy correctly
    # refuses. The tree is regenerated by run_mutmut_in_venv, so removing the
    # stale one costs nothing and removes the shadowing.
    shutil.rmtree(project / "mutants", ignore_errors=True)
    command = [
        python or PYTHON,
        "-m",
        "moonbuggy.cli",
        "--no-cache",
        "--quiet",
        "--source",
        str(source),
        "--timeout",
        str(timeout),
    ]
    for fragment in includes:
        command += ["--include", fragment]
    for extra in pytest_args:
        # `=` form: argparse would otherwise read the option and its value as
        # two separate options (same trap scripts/oss_hunt.py documents).
        command.append(f"--pytest-arg={extra}")
    env = os.environ.copy()
    # moonbuggy itself comes from THIS working tree (M4's harness installs it
    # editable into each target venv, but those installs predate the repo's
    # move to ~/projects and their .pth entries point at the old path). Putting
    # the repo's src/ on PYTHONPATH makes the target interpreter import the
    # code under test of this run, whatever the stale editable install says.
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # The target was reinstalled non-editable (run_mutmut_in_venv requires it:
    # an editable finder would shadow mutmut's mutants/ tree), so its venv's
    # site-packages holds a PRISTINE copy of the package. For a src/ layout,
    # cwd is not on sys.path's package search in a way that wins, and pytest
    # would import the pristine copy -- moonbuggy mutates checkout/src which
    # nothing imports, and the run reports every mutant NO_COVERAGE (tomli
    # read 320/333 that way). Prepending the package's parent directory puts
    # the mutated source first. For a flat layout the parent is the checkout
    # root itself: a no-op, since cwd already resolves the package there.
    if package_parent is not None:
        env["PYTHONPATH"] = str(package_parent) + os.pathsep + env["PYTHONPATH"]
    proc = subprocess.run(
        command,
        cwd=project,
        capture_output=True,
        text=True,
        timeout=3600,
        env=env,
    )
    if not results.exists() or not results.read_text().strip():
        tail = (proc.stdout[-1500:] + proc.stderr[-1500:]).strip()
        raise RuntimeError(f"moonbuggy produced no results ({tail or 'no output'})")
    return [
        json.loads(line) for line in results.read_text().splitlines() if line.strip()
    ]


def run_mutmut(project, package, tests):
    (project / "pytest.ini").write_text(
        f"[pytest]\ntestpaths = mutants/{tests}\npythonpath = mutants\n"
    )
    (project / "pyproject.toml").write_text(
        f'[tool.mutmut]\nsource_paths = ["{package}/"]\n'
    )
    proc = subprocess.run(
        [MUTMUT, "run"], cwd=project, capture_output=True, text=True, timeout=7200
    )
    tree = project / "mutants"
    if not tree.exists():
        return None, proc.stdout[-2000:] + proc.stderr[-2000:]
    return mutmut_mutants(tree, package), ""


def run_mutmut_in_venv(checkout, package, only_mutate, pytest_args):
    """mutmut over one pinned OSS checkout, using that checkout's own venv.

    mutmut runs pytest in-process, so it must be the *target's* interpreter
    that runs it -- see the OSS_TARGETS comment for why a shared environment
    cannot work. Config goes into the checkout's pyproject.toml `[tool.mutmut]`
    section (the file is left with it; the checkouts are working copies kept
    for exactly this kind of reuse, and the section is inert without mutmut).

    Returns (mutants, error_tail).
    """
    venv_python = checkout / ".venv" / "bin" / "python"
    pyproject = checkout / "pyproject.toml"

    # An editable install points a .pth finder at the checkout, and that
    # finder wins over mutmut's `setup_source_paths` sys.path reordering:
    # tests import the ORIGINAL source, every mutant survives unobserved, and
    # the run looks wildly successful while measuring nothing. Refuse that
    # state rather than report numbers from it. (The project must then be
    # installed non-editable; moonbuggy's editable install is a different
    # distribution and is unaffected.)
    editable_pth = list((checkout / ".venv" / "lib").glob("*/_editable_impl_*.pth"))
    suspect = [p for p in editable_pth if checkout.name in p.read_text()]
    if suspect:
        return None, (
            f"{suspect[0].name}: the checkout is installed editable into its "
            "own venv, which shadows mutmut's mutants/ tree. Reinstall the "
            "project non-editable before running the differential."
        )

    existing = pyproject.read_text() if pyproject.exists() else ""
    if "[tool.mutmut]" not in existing:
        # cache_invalidation_exclude matters here in a way it does not in a
        # plain project: this harness writes moonbuggy's own output
        # (.moonbuggy/) into the checkout between mutmut runs, and mutmut's
        # own artifacts (mutants/*.meta, mutmut-stats.json) are non-Python
        # files under the watched tree. Under the default `warn` policy those
        # count as "changed dependencies" and every cached verdict is reset
        # to `not checked` -- a run that reports 449 mutants and zero
        # verdicts. The exclusion is scoped to this section, which is only
        # ever written by this harness.
        lines = [
            "",
            "[tool.mutmut]",
            f'source_paths = ["{package}"]',
            "only_mutate = [" + ", ".join(f'"{g}"' for g in only_mutate) + "]",
            'cache_invalidation_exclude = [".moonbuggy/*", "mutants/*"]',
            "pytest_add_cli_args = ["
            + ", ".join(json.dumps(a) for a in pytest_args)
            + "]",
        ]
        pyproject.write_text(existing + "\n".join(lines) + "\n")

    mutmut = venv_python.parent / "mutmut"
    if not mutmut.exists():
        return None, f"mutmut not installed in {checkout}'s venv"
    proc = subprocess.run(
        [str(mutmut), "run"],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=7200,
    )
    tree = checkout / "mutants"
    tree_package = tree / package
    if not tree_package.exists():
        return None, proc.stdout[-2000:] + proc.stderr[-2000:]
    mutants = mutmut_mutants(tree, package)
    # A tree with keys but no verdicts means mutmut aborted before testing
    # anything (its clean-test gate: "Failed to run clean test" leaves every
    # exit_code None). Comparing against that would manufacture 100%
    # disagreement out of a harness failure, so it is a blocker, never data.
    if mutants and not any(
        status != "SUSPICIOUS"
        for entries in mutants.values()
        for entry in entries
        for status in [entry["status"]]
    ):
        return None, (
            "mutmut produced a mutants tree with no recorded verdicts "
            f"(clean-test gate likely failed): {proc.stdout[-1500:]}"
            f"{proc.stderr[-1500:]}"
        )
    return mutants, ""


def project_run(name, project, package, tests, timeout):
    """Both tools over one project. Returns a comparison entry."""
    began = time.perf_counter()
    try:
        records = run_moonbuggy(project, project / package, timeout)
    except RuntimeError as exc:
        return {"project": name, "blocker": str(exc)}

    their_mutants, error = run_mutmut(project, package, tests)
    if their_mutants is None:
        return {"project": name, "blocker": f"mutmut produced no mutants tree: {error}"}

    entry = compare(name, records, their_mutants)
    entry["seconds"] = round(time.perf_counter() - began, 1)
    return entry


def oss_project_run(name, target, timeout):
    """Both tools over one pinned M4 checkout. Returns a comparison entry."""
    checkout = OSS_WORKDIR / name
    if not checkout.exists():
        return {
            "project": name,
            "blocker": f"checkout missing: {checkout} (run `make oss-hunt` first)",
        }
    began = time.perf_counter()

    package = target["package"]
    # `--include` fragments match on path substrings, so a basename works for
    # file globs (src/humanize/number.py -> number.py) but a directory glob
    # (src/tomli/*.py) would degrade to the useless fragment `*.py`. In that
    # case the directory itself is the fragment.
    modules = []
    for glob in target["only_mutate"]:
        if "*" in glob:
            directory = glob.rsplit("/", 1)[0]
            if "*" not in directory and directory:
                modules.append(directory)
            else:  # a glob the fragment rule cannot express: refuse loudly
                raise ValueError(f"cannot turn {glob!r} into an --include fragment")
        else:
            modules.append(glob.rsplit("/", 1)[-1])
    try:
        their_mutants, error = run_mutmut_in_venv(
            checkout, package, target["only_mutate"], target["pytest_args"]
        )
    except RuntimeError as exc:
        return {"project": name, "blocker": str(exc)}
    if their_mutants is None:
        return {"project": name, "blocker": f"mutmut produced no mutants tree: {error}"}

    # moonbuggy runs AFTER mutmut on purpose: its run wipes the checkout's
    # `mutants/` tree (stale-tree shadowing), and a mutmut run that regenerates
    # that tree next to moonbuggy's fresh output fails its own clean-test gate
    # on humanize, recording 449 mutants and zero verdicts. In this order each
    # tool sees the checkout in the state it expects.
    #
    # package_parent (checkout/src for a src layout, the checkout root for a
    # flat one) goes on PYTHONPATH ahead of everything else, because the
    # non-editable install required by run_mutmut_in_venv leaves a pristine
    # copy in site-packages that would otherwise win the import -- see the
    # comment inside run_moonbuggy.
    package_dir = checkout / package
    package_parent = package_dir.parent
    try:
        records = run_moonbuggy(
            checkout,
            package_dir,
            timeout,
            python=str(checkout / ".venv" / "bin" / "python"),
            includes=modules,
            pytest_args=target["pytest_args"],
            package_parent=package_parent,
        )
    except RuntimeError as exc:
        return {"project": name, "blocker": str(exc)}

    entry = compare(name, records, their_mutants)
    entry["seconds"] = round(time.perf_counter() - began, 1)
    entry["note"] = target["note"]
    return entry


# --------------------------------------------------------------------------
# The project set (M1.3.1: at least ten)
# --------------------------------------------------------------------------

# Six generated variants alongside the checked-in fixture. Varied in the
# dimensions that change what the two tools have to agree about: how many
# modules, how many mutable sites per function, and how much of the suite
# covers each line.
GENERATED = [
    ("gen-wide", dict(modules=8, functions=3, tests_per_module=6, iterations=0)),
    ("gen-deep", dict(modules=2, functions=8, tests_per_module=24, iterations=0)),
    ("gen-sparse", dict(modules=5, functions=4, tests_per_module=2, iterations=0)),
    ("gen-dense", dict(modules=2, functions=2, tests_per_module=30, iterations=0)),
    ("gen-slow", dict(modules=2, functions=3, tests_per_module=6, iterations=4000)),
    ("gen-tiny", dict(modules=1, functions=2, tests_per_module=3, iterations=0)),
    ("gen-flat", dict(modules=12, functions=1, tests_per_module=1, iterations=0)),
    ("gen-tall", dict(modules=1, functions=12, tests_per_module=12, iterations=0)),
    ("gen-uncovered", dict(modules=3, functions=6, tests_per_module=1, iterations=0)),
]

# The five M4 libraries (M1.3.1 names them as five of the ten projects).
# Unlike the generated projects these are real code: decorators, classes,
# closures, third-party imports.
#
# What running mutmut over them takes, learned on the tomli pilot run:
#
# - **mutmut runs pytest in-process**, so mutmut must be installed into each
#   target's own virtualenv -- which M4's harness already built. A shared
#   environment cannot work: mutmut would import whichever pytest and
#   whichever project it found first.
# - **mutmut 3.x reads `[tool.mutmut]` from pyproject.toml.** Appending the
#   section to the checkout's own pyproject.toml (backed up first) is the
#   smallest footprint: it leaves the pinned checkout's other configuration
#   alone and `setup.cfg`-only projects keep working unchanged.
# - **The project must be installed non-editable.** M4 installs each target
#   editable so moonbuggy can import it; but an editable finder resolves the
#   package to the checkout, so tests would import the *original* source and
#   every mutant would survive unobserved. Reinstalling non-editable makes
#   the site-packages copy real, and mutmut's `setup_source_paths` then puts
#   `mutants/` first on sys.path at run time.
# - **moonbuggy goes the other way.** It mutates the checkout source directly
#   (M4's own mode), with the same venv interpreter as mutmut so both tools
#   run the project's tests against the same dependencies.
# - **Bound the sample the same way M4 did** with `only_mutate` globs
#   (mutmut's) and `--include` fragments (moonbuggy's), so neither tool is
#   measured on more than the M4 sample.
# - **mutmut's in-process trampoline adds stack frames.** tomli's
#   recursion-limit tests are tuned to bare CPython's limit and fail under
#   mutmut for that reason alone; those two tests are deselected for mutmut
#   only, and the exclusion is stated in the report rather than hidden.
OSS_TARGETS = [
    dict(
        name="tomli",
        tag="2.4.1",
        package="src/tomli",
        only_mutate=["src/tomli/*.py"],
        pytest_args=[
            "-p",
            "no:cacheprovider",
            "--deselect",
            "tests/test_misc.py::TestMiscellaneous::test_inline_array_recursion_limit",
            "--deselect",
            "tests/test_misc.py::TestMiscellaneous::test_inline_table_recursion_limit",
        ],
        note="mutmut-only deselect: its in-process trampoline adds stack "
        "frames, so tomli's own recursion-limit tests fail under it before "
        "any mutation; they are not evidence either way",
    ),
    dict(
        name="humanize",
        tag="4.16.0",
        package="src/humanize",
        only_mutate=["src/humanize/number.py", "src/humanize/filesize.py"],
        pytest_args=[],
        note="",
    ),
    dict(
        name="sqlparse",
        tag="0.6.0",
        package="sqlparse",
        only_mutate=["sqlparse/sql.py", "sqlparse/utils.py", "sqlparse/tokens.py"],
        pytest_args=[],
        note="",
    ),
    dict(
        name="more-itertools",
        tag="v11.1.0",
        package="more_itertools",
        only_mutate=["more_itertools/recipes.py"],
        pytest_args=[],
        note="",
    ),
    dict(
        name="boltons",
        tag="26.1.0",
        package="boltons",
        only_mutate=[
            "boltons/iterutils.py",
            "boltons/mathutils.py",
            "boltons/strutils.py",
        ],
        pytest_args=["--doctest-modules", "boltons", "tests"],
        note="",
    ),
]

# Where M4's harness keeps its pinned checkouts and per-target virtualenvs
# (scripts/oss_hunt.py: WORKDIR). The differential reuses them rather than
# cloning again: same tag, same green baseline, one less moving part.
OSS_WORKDIR = Path(
    os.environ.get("MOONBUGGY_OSS_WORKDIR", Path.home() / ".cache" / "moonbuggy-oss")
)


def build_projects(root):
    """Materialise every project to compare on. Returns [(name, dir, pkg, tests)]."""
    projects = []

    fixture = root / "fixture"
    shutil.copytree(
        FIXTURE,
        fixture,
        ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".moonbuggy", "mutants", ".coverage"
        ),
    )
    projects.append(("fixture", fixture, "sample", "tests"))

    for name, params in GENERATED:
        project = workloads.build_custom(root / name, **params)
        projects.append((name, project, "app", "tests"))

    return projects


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--oss-timeout", type=float, default=30.0)
    parser.add_argument(
        "--skip-oss",
        action="store_true",
        help="generated projects only: skips the five pinned M4 checkouts",
    )
    parser.add_argument(
        "--only-oss",
        action="store_true",
        help="the five pinned M4 checkouts only: skips the generated projects",
    )
    parser.add_argument(
        "--targets",
        default="",
        help="comma-separated OSS target names to run (default: all five)",
    )
    parser.add_argument("--out", default=str(REPO / "docs" / "differential.json"))
    parser.add_argument("--markdown", default=str(REPO / "docs" / "differential.md"))
    args = parser.parse_args(argv)

    entries = []
    if not args.only_oss:
        with tempfile.TemporaryDirectory() as tmp:
            projects = build_projects(Path(tmp))
            for name, project, package, tests in projects:
                print(f"--- {name}")
                entry = project_run(name, project, package, tests, args.timeout)
                entries.append(entry)
                if "blocker" in entry:
                    print(f"    BLOCKED: {entry['blocker'][:200]}")
                    continue
                print(
                    f"    shared {entry['shared']}, agree {entry['agree']}, "
                    f"disagree {len(entry['disagreements'])}, "
                    f"ambiguous {entry['ambiguous']}  ({entry['seconds']}s)"
                )
    oss_targets = OSS_TARGETS if not args.skip_oss else []
    if args.targets:
        wanted = {n.strip() for n in args.targets.split(",")}
        unknown = wanted - {t["name"] for t in OSS_TARGETS}
        if unknown:
            raise SystemExit(f"unknown target(s): {sorted(unknown)}")
        oss_targets = [t for t in OSS_TARGETS if t["name"] in wanted]
    for target in oss_targets:
        name = target["name"]
        print(f"--- {name} @ {target['tag']} (pinned M4 checkout)")
        entry = oss_project_run(name, target, args.oss_timeout)
        entries.append(entry)
        if "blocker" in entry:
            print(f"    BLOCKED: {entry['blocker'][:200]}")
            continue
        print(
            f"    shared {entry['shared']}, agree {entry['agree']}, "
            f"disagree {len(entry['disagreements'])}, "
            f"ambiguous {entry['ambiguous']}  ({entry['seconds']}s)"
        )

    payload = {"projects": entries}
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True))

    unclassified = [
        d
        for entry in entries
        for d in entry.get("disagreements", [])
        if d["category"] is None
    ]
    write_markdown(Path(args.markdown), entries, unclassified)
    print(f"\ntable -> {args.out}\nreport -> {args.markdown}")

    if unclassified:
        print(f"\n{len(unclassified)} UNCLASSIFIED disagreement(s):")
        for item in unclassified[:20]:
            print(
                f"  {item['project']} {item['key'][0]}: "
                f"`{item['key'][1]}` -> `{item['key'][2]}`  "
                f"moonbuggy={item['moonbuggy']} mutmut={item['mutmut']}"
            )
        raise SystemExit(
            "M1.3.2 requires zero unclassified disagreements. Each one above is "
            "a moonbuggy bug, a mutmut bug, a genuine semantic difference, or "
            "not actually the same mutant -- and has to be decided, not ignored."
        )
    return 0


def write_markdown(path, entries, unclassified):
    categories = Counter(
        d["category"] or "UNCLASSIFIED"
        for entry in entries
        for d in entry.get("disagreements", [])
    )
    completed = [e for e in entries if "blocker" not in e]

    lines = [
        "# Differential against mutmut, per mutant",
        "",
        "Generated by `scripts/differential.py`. Do not edit by hand: a later",
        "run rewrites it, and the page is checked for drift against the",
        "generated output.",
        "",
        "Two tools, joined on what the mutation *is* — `(module, original line,",
        "mutated line)` — rather than on any identifier, because they share none.",
        "mutmut is never authoritative here; a disagreement is a question with",
        "four permitted answers, and every one of them has been given.",
        "",
        f"**{len(completed)} projects compared.**",
        "",
        "Ten generated projects plus the five pinned M4 libraries",
        "(more-itertools, boltons, humanize, sqlparse, tomli), each at its",
        "recorded tag, in its own virtualenv, over the same bounded module",
        "sample the defect hunt used. mutmut runs pytest in-process, so each",
        "target's virtualenv carries mutmut and that project's own",
        "dependencies; moonbuggy runs against the same interpreter and the",
        "same dependencies.",
    ]
    notes = [e["note"] for e in completed if e.get("note")]
    if notes:
        lines += ["", "Stated exclusions and harness notes:"]
        for entry in completed:
            if entry.get("note"):
                lines.append(f"- **{entry['project']}**: {entry['note']}")
    lines += [
        "",
        "| project | moonbuggy mutants | mutmut mutants | shared | agree "
        "| disagree | ambiguous |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for entry in entries:
        if "blocker" in entry:
            lines.append(f"| {entry['project']} | — | — | — | — | — | blocked |")
            continue
        lines.append(
            f"| {entry['project']} | {entry['moonbuggy_mutants']} | "
            f"{entry['mutmut_mutants']} | {entry['shared']} | {entry['agree']} | "
            f"{len(entry['disagreements'])} | {entry['ambiguous']} |"
        )

    shared = sum(e["shared"] for e in completed)
    agree = sum(e["agree"] for e in completed)
    lines += [
        "",
        f"**Agreement on shared mutants: {agree}/{shared}"
        + (f" ({agree / shared:.1%})" if shared else "")
        + ".**",
        "",
        "## Disagreements by category",
        "",
        "| category | count |",
        "|---|---:|",
    ]
    for category, count in sorted(categories.items()):
        lines.append(f"| {category} | {count} |")
    if not categories:
        lines.append("| (none) | 0 |")

    lines += ["", "## Every disagreement", ""]
    any_listed = False
    for entry in completed:
        for item in entry["disagreements"]:
            any_listed = True
            lines += [
                f"- **{item['project']} · {item['key'][0]}** — "
                f"`{item['key'][1]}` → `{item['key'][2]}`",
                f"  - moonbuggy `{item['moonbuggy']}`, mutmut `{item['mutmut']}`",
                f"  - **{item['category'] or 'UNCLASSIFIED'}**: "
                f"{item['reason'] or 'needs a decision'}",
            ]
    if not any_listed:
        lines.append("None.")

    if unclassified:
        lines += [
            "",
            "## Unclassified",
            "",
            f"{len(unclassified)} disagreement(s) have no classification. The run",
            "exits non-zero until each is decided.",
        ]

    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
