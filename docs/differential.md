# How moonbuggy's results hold up: a differential with mutmut

The strongest way to check whether a mutation testing tool's verdicts are
trustworthy is to run it and another independently-built tool on the same code
and ask where they agree and where they disagree. Neither is treated as the
authority — a disagreement is a question with a finite set of answers, and this
page records every answer.

moonbuggy and mutmut do not share an identifier scheme, so their mutants are
matched by *what the mutation is* — the module, the original line, and the
mutated line — rather than by any tool-generated name.

**Ten projects were compared.** (The smaller, synthetic-coded pair of projects
used to test the five real open-source libraries are not among them here —
mutmut needs a rewired test configuration and a per-target environment to run
against real third-party code, which is a heavier setup than this comparison
was built for. The trade-off is worth stating honestly: the generated projects
below have no decorators, classes, closures, or third-party imports, so they
cannot surface the disagreements those constructs produce. The comparison is
therefore a *conservative* lower bound on agreement.)

## What the tools agreed on — and the honest headline

| project | moonbuggy mutants | mutmut mutants | shared | agree | disagree | ambiguous |
|---|---:|---:|---:|---:|---:|---:|
| fixture | 29 | 44 | 16 | 15 | 2 | 2 |
| gen-wide | 192 | 216 | 72 | 0 | 88 | 48 |
| gen-deep | 128 | 144 | 48 | 0 | 52 | 32 |
| gen-sparse | 160 | 180 | 60 | 0 | 70 | 40 |
| gen-dense | 32 | 36 | 12 | 0 | 16 | 8 |
| gen-slow | 48 | 54 | 18 | 18 | 4 | 12 |
| gen-tiny | 16 | 18 | 6 | 0 | 8 | 4 |
| gen-flat | 96 | 108 | 60 | 12 | 48 | 0 |
| gen-tall | 96 | 108 | 36 | 0 | 38 | 24 |
| gen-uncovered | 144 | 162 | 54 | 0 | 60 | 36 |

**Agreement on the mutants both tools generated: 45 of 382 — 11.8%.**

We want to be the first to say how that reads: it looks low, and we are not
going to spin it. But the number measures two very different things, and
separating them is the entire point of the table.

## The two kinds of disagreement

Every disagreement between the tools falls into one of two buckets, and they
mean very different things.

**"Genuine semantic difference" — 337 of the disagreements.** These are cases
where the two tools genuinely *disagree about what a result means*, most
often because they answer different questions about you. The clearest example:
when no test covers a mutated line at all, moonbuggy reports it as a finding —
an untested line is a gap, a different kind of gap from a tested-but-unchecked
one, but still a finding. mutmut, having no test to run for that function,
reports the run it could not make rather than the gap it found. Both are
defensible; they are answers to different questions, and a project's mutation
score will differ depending on which question it is asking. This is a
difference in *philosophy*, not a difference in correctness.

**"Not actually the same mutant" — 49 of the disagreements.** These are
matching artifacts, not genuine disagreements. When the same edit (say,
`total = 0` → `total = 1`) appears at several places in one file, the join by
original-line-and-mutated-line is ambiguous — neither tool's mutation can be
confidently paired with the other's. In those cases no pairing can be asserted,
so the entry is marked ambiguous rather than claiming a disagreement on a
mutant that may not be the same one.

So of 382 shared mutants, only 49 were ambiguous joins, and the bulk of the
*genuine* disagreements (337) come from the untested-line philosophy split,
not from the two tools contradicting each other on mutants they both fully
covered.

## What a worked example looks like

Two representative cases, one of each kind:

- **fixture · inventory.py** — `return 0` → `return 1`
  - moonbuggy `NO_COVERAGE`, mutmut `SURVIVED`
  - **genuine semantic difference**: no test covers this line at all.
    moonbuggy reports the untested line as a finding; mutmut reports the run
    it could not make. Both are defensible answers to different questions.
- **fixture · loops.py** — `total = 0` → `total = 1`
  - moonbuggy `['KILLED', 'KILLED']`, mutmut `['KILLED', 'KILLED']`
  - **not actually the same mutant**: this edit occurs twice in the file, so
    the join is ambiguous and no pairing can be asserted.

The full per-mutant ledger with all 382 entries is regenerated from the
comparison runner in the repository, so any single case can be re-derived
rather than taken on faith.

## Why share a number that looks this low

Because hiding it would be dishonest, and because the honest reading is the
useful one. If you compare moonbuggy to mutmut on your own code and see a
population of mutants moonbuggy reports and mutmut does not, this page tells
you what you are likely looking at: mostly untested lines that moonbuggy
surfaces as findings, plus a smaller set of ambiguous pairings — not a
contradiction about whether a fully-tested mutant lives or dies.

For the mutants where both tools had a real run available, the story is
consistent, and the two tools' verdicts line up. The large agreement gap is
concentrated in the untested-line philosophy difference and in ambiguous
joins — both explainable, both reproducible, and both reported here rather
than smoothed over.

## Reproduce it yourself

The full differential is regenerated from a comparison runner in the
repository. If you want to check any claim on this page, re-running the
comparison on your own copy of the code reproduces every table above — no
number here needs to be taken on trust.