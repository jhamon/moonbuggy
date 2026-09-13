# Survivor export — C3 Phase A findings feed

**Status:** DRAFT v0.1 — awaiting @moonbuggy-qa co-sign on the PR (FROZEN only
after that sign-off comment)
**Version:** v0.1 (tracks `src/moonbuggy/schemas/survivor-export.v1.schema.json`)
**Freeze date:** *none yet — set on qa co-sign*
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
| `survival_reason` | reserved, **always null in v1** | why it survived — see below |
| *(all other fields)* | the record, verbatim | the mutant and its verdict |

Why verbatim rather than a narrower projection: a consumer that can parse
`results.jsonl` parses an export with the same code; `original`/`mutated`/`diff`
are what make the record re-injectable without re-deriving anything (the
plaintext agent line deliberately withholds them, but that line is for
grepping, not for reconstructing a mutant — the two surfaces serve different
consumers and the frozen agent-line shape is unchanged by this contract).

## `survival_reason` — reserved, not yet in vocabulary

**v1 emits `survival_reason: null` on every record, and that is the contract.**
The stable survival-reason vocabulary (equivalent-mutant suspicion vs
assertion-gap vs weak assertion vs operator noise — competitive-intel §2.1 part
B) is **C3 Phase B** and does not exist yet. Per the freeze discipline: no
token is invented here, the field is present-and-null so the shape does not
move again when Phase B lands, and no v1 consumer may interpret `null` as any
reason. Phase B is a version bump (widening the type to the new tokens), not a
silent edit.

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
| schema version pin, required field set, `survival_reason is null`, golden record | `tests/test_survivor_export.py` |
| validator gauntlet (missing/extra/mistyped fields rejected) | same |
| findings-only rule (no KILLED/TIMEOUT/SUSPICIOUS/SKIPPED record ever exports) | same |
| round trip: export → `run <id>` → fresh verdict per id | same |
| exported line's `id`/`status` agree with the plaintext line for the same mutant | same (machine == human, criterion E3) |

## Versioning

v1.0 freezes with qa co-sign on the PR that carries this file. Any change that
adds, renames, or re-types a key is a version bump (`v1 → v1.1` additive /
`v2.0` breaking) landing as a reviewed diff on
`src/moonbuggy/schemas/survivor-export.v1.schema.json` — never an in-place
edit. Phase B's survival-reason tokens are the anticipated first additive bump.
