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
# its timeout arrives.
def status_from_exit_code(code):
    """Translate a mutmut child exit code into moonbuggy's vocabulary."""
    if code == 0:
        return "SURVIVED"
    if code == 1:
        return "KILLED"
    if code < 0:
        return "TIMEOUT"
    return "SUSPICIOUS"


MUTMUT_FUNCTION = re.compile(r"^x_(?P<name>.+)__mutmut_(?P<index>orig|\d+)$")


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
            # Keys look like `sample.inventory.x_is_available__mutmut_2`.
            function_key = key.rsplit(".", 1)[-1]
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
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        match = MUTMUT_FUNCTION.match(node.name)
        if match:
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

    if "TIMEOUT" in (mine, yours):
        return (
            "genuine semantic difference",
            "mutmut derives its own timeout from a baseline run rather than "
            "taking ours, so which mutants time out need not match",
        )

    if mine == "SURVIVED" and ours["tests_run"] == 0:
        return (
            "genuine semantic difference",
            "no test covers this line at all. moonbuggy reports SURVIVED "
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


def run_moonbuggy(project, source, timeout):
    command = [
        PYTHON,
        "-m",
        "moonbuggy.cli",
        "--no-cache",
        "--quiet",
        "--source",
        str(source),
        "--timeout",
        str(timeout),
    ]
    subprocess.run(command, cwd=project, capture_output=True, text=True, timeout=3600)
    results = project / ".moonbuggy" / "results.jsonl"
    if not results.exists():
        return None
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


def project_run(name, project, package, tests, timeout):
    """Both tools over one project. Returns a comparison entry."""
    began = time.perf_counter()
    records = run_moonbuggy(project, project / package, timeout)
    if records is None:
        return {"project": name, "blocker": "moonbuggy produced no results"}

    their_mutants, error = run_mutmut(project, package, tests)
    if their_mutants is None:
        return {"project": name, "blocker": f"mutmut produced no mutants tree: {error}"}

    entry = compare(name, records, their_mutants)
    entry["seconds"] = round(time.perf_counter() - began, 1)
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

# Why the five M4 libraries are NOT in this list, stated rather than left to be
# noticed. M1.3.1 names them, and they would be the most valuable projects here.
#
# mutmut cannot be pointed at an arbitrary checkout: it rewrites the project
# into a `mutants/` tree and requires the project's pytest configuration to be
# replaced with one that reads from it. Doing that to each of the five means
# editing a pinned third-party checkout, and running it means a virtualenv per
# target carrying both mutmut and that project's own dependencies -- which is a
# second copy of M4's harness rather than a few lines here.
#
# The count is made up with generated projects instead, varied along the
# dimensions that change what the two tools have to agree about. That is a real
# substitution and it is a weaker one: generated code has no decorators, no
# classes, no closures and no third-party imports, so it cannot surface the
# disagreements those produce. Recorded as a known gap rather than presented as
# equivalent.
OSS_TARGETS_EXCLUDED = (
    "mutmut requires rewriting each project's pytest configuration and running "
    "from a generated `mutants/` tree, plus a virtualenv per target carrying "
    "both mutmut and that project's dependencies"
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
    parser.add_argument("--out", default=str(REPO / "docs" / "differential.json"))
    parser.add_argument("--markdown", default=str(REPO / "docs" / "differential.md"))
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        projects = build_projects(Path(tmp))
        entries = []
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
        "# Differential against mutmut, per mutant (milestone M1.3)",
        "",
        "Generated by `scripts/differential.py`. Do not edit by hand: a later",
        "run rewrites it, and M1.3.5 uses the rewrite to detect drift.",
        "",
        "Two tools, joined on what the mutation *is* — `(module, original line,",
        "mutated line)` — rather than on any identifier, because they share none.",
        "mutmut is never authoritative here; a disagreement is a question with",
        "four permitted answers, and every one of them has been given.",
        "",
        f"**{len(completed)} projects compared.**",
        "",
        "The five open-source libraries from M4 are **not** among them, and",
        "M1.3.1 names them, so the reason is here rather than left to be",
        f"noticed: {OSS_TARGETS_EXCLUDED}. Generated projects make up the count",
        "instead. That is a weaker substitution and worth saying so — generated",
        "code has no decorators, no classes, no closures and no third-party",
        "imports, so it cannot surface the disagreements those produce.",
        "",
        "| project | moonbuggy mutants | mutmut mutants | shared | agree | disagree "
        "| ambiguous |",
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
        "## Disagreements by category (M1.3.2)",
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
            f"{len(unclassified)} disagreement(s) have no classification. M1.3.2",
            "does not permit this; the run exits non-zero until each is decided.",
        ]

    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
