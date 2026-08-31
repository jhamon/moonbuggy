# Harness output — D2 numbers-pipe contract

**Status:** FROZEN (with the v1 schema)
**Version:** v1.0 (tracks `scripts/schemas/harness-output.v1.schema.json`)
**Freeze date:** 2026-08-31
**Co-owners:** @moonbuggy-perf (bench harness), @moonbuggy-dx (schema, changelog)
**Row-shape source of truth:** `scripts/schemas/harness-output.v1.schema.json`
**Downstream consumers (named, not optional):**
- @moonbuggy-outreach — release receipts; case studies quote machine records verbatim.
- The machine-readable changelog — every perf claim a release makes is traceable to feed records.
- The metrics dashboard (`intel/metrics-dashboard.md`) — CI writes one row per commit from these records.
- @moonbuggy-pm — reads the feed when ranking candidates against measured deltas.
- @moonbuggy-dx's time-to-first-finding benchmark — shares the feed envelope so onboarding is a first-class measured row.

---

## Single source of truth

The **row shape** of the D2 numbers pipe is defined **exactly once**, by the frozen
JSON Schema at `scripts/schemas/harness-output.v1.schema.json`. It is the one
definition of every perf number's document structure: `suite`, `wall_clock`,
`mutants`, `mutants_per_sec`, `memory_delta`, and the `hypothesis` tag, plus the
`schema` version pin. Any consumer that names a field of a perf-numbers document
must use a field that appears in that schema. A number cited in docs, dashboards,
or PRs must come from a document that validates against it.

This page intentionally does **not** restate the row shape. It is the **companion
document**: it carries the end-to-end hypothesis-tag invariant and the consumer
rules (feed / changelog / outreach / dashboard) that operate on top of that shape
without redefining it. The schema is the contract; this page is how it is used.

## The end-to-end hypothesis-tag invariant

This is the load-bearing requirement of the contract. It is stated once here and
must hold everywhere. It is part of the frozen contract, not a draft.

> **`hypothesis`, when present on a document, is the same byte string from the
> bench invocation to the feed row to the changelog entry to the outreach
> receipt. No consumer may rename, drop, prefix, suffix, or reverse-map it.**

Concretely:

- The bench harness accepts the tag as a per-run value (`BENCH_HYPOTHESIS` today,
  the CLI/env conduit to be a `--hypothesis H21` / `MB_HYPOTHESIS=H21` flag) and
  copies it *verbatim* onto the emitted row's `hypothesis` field.
- The changelog generator groups feed rows by the tag; a changelog sentence that
  names a tag must name the exact tag from the row.
- Outreach copies the tag from the feed row into the published receipt. A
  receipt that cannot show its tag is not citable.
- `"baseline"` is the **reserved untagged marker**. A reference run that belongs
  to no single perf hypothesis is emitted with `hypothesis: "baseline"` — the
  same handling as `--hypothesis baseline` / `MB_HYPOTHESIS=baseline`. A receipt
  quoting a baseline run must quote it as `"baseline"`, never invent a tag.

**Why this is a contract, not a convention:** outreach's entire model is "case
studies cite your verified numbers instead of re-deriving" and perf's is "a
receipt you can't misread." If the tag drifts between layers, a case study can
attach a measured delta to the wrong hypothesis — a correctness bug in the
*telling*, which is the one thing this feed exists to prevent.

## File contract

- One `harness-output.v1` document (one JSON object) per line of the feed; no
  keys with a newline in any value; a file is a sequence of independent records
  (a reader with one line has everything it needs — the same property moonbuggy's
  mutation JSONL relies on).
- Every record carries the `schema` version pin (const `1` for v1) and `harness`
  (the emitting script), so a reader can tell which contract and which moonbuggy
  harness wrote it.
- Records are **append-only**: a corrected number is a *new* record with a new
  `timestamp`, never an in-place mutation. The feed is a diff surface; the
  dashboard and changelog read the latest record per `(suite, hypothesis)`.
- Raw timing values are emitted with enough precision for a second consumer to
  reproduce the derived figure (`mutants_per_sec = mutants / wall_clock`),
  because a receipt that says "end to end" must be reproducible from the file.

## How each consumer reads the feed

The grouping keys below are the schema's own fields — no consumer-defined synonyms.

- **Changelog** groups records by `moonbuggy` (the measured version) + `suite` +
  `hypothesis`, and a "this release …" statement cites the record's `commit` and
  `hypothesis` verbatim. A changelog entry that changes a number without a new
  record is a contradiction.
- **Outreach** receives the feed (or a filtered projection of it) and quotes
  `mutants_per_sec`, `wall_clock`, `memory_delta`, and `hypothesis` verbatim in a
  receipt. No adjectives; the machine line is the receipt.
- **Dashboard** reads the latest record per `(suite, hypothesis)` on each CI
  commit and writes one row. CI does not invent a number the feed has not produced.

## Versioning

The row shape changes only by **version bump + reviewed diff** on
`scripts/schemas/harness-output.v1.schema.json` — never an in-place edit. Any
change that adds, renames, or re-types a key (e.g. splitting `hypothesis` into a
structured tag object, or adding a `mode` cold/warm distinction) is a breaking
change (v1 → v2) and moves to `harness-output.v2.schema.json`. This companion
documentation of the invariant and consumer rules updates in step with the frozen
schema it accompanies, and its version tracks that schema's.

## Superseded draft (records the collision this contract resolves)

An earlier DRAFT v0.1 of a "harness-feed JSONL" contract proposed a parallel
field-name set — `feed_schema`, `tool_version`, `run_id`, `wall_clock_s`,
`mutants_total`, `memory_delta_mb`, `hypothesis_tag`, `operator_tier`,
`verdict_counts`, `mode`, `ended_at`. That draft is **superseded** by this frozen
contract. It must not be read as a competing definition of the pipe. Where a
draft concept has a home in the v1 schema, the field names are:

| draft v0.1 (retired) | v1 schema field |
|---|---|
| `feed_schema` | `schema` |
| `tool_version` | `moonbuggy` |
| `run_id` | `timestamp` (+ `commit`, `purpose`, `harness` disambiguate) |
| `wall_clock_s` | `wall_clock` |
| `mutants_total` | `mutants` |
| `mutants_per_sec` | `mutants_per_sec` |
| `memory_delta_mb` | `memory_delta` |
| `hypothesis_tag` | `hypothesis` (reserved value `"baseline"`) |

Draft-only concepts with no v1 home (`operator_tier`, `verdict_counts`, `mode`
cold/warm, `baseline`/`comparison_ratio`) are **not** part of the frozen v1
contract. If a downstream consumer proves it needs one, that is a v1→v2 version
bump on the schema — not an ad-hoc addition to the feed.

## The load-bearing test

The contract is *executable*: `tests/test_harness_output_schema.py` freezes the
version pin, the required field set, a golden row, and a validator gauntlet;
`tests/test_harness_output_integration.py` runs the emit gate end to end. A future
bench-facing test asserts that (a) every emitted record validates against the
frozen schema, (b) each `hypothesis` is either a register tag or the literal
`"baseline"`, and (c) `mutants_per_sec ≈ mutants / wall_clock` within the
floating-point tolerance the harness emits. The tag-survival assertion (bench CLI
arg == record field == changelog grouping) becomes a doctest in the changelog
generator per dx's doctest-as-contract rule.