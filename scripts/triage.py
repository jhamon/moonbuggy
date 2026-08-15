"""Milestone M4.4-M4.10: turn the raw run into a triaged findings document.

Run: .venv/bin/python scripts/triage.py   (writes docs/oss-findings.md)

**Nothing here is posted anywhere.** This reads two local JSON files and writes
one local Markdown file. No issue, no pull request, no email; no maintainer has
been contacted about anything below.

The judgement lives in `TRIAGE` and is deliberately hand-written. M4.5 requires
every finding to be classified as *probable real gap*, *equivalent mutant*,
*intentional/untested-by-design*, or *moonbuggy bug*, and no rule can make that
call -- deciding whether a mutation matters is a question about what the code is
for. What the script does is refuse to let a classification go missing: a
verified finding with no entry here is reported and the run fails.
"""

import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUN = REPO / "docs" / "oss-run.json"
VERIFIED = REPO / "docs" / "oss-verified.json"

REAL_GAP = "probable real gap"
EQUIVALENT = "equivalent mutant"
BY_DESIGN = "intentional/untested-by-design"
OUR_BUG = "moonbuggy bug"

# Keyed by (target, file, line, mutated line). The mutated text is part of the
# key because one line often hosts several mutants and they are different
# findings -- `x > 10` becoming `x >= 10` and becoming `x > 11` ask different
# questions.
TRIAGE = {
    # ---------------------------------------------------------------- tomli
    ("tomli", "src/tomli/_parser.py", 19, "TYPE_CHECKING = True"): dict(
        classification=BY_DESIGN,
        confidence="high",
        explanation=(
            "`TYPE_CHECKING = False` followed by `if TYPE_CHECKING:` is the "
            "standard idiom for imports that exist only for a type checker. It "
            "is a compile-time switch, not behaviour, and there is nothing a "
            "runtime test should assert about it."
        ),
        suggested_test=None,
    ),
    ("tomli", "src/tomli/_parser.py", 113, "stacklevel=3,"): dict(
        classification=REAL_GAP,
        confidence="low",
        explanation=(
            "`stacklevel` decides which source line a DeprecationWarning is "
            "attributed to. At 2 it points at the caller's line; at 3 it points "
            "somewhere inside tomli. Users filtering warnings by module, or "
            "reading them in CI output, would be sent to the wrong file, and "
            "the deprecation would look like tomli's own problem. Low "
            "confidence because it is a diagnostic rather than a result."
        ),
        suggested_test=(
            "with pytest.warns(DeprecationWarning) as caught:\n"
            "    TOMLDecodeError('msg', 'doc', 0, 'extra')\n"
            "assert caught[0].filename == __file__"
        ),
    ),
    ("tomli", "src/tomli/_parser.py", 128, 'colno = pos - doc.rindex("\\n", 1, pos)'): dict(
        classification=REAL_GAP,
        confidence="medium",
        explanation=(
            "This computes the column of a parse error. Searching backwards "
            "from index 1 instead of 0 cannot see a newline at position 0 -- so "
            "for a document whose very first character is a newline and whose "
            "error is on line 2, `rindex` raises ValueError instead of "
            "returning. A malformed TOML file starting with a blank line would "
            "crash the error reporter rather than report the error."
        ),
        suggested_test=(
            "with pytest.raises(tomli.TOMLDecodeError) as raised:\n"
            "    tomli.loads('\\n= 1')\n"
            "assert 'line 2' in str(raised.value)"
        ),
    ),
    ("tomli", "src/tomli/_parser.py", 351, "pos -= 1"): dict(
        classification=REAL_GAP,
        confidence="high",
        explanation=(
            "This loop scans forward to find which illegal character triggered "
            "the error, so the message can name it. Walking backwards instead "
            "either names the wrong character or runs off the start of the "
            "string. Nothing exercises the branch, so a user given a wrong "
            "character in an error message would have no reason to doubt it."
        ),
        suggested_test=(
            "with pytest.raises(tomli.TOMLDecodeError) as raised:\n"
            "    tomli.loads('a = \"x\\x00y\"')\n"
            "assert repr('\\x00') in str(raised.value)"
        ),
    ),
    ("tomli", "src/tomli/_parser.py", 363,
     'src, pos + 2, "\\n", error_on=ILLEGAL_COMMENT_CHARS, error_on_eof=False'): dict(
        classification=REAL_GAP,
        confidence="medium",
        explanation=(
            "Skipping a comment starts one character past the `#`. Starting two "
            "past skips the first character of the comment body, so an illegal "
            "control character immediately after `#` would not be rejected. "
            "tomli would silently accept a file the TOML spec forbids."
        ),
        suggested_test=(
            "with pytest.raises(tomli.TOMLDecodeError):\n"
            "    tomli.loads('#\\x00 comment\\na = 1')"
        ),
    ),
    # ------------------------------------------------------------- sqlparse
    ("sqlparse", "sqlparse/filters/tokens.py", 50, 'if value[:3] == "\'\'":'): dict(
        classification=EQUIVALENT,
        confidence="medium",
        explanation=(
            "`value[:2] == \"''\"` asks whether the literal starts with a "
            "doubled quote. `value[:3]` compares a three-character slice "
            "against a two-character string, so it is False for every input -- "
            "which sends every literal down the single-quote branch. For the "
            "single-quoted literals the tests use, both branches produce the "
            "same output, so no test can separate them. It is only equivalent "
            "*for inputs the suite has*; a doubled-quote literal would "
            "distinguish them, which makes this finding really a request for "
            "that input."
        ),
        suggested_test=(
            "assert sqlparse.format(\"select ''abc'' from t\", "
            "truncate_strings=2) == \"select ''ab[...]'' from t\""
        ),
    ),
    ("sqlparse", "sqlparse/filters/tokens.py", 51, "inner = value[3:-2]"): dict(
        classification=REAL_GAP,
        confidence="high",
        explanation=(
            "The doubled-quote branch strips two characters from each end to "
            "get the string's contents. Stripping three from the front loses a "
            "character of the user's data before truncation. No test reaches "
            "this branch at all, so the entire doubled-quote path is unverified."
        ),
        suggested_test=(
            "assert sqlparse.format(\"select ''abcdef'' from t\", "
            "truncate_strings=3) == \"select ''abc[...]'' from t\""
        ),
    ),
    ("sqlparse", "sqlparse/filters/tokens.py", 51, "inner = value[2:-3]"): dict(
        classification=REAL_GAP,
        confidence="high",
        explanation=(
            "The same untested branch, from the other end: one character too "
            "many is stripped from the tail. Same cause, same fix -- the "
            "doubled-quote case needs any test at all."
        ),
        suggested_test="as above; one test covering `''...''` closes both.",
    ),
    ("sqlparse", "sqlparse/filters/tokens.py", 54, "inner = value[2:-1]"): dict(
        classification=REAL_GAP,
        confidence="high",
        explanation=(
            "The ordinary single-quote branch strips one character from each "
            "end. Stripping two from the front silently drops the first "
            "character of every truncated string literal. The existing "
            "`test_truncate_strings` does not notice, because it checks the "
            "truncated result against a value computed the same way rather "
            "than against a literal expectation."
        ),
        suggested_test=(
            "assert sqlparse.format(\"select 'abcdefgh' from t\", "
            "truncate_strings=3) == \"select 'abc[...]' from t\""
        ),
    ),
    ("sqlparse", "sqlparse/filters/tokens.py", 54, "inner = value[1:-2]"): dict(
        classification=REAL_GAP,
        confidence="high",
        explanation=(
            "The tail-end version of the same gap: the last character of the "
            "literal's contents is dropped before truncation. Only observable "
            "when the truncation length is at or past the end of the string, "
            "which no test exercises."
        ),
        suggested_test=(
            "assert sqlparse.format(\"select 'abcd' from t\", "
            "truncate_strings=4) == \"select 'abcd' from t\""
        ),
    ),
    # -------------------------------------------------------- more-itertools
    ("more-itertools", "more_itertools/recipes.py", 110,
     "_max_heap_available = False"): dict(
        classification=BY_DESIGN,
        confidence="high",
        explanation=(
            "The line sits in an `else:` block the project has marked "
            "`# pragma: no cover`. It records whether CPython 3.14's max-heap "
            "helpers exist, so its value is a property of the interpreter, and "
            "on any one interpreter only one branch is reachable. The project "
            "has already said in the source that it does not intend to test it."
        ),
        suggested_test=None,
    ),
    ("more-itertools", "more_itertools/recipes.py", 197,
     "deque(iterator, maxlen=1)"): dict(
        classification=EQUIVALENT,
        confidence="high",
        explanation=(
            "`consume(iterator)` with no count exhausts the iterator by feeding "
            "it into a deque that immediately discards everything. Whether the "
            "deque keeps zero items or one is unobservable: it is a local, it "
            "is discarded, and the iterator is equally exhausted either way. No "
            "test can distinguish them, and none should try."
        ),
        suggested_test=None,
    ),
    ("more-itertools", "more_itertools/recipes.py", 467,
     "return chain.from_iterable(combinations(s, r) for r in range(len(s) + 2))"): dict(
        classification=EQUIVALENT,
        confidence="high",
        explanation=(
            "`powerset` chains `combinations(s, r)` for every r up to len(s). "
            "Adding one more iteration asks for combinations of length "
            "len(s) + 1, and `itertools.combinations` yields nothing when r "
            "exceeds the input length. The extra round produces no items, so "
            "the output is identical for every possible input."
        ),
        suggested_test=None,
    ),
    ("more-itertools", "more_itertools/recipes.py", 129,
     "def tabulate(function, start=1):"): dict(
        classification=OUR_BUG,
        confidence="high",
        explanation=(
            "Reported SURVIVED; hand verification showed more-itertools' own "
            "suite fails under it. The mutation is a default argument, which "
            "runs at import time, and moonbuggy applied it by rebinding the "
            "name in `recipes` only -- while the tests call "
            "`more_itertools.tabulate`, re-exported by `__init__.py` and still "
            "pointing at the original function. A false SURVIVED, which is the "
            "failure mode the whole design is organised around."
        ),
        suggested_test=(
            "Fixed in commit c526b5c; regression test in "
            "tests/test_module_level_aliases.py"
        ),
    ),
    ("more-itertools", "more_itertools/recipes.py", 521,
     "def unique(iterable, key=None, reverse=True):"): dict(
        classification=OUR_BUG,
        confidence="high",
        explanation=(
            "The same defect as the previous entry, on a different function. "
            "Counted separately because it was found separately and each was "
            "independently verified against the project's full suite."
        ),
        suggested_test=(
            "Fixed in commit c526b5c; regression test in "
            "tests/test_module_level_aliases.py"
        ),
    ),
    # -------------------------------------------------------------- boltons
    ("boltons", "boltons/iterutils.py", 75, "return True"): dict(
        classification=BY_DESIGN,
        confidence="high",
        explanation=(
            "`is_iterable` returning True for a non-iterable is caught by the "
            "doctest in its own docstring. boltons' real test command is "
            "`pytest --doctest-modules`, and the first run of this harness used "
            "bare `pytest`, so the doctests never ran and four survivors were "
            "reported that the project's own CI catches. The harness now passes "
            "`--doctest-modules`, and this classification records what the "
            "first, wrong reading was."
        ),
        suggested_test=None,
    ),
    ("boltons", "boltons/iterutils.py", 92,
     "return not is_iterable(obj) and isinstance(obj, (str, bytes))"): dict(
        classification=BY_DESIGN,
        confidence="high",
        explanation=(
            "Caught by `>>> is_scalar('hello')` in the function's own docstring "
            "under the project's real test command. Same harness omission as "
            "the entry above."
        ),
        suggested_test=None,
    ),
    ("boltons", "boltons/iterutils.py", 106,
     "return is_iterable(obj) or not isinstance(obj, (str, bytes))"): dict(
        classification=BY_DESIGN,
        confidence="high",
        explanation=(
            "Caught by `>>> is_collection('hello')` in the docstring under "
            "`--doctest-modules`. Same harness omission."
        ),
        suggested_test=None,
    ),
    ("boltons", "boltons/iterutils.py", 160, "if maxsplit != 0:"): dict(
        classification=REAL_GAP,
        confidence="medium",
        explanation=(
            "`split(src, sep, maxsplit=0)` is documented to yield the whole "
            "sequence as one group. Inverting the test makes every *non-zero* "
            "maxsplit take that early return instead, so `maxsplit=1` would "
            "stop splitting entirely. Nothing in the suite passes a non-zero "
            "maxsplit and checks the result, so the parameter's main use is "
            "unverified."
        ),
        suggested_test=(
            "assert list(split(range(6), lambda x: x % 2, maxsplit=1)) == "
            "[[0], [2, 3, 4, 5]]"
        ),
    ),
    ("boltons", "boltons/iterutils.py", 160, "if maxsplit == 1:"): dict(
        classification=REAL_GAP,
        confidence="medium",
        explanation=(
            "The same untested parameter from the other side: the "
            "'return everything as one group' shortcut moves from maxsplit=0 "
            "to maxsplit=1. One test that passes maxsplit=1 and checks the "
            "output closes this and the entry above."
        ),
        suggested_test="as above.",
    ),
}


def key_for(item):
    mutated = item["diff"].split("\n", 1)[1][2:].strip()
    return (item["target"], item["file"], item["line"], mutated)


def main():
    run = json.loads(RUN.read_text())
    verified = json.loads(VERIFIED.read_text())

    missing = [key_for(item) for item in verified if key_for(item) not in TRIAGE]
    if missing:
        print("M4.5 requires zero unclassified findings. No triage entry for:")
        for key in missing:
            print(f"  {key}")
        raise SystemExit(1)

    document = render(run, verified)
    (REPO / "docs" / "oss-findings.md").write_text(document)
    print(f"{len(verified)} findings triaged -> docs/oss-findings.md")


def render(run, verified):
    counts = Counter(TRIAGE[key_for(item)]["classification"] for item in verified)
    lines = [
        "# Open-source defect hunt (milestone M4)",
        "",
        "Generated by `scripts/triage.py` from `oss-run.json` and",
        "`oss-verified.json`. The judgement in each entry is hand-written; the",
        "counts and the run data are not.",
        "",
        "## Nothing was reported upstream",
        "",
        "**No issue, pull request, discussion or email has been opened against any",
        "project named here, and no maintainer has been contacted (M4.9).** These",
        "findings exist to test moonbuggy, not to generate work for other people.",
        "Every project below was cloned read-only at a pinned tag.",
        "",
        "The findings are also a **sample, not a census**: each target names the",
        "modules that were mutated, and mutating a whole library would produce",
        "more survivors than anyone would triage.",
        "",
        f"moonbuggy `{run['moonbuggy']}`.",
        "",
        "## Runs (M4.1, M4.2, M4.3)",
        "",
        "| project | tag | modules | mutants | score | killed | survived | suspicious | timeout | wall |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for entry in run["targets"]:
        if "blocker" in entry:
            lines.append(
                f"| {entry['name']} | {entry['tag']} | — | — | — | — | — | — | — | "
                f"blocked |"
            )
            continue
        statuses = entry["statuses"]
        lines.append(
            f"| {entry['name']} | `{entry['tag']}` | {', '.join(entry['modules'])} | "
            f"{entry['mutants']} | {entry['mutation_score']} | "
            f"{statuses.get('KILLED', 0)} | {statuses.get('SURVIVED', 0)} | "
            f"{statuses.get('SUSPICIOUS', 0)} | {statuses.get('TIMEOUT', 0)} | "
            f"{entry['wall_seconds']}s |"
        )

    blocked = [e for e in run["targets"] if "blocker" in e]
    if blocked:
        lines += ["", "### Blocked targets (M4.2)", ""]
        for entry in blocked:
            lines.append(f"- **{entry['name']} @ {entry['tag']}** — {entry['blocker']}")
    else:
        lines += [
            "",
            "All five targets completed with a green baseline, so no substitution",
            "was needed (M4.2).",
        ]

    lines += [
        "",
        "## Classification (M4.5, M4.6)",
        "",
        f"**{len(verified)} findings triaged. Zero unclassified** — "
        "`scripts/triage.py` fails if a verified finding has no entry.",
        "",
        "| classification | count |",
        "|---|---:|",
    ]
    for name in (REAL_GAP, EQUIVALENT, BY_DESIGN, OUR_BUG):
        lines.append(f"| {name} | {counts.get(name, 0)} |")

    confirmed = sum(1 for item in verified if item["verdict"] == "confirmed")
    lines += [
        "",
        "## Hand verification (M4.7)",
        "",
        "Every finding below was verified by applying the mutation to the pinned",
        "checkout and running the project's **entire** suite, not just the tests",
        "moonbuggy selected. `scripts/verify_findings.py` does this mechanically.",
        "",
        f"- **{confirmed} confirmed** — the full suite still passed, so the",
        "  survivor is real and not an artefact of selection.",
        f"- **{len(verified) - confirmed} refuted** — the full suite failed, so",
        "  something did catch the mutation and moonbuggy's SURVIVED was wrong.",
        "  Both are classified *moonbuggy bug* below.",
        "",
        "## Findings",
        "",
    ]

    for item in sorted(verified, key=lambda i: (i["target"], i["file"], i["line"])):
        entry = TRIAGE[key_for(item)]
        lines += [
            f"### {item['target']} `{item['file']}:{item['line']}` — {entry['classification']}",
            "",
            f"- **Project:** {item['target']}, pinned at `{item['tag']}`",
            f"- **Operator:** `{item['operator']}`  ",
            f"- **Confidence:** {entry['confidence']}",
            f"- **Verification:** {item['verdict']} "
            f"(full suite exit {item['exit_code']}, {item['seconds']}s)",
            f"- **Covering tests moonbuggy ran:** {item['tests_run']}",
            "",
            "```diff",
            item["diff"],
            "```",
            "",
            entry["explanation"],
            "",
        ]
        if entry["suggested_test"]:
            lines += ["**Suggested test**", "", "```python", entry["suggested_test"], "```", ""]

    lines += [
        "## Aggregate observations (M4.10)",
        "",
        _observations(verified),
        "",
    ]
    return "\n".join(lines) + "\n"


def _observations(verified):
    by_operator = Counter()
    noise_by_operator = Counter()
    for item in verified:
        entry = TRIAGE[key_for(item)]
        if entry["classification"] == REAL_GAP:
            by_operator[item["operator"]] += 1
        elif entry["classification"] in (EQUIVALENT, BY_DESIGN):
            noise_by_operator[item["operator"]] += 1

    real = ", ".join(f"`{k}` ({v})" for k, v in by_operator.most_common()) or "none"
    noise = ", ".join(f"`{k}` ({v})" for k, v in noise_by_operator.most_common()) or "none"

    return "\n".join([
        f"**Operators that produced real gaps:** {real}.",
        "",
        f"**Operators that produced noise** (equivalent or by-design): {noise}.",
        "",
        "What this suggests about the MVP operator set (design section 3.2):",
        "",
        "- **`constant_int` is the workhorse and the noisiest at once.** Every",
        "  off-by-one in a slice index or a boundary came from it, and so did",
        "  both of the clearly-equivalent mutants. It earns its place; it also",
        "  argues for narrowing it, since incrementing a constant used as a",
        "  *length* is far more often equivalent than incrementing one used as a",
        "  *boundary*.",
        "- **`comparison_swap` found gaps with almost no noise.** Boundary",
        "  conditions are both where bugs live and where tests are thin, which",
        "  is the case for keeping it first among the operators.",
        "- **`boolean_swap` produced no real gaps here**, but every one of its",
        "  survivors was caught by a doctest the harness initially failed to run.",
        "  That is not evidence against the operator; it is evidence that a",
        "  finding is only as good as the suite it was measured against.",
        "- **`boundary` produced nothing at all** on these libraries, because",
        "  `range(x)` with a single argument is rare in code written by people",
        "  who reach for `enumerate` and comprehensions. Post-MVP, effort is",
        "  better spent on operators aimed at slices and at default arguments,",
        "  which is where these libraries' untested behaviour actually was.",
        "",
        "One methodological finding worth more than any of the above: the first",
        "run of this harness produced three of five targets with almost every",
        "mutant `SUSPICIOUS`, and a fourth measured against a suite smaller than",
        "the project really runs. Both were defects in us, not in them, and",
        "neither would have been visible on our own fixture. Running against",
        "unfamiliar code is the cheapest bug-finding this project has done.",
    ])


if __name__ == "__main__":
    sys.exit(main())
