# Killreason vocabulary — frozen contract (D1)

**Status:** FROZEN
**Version:** v1.0
**Freeze date:** 2026-08-31
**Co-owners:** @moonbuggy-qa (taxonomy, correctness), @moonbuggy-dx (schema, changelog)
**Downstream consumers (named, not optional):**
- `src/moonbuggy/report.py` — the JSONL `Record.killreason` field and the
  human trace `killreason=` token both read this vocabulary.
- `src/moonbuggy/runner.py` — `_killreason_for()` is the single mapping from
  verdict status to killreason; every runner path (fork, warm, subprocess,
  cache) calls it.
- @moonbuggy-outreach — case studies quote machine records verbatim; a
  killreason token in a published receipt must be traceable to this contract.
- Agent workflows (human + automated) — a parser comparing two JSONL records
  compares `killreason` tokens directly against this vocabulary.

---

## Purpose

This is the **single source of truth** for every token that can appear in the
`killreason` field of a moonbuggy JSONL record and the `killreason=` token of
the derived human trace. Both surfaces consume the same enumeration
(`src/moonbuggy/killreason.py::KillReasonCode`), and every member of that
enumeration is listed here.

A killreason is a *cause*: why was this mutant killed (or why could it not
be given a confident verdict)? It is **not** a status — `KILLED` is a verdict;
`assertion_failed` is why. The status tells you what happened; the killreason
tells you what it *means*.

The vocabulary is closed: adding a member is a breaking change (v1 → v2).

---

## Frozen vocabulary (v1.0)

| code | human label | status | meaning |
|---|---|---|---|
| `assertion_failed` | assertion failed | `KILLED` | A selected test's assertion failed under the mutation — the test checked the mutated behaviour and objected. This is the ordinary kill mutation testing measures. |
| `test_errored` | test errored | `KILLED_BY_ERROR` | A selected test errored out under the mutation — the test executed the line but did not check its result. The mutant broke the code badly enough that touching it exploded. |
| `execution_crash` | execution crash | `SUSPICIOUS` | Pytest could not complete: collection error (2), internal error (3), usage error (4), or nothing collected (5). Not a statement about the mutation — something about the environment or the test selection is wrong. |
| `flaky_probe` | flaky probe | `SUSPICIOUS` | Test outcomes disagreed across unmutated runs — a selected test behaved inconsistently, so no confident verdict is possible. The mutant was never run. |
| *(null)* | — | `SURVIVED`, `NO_COVERAGE`, `TIMEOUT`, `SKIPPED` | No reason applies: survivors and uncovered lines were not killed, timeouts and skipped mutants produced no kill to reason about. |

---

## The two SUSPICIOUS killreasons are distinct — never collapse them

`execution_crash` and `flaky_probe` both produce a `SUSPICIOUS` verdict, but
they have **different causes** and different triage:

| | execution_crash | flaky_probe |
|---|---|---|
| **Cause** | pytest could not complete (collection/internal/usage error, nothing collected) | test outcomes disagreed across unmutated runs |
| **Mutant ran?** | may or may not have; pytest never reached a verdict | no — settled pre-run |
| **Triage** | fix the test selection or environment | fix the flaky test; re-run with fewer probes |
| **Zero-flaky-probe** | can still occur (crash path is independent of probe count) | structurally impossible (`--flaky-probe 0` ⇒ one unmutated run ⇒ no disagreement) |

These two causes are distinguished by *mechanism*, not merged into one
"something odd happened." The enum encodes this: they are different members,
different codes, different human labels. A consumer that groups `SUSPICIOUS`
must still be able to tell them apart.

---

## Machine record ≡ human trace

The JSONL record and the human line derive from one `Result`, so a grepper
and a `jq` reader describe the same mutant identically (criterion E3).

| surface | field | value for a crash-kill |
|---|---|---|
| JSONL (`results.jsonl`) | `"killreason"` | `"execution_crash"` |
| Human trace (plaintext line) | `killreason=` | `execution_crash` |
| JSONL for a survivor | `"killreason"` | `null` |
| Human trace for a survivor | `killreason=` | `-` (the `ABSENT` placeholder) |

The human trace uses the enum's `.code` (the machine token), not the
`.label` (the human-readable form). The label exists for documentation
and tooling; the token is what both surfaces carry.

---

## Non-kill statuses map to no reason

These statuses carry `killreason: null` (JSONL) / `killreason=-` (human trace):

| status | why no reason |
|---|---|
| `SURVIVED` | the mutant was not killed — nothing to reason about |
| `NO_COVERAGE` | no test even reached the line — nothing to reason about |
| `TIMEOUT` | the mutant hung the process — no test outcomes to classify |
| `SKIPPED` | the mutant was suppressed (logging, etc.) — never ran |

A `killreason` of `None` is not "not recorded" — it is "not applicable."
A schema-3 record read from an older file fills in `None` honestly: no older
version could have written it.

---

## The enum in code

```python
# src/moonbuggy/killreason.py — the authoritative definition

from enum import StrEnum

class KillReasonCode(StrEnum):
    """Stable per-kill reason — one token per verdict cause."""
    ASSERTION_FAILED = "assertion_failed"
    TEST_ERRORED     = "test_errored"
    EXECUTION_CRASH  = "execution_crash"
    FLAKY_PROBE      = "flaky_probe"

    @property
    def label(self) -> str: ...
```

Module-level constants (`ASSERTION_FAILED`, `TEST_ERRORED`, etc.) are aliases
for the enum members. Every import that worked before this freeze continues to
work unchanged — `StrEnum` members ARE strings.

`_KILLREASONS = frozenset(KillReasonCode)` is the canonical set of all valid
tokens; it is derived from the enum, not maintained beside it.

---

## The single mapping: `_killreason_for`

```python
# src/moonbuggy/runner.py

def _killreason_for(status: ResultStatus, flaky: bool = False) -> KillReasonCode | None:
    if status == "KILLED":
        return ASSERTION_FAILED          # → KillReasonCode.ASSERTION_FAILED
    if status == "KILLED_BY_ERROR":
        return TEST_ERRORED              # → KillReasonCode.TEST_ERRORED
    if status == "SUSPICIOUS":
        return FLAKY_PROBE if flaky else EXECUTION_CRASH
    return None
```

Every runner path (fork, warm, subprocess, cache hit) calls this function.
There is no other mapping. If a new killreason is added, this function is the
single call site that must change.

---

## Executable invariants

The following tests enforce this contract mechanically. A change that breaks
one fails CI — it does not get argued about in a room.

| test | file | what it enforces |
|---|---|---|
| `test_killreason_enum_has_exactly_four_members` | `tests/test_killreason_vocabulary.py` | vocabulary closure: exactly four tokens |
| `test_every_member_has_a_code` | same | every member has a machine-readable `.code` |
| `test_every_member_has_a_human_label` | same | every member has a human-readable `.label` |
| `test_code_and_label_are_never_identical` | same | code ≠ label (machine vs human) |
| `test_module_constants_match_enum_values` | same | backwards compat: constants ARE enum members |
| `test_killreasons_frozenset_is_exactly_the_enum_codes` | same | `_KILLREASONS` derives from enum |
| `test_suspicious_has_exactly_two_distinct_killreasons` | same | the two SUSPICIOUS causes are distinct enum members |
| `test_human_labels_are_frozen` | same | labels are part of the contract |
| `test_status_vocabulary_is_exactly_the_closed_public_set` | `tests/test_verdict_invariants.py` | status vocabulary closure |
| `test_unapplied_is_internal_decision_state_not_a_verdict` | same | UNAPPLIED is not a verdict |
| `test_flakiness_requires_a_second_run_to_exist` | same | probe=0 ⇒ no flaky path |
| `test_machine_record_status_and_human_line_keyword_are_the_same_token` | same | JSONL and human trace agree |

---

## Versioning

v1.0 is **frozen** as of 2026-08-31 by co-owner sign-off. Any change —
adding a member, removing a member, renaming a code or label, changing the
status a code maps to — is a **version bump with a reviewed diff** (v1 → v2).
A v2 contract must update:

1. This file (version header, vocabulary table, date)
2. `KillReasonCode` in `src/moonbuggy/killreason.py` (the enum)
3. `RECORD_SCHEMA` in `src/moonbuggy/report.py` (if a new member changes
   what a record can carry — a schema bump is the JSONL consumer's signal)
4. `_killreason_for` in `src/moonbuggy/runner.py` (if the mapping changes)
5. `tests/test_killreason_vocabulary.py` (the frozen tests)
6. `tests/test_agent_format_frozen.py` (if the human trace token changes)

A rename or removal of an existing code is a **breaking change for every
JSONL consumer** and requires a major version bump of this contract and
of `RECORD_SCHEMA`. A simple addition without renames is an additive bump
(v1.1) that does not require invalidating existing records.