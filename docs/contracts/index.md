# Moonbuggy — versioned contracts

Internal cross-bot interface contracts for moonbuggy. These files are part of
the versioned-contract procedure (the team register lives at
`~/work/moonbuggy-team/contracts/CONTRACT-PROCEDURE.md`). A contract here is
not prose — it is a frozen, versioned spec that changes only by reviewed diff,
and its consumers are named on the file so drift has an owner.

These pages are a development/build record, not user documentation, and are
excluded from the published site for the same reason as
[`docs/development`](../development/index.md). They stay in the repository
because release and CI processes depend on them and cite them by path.

| contract | interface | co-owners | status | freeze |
|---|---|---|---|---|---|
| [killreason-v1](killreason-v1.md) | the killreason vocabulary: every token in the JSONL `killreason` field and the human trace `killreason=` token | @moonbuggy-qa, @moonbuggy-dx | FROZEN v1.0 | 2026-08-31 |
| [harness-output-v1](../../scripts/schemas/harness-output.v1.schema.json) | the D2 numbers pipe: suite, wall-clock, mutants/sec, memory delta, hypothesis tag | @moonbuggy-perf, @moonbuggy-dx | FROZEN v1.0 | 2026-08-31 |
| [harness-output-jsonl](harness-output-jsonl.md) | companion to harness-output-v1: the end-to-end hypothesis-tag invariant + consumer rules (feed/changelog/outreach/dashboard); does not redefine the row | @moonbuggy-perf, @moonbuggy-dx | FROZEN v1.0 | 2026-08-31 |

## The register rule, restated

A frozen contract changes only by **version bump + reviewed diff**, never an
in-place edit. A register row without a date is stalled by definition. This
index lists the current status only; the authoritative list of who owes what
is the team TRACKING register.