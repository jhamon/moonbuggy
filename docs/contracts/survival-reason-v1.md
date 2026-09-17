# Survival-reason vocabulary — survivor-export companion (C3 Phase B)

**Status:** DRAFT v0.1 — awaiting boss co-sign on the PR (FROZEN only after
that sign-off; the schema diff it constrains lands in the same PR and must not
merge before it)
**Version:** v0.1 (constrains the `survival_reason` enum of
`src/moonbuggy/schemas/survivor-export.v1.schema.json`, widened null → tokens
in the same diff)
**Co-owners:** @moonbuggy-qa (taxonomy, derivability, correctness),
@moonbuggy-dx (schema, CLI, changelog)
**Downstream consumers (named, not optional):**
- `src/moonbuggy/schemas/survivor-export.v1.schema.json` — the `survival_reason`
  enum is exactly the token set frozen here (plus null); schema drift from this
  file fails the vocabulary tests.
- `src/moonbuggy/export.py` — the emitter derives every token from the record's
  own fields by the derivation table below; it never free-texts a reason.
- Agent workflows (human + automated) — an agent reading `survivors.jsonl`
  triages on this token and re-injects via `moonbuggy run <id>`.
- @moonbuggy-outreach — case studies quote exported findings verbatim; a
  `survival_reason` in a published receipt must trace to this file.

---

## Purpose

Phase A froze the survivor export with `survival_reason: null` on every record
and reserved the field so the shape would not move again. This file is the
Phase B vocabulary it was reserved for: the closed token set for *why a finding
survived*, widening the schema field null → (null | token) as an additive
version bump, per the versioned-contract procedure.

The QA clause that shapes every token: **a SURVIVED must be traceable to why** —
and traceable mechanically, from data the record already carries. A reason a
reader has to trust the tool's opinion for is a reason the tool could lie
about, and moonbuggy must never lie. So there are **no judgment-call tokens**:
every token in this vocabulary is derivable from fields present on the record
itself, by one table, with no free-text fallback anywhere.

## The derivability rule

Each token names a condition on the record's own fields. The derivation is a
fixed-precedence evaluation over those conditions — the first matching row
wins — so two independent readers of the same record always derive the same
token, and a producer emitting a token whose condition is false on its own
record is a bug the tests catch.

| precedence | token | derivable from | meaning |
|---|---|---|---|
| 1 | `accepted_equivalent` | `accepted == true` | A live accepted-equivalents ledger entry covers this mutant. The human's *reason* for accepting is not a token — it travels verbatim in `accept_reason` (free text is a ledger responsibility, where a human wrote it, never a vocabulary responsibility). The mutant is annotated, never hidden: it still exports. |
| 2 | `logging_noise` | `logging_call == true` | The mutation sits inside a logging call's argument expressions and is unkillable by construction — nothing asserts on log contents (see `docs/equivalent-mutants.md`, "Logging calls"). Only reachable with `--include-logging-mutants`; the default policy suppresses these and they never become findings. |
| 3 | `no_coverage` | `status == "NO_COVERAGE"` | No test was selected — `tests_run == 0` by the runner's own rule. The fix is to write a test that reaches the line (or find out why selection missed one that does). |
| 4 | `covered_unasserted` | `status == "SURVIVED"` with `tests_run > 0`, none of the above | Tests ran against the mutant and none objected. This is the ordinary survivor. |
| — | `null` | none of the above | Not yet classified. Every v1.0 record carries null; a v1.1 producer that has not classified a finding emits null rather than inventing a token. null is **not** a fifth reason: a consumer must never read it as one. |

The deliberately-absent case: **equivalent-mutant suspicion is not a token.**
Whether a survivor is equivalent or the suite is weak is exactly the judgement
`docs/equivalent-mutants.md` walks through and the accepted-equivalents ledger
records after a human makes it. There is no analysis in moonbuggy that can
derive "this is probably equivalent" mechanically, so there is no token for it
— a `weak_test`/`equivalent_suspect` token would be the tool grading its own
output, the one thing the vocabulary must not do. The path is: triage it,
`moonbuggy accept <id> --reason ...`, and the next export carries
`accepted_equivalent` with the human reason in `accept_reason`.

## The four-token axis, against the intel doc

competitive-intel §2.1 part B named the leading axis as "equivalent-mutant
suspicion vs assertion-gap vs weak assertion vs operator noise". This
vocabulary lands the mechanically-derivable subset of that axis:

- **assertion-gap** → `no_coverage` (nothing reached the line; the strongest,
  cheapest signal an agent can act on).
- **weak assertion** → `covered_unasserted` (tests ran, none checked the
  mutated behaviour — the assertion-gap axis's covered half, per qa's D6
  recommendation: "covered-but-not-asserted vs uncovered").
- **operator noise** → `logging_noise` (the one unkillable-by-construction
  family moonbuggy already recognizes mechanically).
- **equivalent-mutant suspicion** → deliberately *absent*; it is carried by the
  ledger (`accepted`/`accept_reason`), not by a token. See above.

## Machine record ≡ derivation

Every token above is computed from the exported record's own fields, so the
invariant "a consumer that can parse the record can re-derive the reason"
holds by construction — the same spirit as killreason-v1's machine==human rule,
one layer up: not "JSONL and the plaintext line agree", but "the token and the
fields it claims to derive from agree". A `survival_reason` of
`no_coverage` on a record with `tests_run: 3` is a contradiction, not a nuance.

## Versioning

v0.1 is drafted for boss co-sign on the PR that carries this file plus the
schema widening. The token set is **closed**: adding, renaming, or removing a
token is a version bump of this file *and* of
`survivor-export.v1.schema.json` (`v1.1 → v1.2` additive / `v2.0` breaking),
landing as a reviewed diff — never an in-place edit. A token whose derivation
condition is not already a record field (i.e. one that would require new
runner state) is a breaking change: consumers cannot re-derive it from records
they already hold.

## Executable invariants

| what is enforced | where |
|---|---|
| vocabulary closure: the schema enum is exactly (null + the four tokens) | `tests/test_survivor_export.py` |
| unknown tokens rejected by the validator (invented token fails the gauntlet) | same |
| null-vs-token discrimination: null validates, every token validates, a non-vocabulary string does not | same |
| every token's derivation condition is stated on the record it rides | same (derivation table, per token) |
| `accept_reason` carries no vocabulary meaning when `accepted` is false | same |
