# Re-injection loop cost model (C3 Phase C groundwork)

Part C of the C3 contract: given an exported `survivors.jsonl` and a new or
edited test file, re-run the survivor ids and report verdict transitions in
stable tokens. This note prices that loop from a measured prototype, so the
`dx` work that ships the CLI surface starts from numbers rather than intuition.

**Deliverable status: measurements complete, no new CLI surface shipped.**
The batch run-over-ids primitive already exists (`moonbuggy run -` reading ids
from stdin, landed with t_ba5a177c). Everything below was measured with it.

## The loop, and where its cost lives

A re-injection run has three legs:

1. **Coverage/baseline pass** — one instrumented suite run (plus
   `--flaky-probe` uninstrumented repeats) to learn which tests cover each
   survivor. Paid once per invocation, amortized over all ids in it.
2. **Per-mutant verification** — each survivor runs in its own pytest
   subprocess against only its covering tests. This is the fork floor.
3. **Reporting** — verdict transitions in stable tokens. Free.

Because the baseline pass is per-*invocation* rather than per-mutant, the
batching decision dominates. `moonbuggy run` already amortizes it across
every id on stdin.

## Measured: 51 SURVIVED mutants, more-itertools v11.1.0 @ 64be96c

Setup: the M4 receipt's 51 `recipes.py` survivors, re-measured after adding a
plausible new test file (`test_moonbuggy_reinjection_extra.py`, 7 tests
targeting the survivors' neighborhoods: factor/sieve primes,
nth_combination edge indices, sliding_window dispatch, ichunked, partitions).
Full suite green before and after: 729 passed, 19896 subtests.

| Strategy | Command shape | Wall clock | Per-mutant |
| --- | --- | --- | --- |
| One-at-a-time | 51 × `moonbuggy run <id>` | sampled 10 ids: 1221s → **~104 min extrapolated for 51** | ~122s/id |
| **Batched (primitive)** | 1 × `moonbuggy run -` < 51 ids | **257s (~4.3 min)** | ~5.0s/id |
| Fresh full run | 1 × `moonbuggy run` over recipes.py, 381 mutants | **1875s (~31 min)** | — |

Sampling note: the one-at-a-time leg is a 10-id sample extrapolated linearly;
the batched and full-run legs are complete measurements. All runs on the same
machine, same venv, `--no-cache --timeout 30`, sequential (not overlapped) —
these verdict classes are load-sensitive and the M4 receipt's timing caveat
applies here too.

The batched primitive is **~24x cheaper than the naive per-id loop** and
**~7.3x cheaper than a fresh full run** on this sample. Both ratios come
mostly from leg 1: the batch pays the ~100s coverage pass once and hands each
mutant only its covering tests (1-9 tests per survivor in this sample, vs 729
for the full run).

## Verdict transitions observed

With a new test file targeting the survivors' neighborhoods, the honest
result is: **51 of 51 still SURVIVED** in the batched re-measurement. The new
tests exercise the right functions but none of these 51 mutants is
assertion-distinguishable by them — a useful negative result for the C3
contract: re-injection must report "no transition" just as loudly as a
transition, and silence is a finding about the new tests, not a bug in the
loop.

The fresh full run (leg for comparison) showed one id flipping to
KILLED_BY_ERROR (`recipes.py:680:arithmetic_swap:0`, `killreason=test_errored`)
under its larger per-run context. That is exactly the correctness floor the
contract already names: **KILLED_BY_ERROR is not a kill** — the only token
that says "checked it" is `assertion_failed`. A transition table must keep
KILLED_BY_ERROR out of the killed column.

## Cost model for the re-injection loop (N survivors)

With `B` = baseline-pass cost (~100s here, scales with suite size × (1 +
probes)) and `m` = mean per-mutant verification cost (fork floor ≈ suite-slice
startup + covered tests' time):

- Batched: `T ≈ B + N·m` — 257s = ~100s baseline + 51 × ~3.1s here.
- One-at-a-time: `T ≈ N·(B + m)` — the baseline is paid N times. This is the
  24x gap, and it is structural, not tunable: it is why the CLI surface the
  dx task ships must accept many ids in one invocation as the primary path,
  with single-id as the degenerate case.

Reuse levers, in the order they matter:

1. **Batch ids into one invocation** (measured, biggest lever). The coverage
   pass is shared; nothing else comes close.
2. **`--flaky-probe 0` for re-injection** — the probes re-run the whole suite
   uninstrumented. For a re-run of known survivors (already flaky-screened in
   the full run that produced the export) the SUSPICIOUS verdict is rarely
   worth 1+ full suite runs. At probe=1 here that would roughly double the
   batch cost; the dx surface should default re-injection to probe=0 and say
   so.
3. **Cache** — `moonbuggy run` deliberately never reads the cache (the point
   is re-measuring), but a fingerprint keyed on the changed test file could
   skip the baseline pass when the coverage mapping is unchanged. Deferred:
   correctness-sensitive (a stale linemap silently mis-selects tests), and the
   baseline pass is not the dominant term once batching is in.
4. **Coverage-guided selection** — already the runner's behavior (each mutant
   runs only its covering tests); the re-injection loop inherits it for free.
5. **`-j` parallel mutant runs** — each run is an independent subprocess, so
   wall clock scales near-linearly with cores for SURVIVED-heavy batches.
   Measured separately on the H33 work; unchanged here.

Fork floor assumption (from the 1:1, §4.1): a mutant cannot cost less than
one pytest subprocess startup against its test slice (~1-3s here even for
1-9 tests). The batched numbers sit near that floor; the remaining headroom is
in the baseline pass, not the per-mutant leg.

## Implications for the dx CLI surface (shipped: `run --against`)

The surface landed on top of these numbers, not beside them:

- Primary path: read ids from stdin, one per line — `moonbuggy run -`.
- `--against survivors.jsonl` (requires `--trace-json`) pairs each fresh
  verdict with its export record under a `reinject` subdocument
  (`reinject_schema: 1`): the prior status/survival_reason, and a closed
  transition token — `survived->assertion_failed` (a real catch),
  `survived->killed_by_error` (**not** a kill, per the rule below),
  `survived->survived` (no transition). The stderr summary adds
  `transitions=...  caught=N`, and `caught` counts only assertion_failed
  catches.
- Exit code: unchanged — the full run's contract holds (1 when any
  re-measured id is still a finding), so a CI gate can fail on it while
  reading transitions for the humans.
- `--flaky-probe 0` remains the documented recommendation for re-injection
  (lever 2): the probes are worth a verdict about the suite, and a survivor
  re-run is a verdict about a mutant. Deliberately a documented invocation
  rather than a changed default — flipping `run`'s default would move the
  SUSPICIOUS semantics, which is a verdict-behavior change.

## Receipts

- Batched rerun of 51 ids: 256.94s wall (`rerun.err` in the task workspace,
  2026-09-21). All 51 SURVIVED.
- One-at-a-time sample (10 of 51 ids): 1220.98s → 122.10s/id.
- Fresh full run, same checkout with the new test file: 1875.26s wall,
  381 mutants, KILLED=132 KILLED_BY_ERROR=104 NO_COVERAGE=7 SURVIVED=101
  TIMEOUT=37 (verdict-class mix shifted vs the M4 receipt for the same
  load-sensitivity reason the M4.2 note records; the survivor set the loop
  re-runs is defined by the export, not by this rerun).
- Full suite green before and after adding the new test file: 729 passed,
  19896 subtests passed in ~9s.
