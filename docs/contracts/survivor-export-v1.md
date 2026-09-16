# Survivor export — C3 Phase A findings feed

**Status:** FROZEN v1.0 (qa co-sign 2026-09-13 on PR #68); widened to **v1.1**
by the survival-reason vocabulary (C3 Phase B —
docs/contracts/survival-reason-v1.md), pending boss co-sign on the Phase B PR
**Version:** v1.1 (tracks `src/moonbuggy/schemas/survivor-export.v1.schema.json`)
**Freeze date:** 2026-09-13 (v1.0); v1.1 pending sign-off
**Co-owners:** @moonbuggy-qa (vocabulary correctness, machine==human
invariants), @moonbuggy-dx (schema, CLI, changelog)
**Downstream consumers (named, not optional):**
- Agent workflows (human + automated) — the export is the handoff surface for
  the C3 loop: an agent consumes findings, fixes tests, re-measures via
  `moonbuggy run <id>`.
- `src/moonbuggy/cli/export.py` — the emitter; every export line is built and
  validated against this contract.
- `moonbuggy run <id>` — the re-injection primitive; the exported `id` is its
  direct input (round-trip pinned by test).
- @moonbuggy-outreach — case studies on AI-written test suites quote exported
  findings verbatim.

---

## Single source of truth

The **record shape** of the survivor export is defined **exactly once**, by the
frozen JSON Schema at `src/moonbuggy/schemas/survivor-export.v1.schema.json`.
This page is the **companion document**: it carries the design rules (what is
exported, why the envelope is reused verbatim, why `survival_reason` is null)
without redefining the shape. The schema is the contract; this page is how it
is used.

## What is exported

Exactly the **findings**: every record in `results.jsonl` whose status is
`SURVIVED` or `NO_COVERAGE` (the same two statuses `report.FINDING_STATUSES`
defines and the exit code gates on). Killed, timed-out, suspicious and skipped
mutants are never exported — an export with zero lines is a fact about the run
("no findings"), not an error.

One file, one JSON object per line, in the order the run reported the findings.
The file is independent of `results.jsonl` (which is never rewritten) and is
reproducible: re-running `moonbuggy export` over unchanged artifacts yields the
same records modulo the `exported` timestamp.

## The envelope is reused verbatim

An export record **is** a schema-4 record (the `results.jsonl` envelope,
`report.RECORD_SCHEMA` — `original`/`mutated`/`diff`/`nearest_test`/
`killreason`/`accepted`/…) under an export version pin, plus exactly the fields
the export itself contributes:

| field | source | meaning |
|---|---|---|
| `schema` | export pin (const `1`) | which contract wrote this line |
| `exported` | export provenance | UTC timestamp of the export |
| `moonbuggy` | export provenance | emitting tool version |
| `record_schema` | carried from the record (const `4`) | the embedded envelope's own version |
| `survival_reason` | reserved in v1.0 (always null); widened in **v1.1** to the survival-reason tokens (null stays valid: not yet classified) | why it survived — see below |
| *(all other fields)* | the record, verbatim | the mutant and its verdict |

Why verbatim rather than a narrower projection: a consumer that can parse
`results.jsonl` parses an export with the same code; `original`/`mutated`/`diff`
are what make the record re-injectable without re-deriving anything (the
plaintext agent line deliberately withholds them, but that line is for
grepping, not for reconstructing a mutant — the two surfaces serve different
consumers and the frozen agent-line shape is unchanged by this contract).

## `survival_reason` — the survival-reason vocabulary (v1.1)

**v1.0 emitted `survival_reason: null` on every record, and that was the
contract.** The stable survival-reason vocabulary is **C3 Phase B** and lands
as this additive widening: the field's type widens null → (null | token) and
the closed token set freezes in
[docs/contracts/survival-reason-v1.md](survival-reason-v1.md) — the vocabulary
doc is the single source of truth for what the tokens mean and how each is
derived; the schema enum and that doc must agree (the tests derive one from
the other).

The four tokens — `no_coverage`, `covered_unasserted`, `logging_noise`,
`accepted_equivalent` — are each mechanically derivable from the record's own
fields; there are no judgment-call tokens. **null remains valid in v1.1**: it
means *not yet classified* (every v1.0 line carries it; a v1.1 producer that
has not classified a finding emits null rather than inventing a token), and no
consumer may read null as any reason. Equivalent-mutant suspicion is
deliberately **not** a token: it is a human judgement, carried by the ledger
(`accepted` / `accept_reason`) and surfaced as the `accepted_equivalent` token
once a human has made it.

The `killreason` field **does** travel verbatim (and is `null` on every
finding, because a finding was not killed): the D1 killreason vocabulary is
frozen, and carrying the field keeps one parser valid for both
`results.jsonl` and exported lines.

## The re-injection round trip

The export exists to close the loop, so the loop is part of the contract:

1. Run moonbuggy (`run A`), export (`moonbuggy export`).
2. Every exported record's `id` is a valid direct input to
   `moonbuggy run <id>` — the re-measurement primitive that always measures
   fresh and never serves the cache.
3. The fresh verdict arrives on the same frozen agent-line shape results.txt
   uses, with the D1 `killreason` for any kill — so a transition
   `SURVIVED → KILLED (assertion_failed)` is machine-readable from records
   alone.

The round trip is pinned by a test (`tests/test_survivor_export.py`), which
exports from a real run and re-runs each exported id through `main`,
asserting one fresh verdict per exported id.

## Executable invariants

| what is enforced | where |
|---|---|
| schema version pin, required field set, golden record | `tests/test_survivor_export.py` |
| validator gauntlet (missing/extra/mistyped fields rejected) | same |
| findings-only rule (no KILLED/TIMEOUT/SUSPICIOUS/SKIPPED record ever exports) | same |
| round trip: export → `run <id>` → fresh verdict per id | same |
| exported line's `id`/`status` agree with the plaintext line for the same mutant | same (machine == human, criterion E3) |
| survival-reason vocabulary closure: schema enum == null + the four frozen tokens; unknown tokens rejected; every token derivable from its own record | same (vocabulary tests, constraining docs/contracts/survival-reason-v1.md) |

## Versioning

v1.0 froze with qa co-sign on PR #68 (2026-09-13). Any change that adds,
renames, or re-types a key is a version bump (`v1 → v1.1` additive /
`v2.0` breaking) landing as a reviewed diff on
`src/moonbuggy/schemas/survivor-export.v1.schema.json` — never an in-place
edit. The survival-reason widening **is** the first additive bump: v1.0 →
v1.1, carried by the same PR as docs/contracts/survival-reason-v1.md, with the
vocabulary tests pinning the widened enum.
