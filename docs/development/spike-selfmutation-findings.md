# Spike D findings — M1.1 self-mutation containment

**Status:** resolved. Verdict: **the containment scheme works, and it needs
three isolations, not two.** Nothing in the runner or forkserver changed. The
spike was time-boxed and ran as a set of ad-hoc probes (this document records
them); a checked-in rig script is the deliverable that would make M1.1's
criteria verifiable, and none of M1.1's criteria are marked met here.

**The question.** M1.1 (self-mutation) was left vacant because the code under
mutation would be the mutation engine itself: a defective mutant could corrupt
the run meant to detect it. The plan called for a pinned separate install
mutating a separate checkout. The spike asks whether that containment actually
holds, end to end, with the real pinned runner and a real self-run.

## The rig

- **Runner:** moonbuggy at a pinned ref, installed non-editably into its own
  venv (a real site-packages copy — `moonbuggy.__file__` resolves under
  `runner-venv/lib/python3.12/site-packages/`, never into either checkout).
- **Target:** a separate clone of the repo, at the ref under test
  (`da363f3`), with the pinned runner invoked with `--project . --source src`.
- **Isolation checks run up front:** the target's full test suite (598
  passing) under the runner venv's interpreter; and `git status` clean in both
  checkouts before and after.

Everything below ran through that rig. Nothing here touches the dev
checkout's working tree.

## Finding 1 — two isolations are not enough: the import boundary is the third

The scheme as written (pinned install + separate checkout) **silently
produces vacuous verdicts**. The first full self-run produced 1702 mutants,
1% killed, in 137s — plausible-looking, and wrong.

The mechanism: the runner's child pytest processes import `moonbuggy` from the
runner venv's site-packages, because that venv has moonbuggy installed and
nothing puts the target's `src/` ahead of it. The mutant payload the runner
hands its children (`runner._env_for`) points at the **target's** file — so
the in-memory mutation is installed against a path the suite never imports.
Every mutant is a no-op for the code under test. The verdicts are not merely
noisy; they measure nothing.

Confirmed mechanically: with the rig's default environment, a probe process
run from the target directory resolves `moonbuggy.export` to the runner venv's
copy; the pin test (`test_schema_version_pin_matches_the_frozen_const`) passes
under a mutation of the target's file and fails under the same mutation
applied to the venv's copy.

**The fix is one environment line, and the runner already cooperates:**
`PYTHONPATH=<target>/src`. Children inherit the runner's environment
(`_env_for` starts from `dict(os.environ)`), and `PYTHONPATH` precedes
site-packages on `sys.path`, so every child pytest imports the target copy.
Re-running the pin-test probe with that boundary set: the target's mutation is
now observed by the suite that imports the target.

**This is the spike's central deliverable:** M1.1's scheme must *verify* the
import boundary before the run (M1.1.2 in the milestone) — probe
`moonbuggy.__file__` from a child run with the rig's environment and refuse to
start unless it resolves under the target checkout. The failure mode without
this check is exactly the failure mode self-mutation exists to catch: a
confident, well-formatted verdict about code that never ran.

## Finding 2 — with the boundary fixed, the runner detects planted defects

A defect was planted by hand in the target's
`src/moonbuggy/report.py:60` (`_SCHEMA_2_DEFAULTS` `logging_call` fill flipped
`False` → `True`, the same edit moonbuggy's own `constant_bool` operator
generates there). Re-running the pinned runner against the planted target:

- 26 verdicts changed relative to the clean baseline, including
  `report.py:60:constant_bool:0` itself flipping KILLED → SURVIVED (the
  "defect" becomes the baseline, so the mutant no longer differs from it),
  plus three other KILLED → SURVIVED flips and 18 TIMEOUTs from the
  now-slower suite.
- The runner completed its run, reported the changed verdicts, and its own
  installed package was byte-identical to the pinned install afterwards
  (hash-checked). Containment held in both directions: the runner sees the
  target's defect, and the defect does not touch the runner.

This is the evidence behind M1.1.4 and M1.1.5. It is a spike
demonstration, not a met criterion — M1.1.4 requires the rig itself to
perform and check the plant.

## Finding 3 — the target's own suite is timing-sensitive at the boundary

Across repeated identical-configuration runs on this machine (a laptop under
ordinary desktop load), a handful of mutants flip between KILLED, SURVIVED and
TIMEOUT at the timing boundary: two identical j2/t120 runs disagreed on 8 of
1702 records. Three sources, in order of confidence:

1. **Genuine load sensitivity.** `--timeout` is per-mutant wall clock; the
   full 598-test suite runs ~7.5s per covered mutant, and mutants that make
   the suite slower (dataclass `frozen=False`, ledger constants, etc.) ride
   close to the limit. Under load they cross it. With `--timeout 120` the
   TIMEOUT count dropped to zero; with the default 30s, 88-93 mutants
   TIMEOUT.
2. **`KILLED_BY_ERROR` vs assertion kills** move with test ordering and
   resource state (3-4 flips per run pair). These are the M1.4.3-class
   flaky-adjacent cases, and the flaky probe catches them only when
   `--flaky-probe N` is set (a probe-2 run reclassified all 95 as
   `SUSPICIOUS` with `killreason=flaky_probe`, as designed).
3. **A residual kill-attribution ambiguity** on one mutant id
   (`export.py:52`, KILLED in the first cold run, SURVIVED in four later
   runs including `--no-cache`), not fully resolved here and named in
   M1.1.7's stability requirement rather than buried.

4. **Self-poisoning across runs.** A self-mutation run that dies mid-flight
   leaves `target/.moonbuggy/results.jsonl` behind — the target's own test
   suite contains subprocess tests that invoke moonbuggy with the default
   output dir — and the *next* run's red-baseline gate refuses to start
   because of it. This is the gate working (a stale results file genuinely
   poisons the baseline check), but it means the rig must clean the target's
   artifacts between runs, and that a crashed run is not recoverable by
   simply re-running.

The conclusion for the milestone: self-mutation needs a quiet machine and an
explicit, generous `--timeout`, and its verdict claims must be preceded by a
stability statement (two identical runs, disagreement list). A
self-mutation run that cannot be reproduced is worse than none, because the
failure would read as a finding.

## What a self-run of moonbuggy actually looks like (with the boundary fixed)

Early returns from the definitive run (still in progress at the time of
writing; the numbers are a snapshot of 211/1702, not a receipt): the killed
share jumps from the vacuous 1% to roughly half of covered mutants —
88 KILLED, 46 KILLED_BY_ERROR, 78 SURVIVED, 6 TIMEOUT — i.e. moonbuggy's own
suite kills most of what it can reach, and the ~78 SURVIVED records are
precisely the self-findings (unasserted covered behaviour) the milestone is
meant to harvest. The full receipt, drift-checkable, is M1.1.6's deliverable.

## Costs and consequences

- **Time.** With the boundary fixed, every covered mutant runs the full
  598-test suite (~7.5s); 95 covered mutants ≈ 12 min of suite time per full
  run on two workers, plus coverage/baseline overhead. The `--source src`
  surface is 1702 mutants, 1607 of them currently NO_COVERAGE — the test
  suite exercises ~6% of moonbuggy's own mutable lines. That ratio, checked
  in as a receipt, is the milestone's honest headline number.
- **No product changes.** Everything the spike needed already exists:
  non-editable install, separate checkouts, `PYTHONPATH` inheritance, the
  red-baseline gate, the flaky probe. The rig is glue, which is why it
  belongs as a script (M1.1.1) rather than new machinery.
- **Do NOT integrate into CI yet.** The stability question (Finding 3) is
  unresolved and the run cost is laptop-scale hours, not minutes. M1.1.8
  keeps the rig out of CI until M1.1.7 is answered with numbers.
