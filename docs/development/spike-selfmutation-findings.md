# Spike D findings — M1.1 self-mutation containment

**Status:** resolved (round 1), **superseded in round 2** — see Finding 4
below. Round 1's verdict: *the containment scheme works, and it needs three
isolations, not two.* Round 2's verdict: the `PYTHONPATH` boundary that fixes
round 1's vacuity **also silently voids the pinned-runner pillar** — the parent
process imports and executes the target's engine — and the
boundary-fixed rig died deterministically at the same point in five
consecutive full runs. Round 2 changed nothing in the runner or forkserver;
it only ran the rig more and read the code closer. Nothing in the runner or
forkserver changed in either round. The spike was time-boxed and ran as a set
of ad-hoc probes (this document records them); a checked-in rig script is the
deliverable that would make M1.1's criteria verifiable, and none of M1.1's
criteria are marked met here.

Findings 1–3 are round 1; Findings 4–7 are round 2. Round 1's findings stand
as written but describe runs made before the boundary fix or with it half
applied; where round 2 re-derives a claim, the round-2 finding wins.

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

*(Round 1 evidence, made before the `-E` fix of Finding 4 existed — read with
Finding 4 in mind: the run below executed the target's engine in the parent.
The `-E`+subprocess rig re-demonstrates detection cleanly in Finding 6.)*

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
   suite is fast per mutant (median 1.0–1.5s in the boundary-fixed runs,
   which select only covering tests) but slow in aggregate, and mutants that
   make the suite slower (dataclass `frozen=False`, ledger constants, etc.)
   ride close to the limit. Under load they cross it. With `--timeout 120`
   the TIMEOUT count dropped to zero in the vacuous-era runs; in the
   boundary-fixed runs 11 `forkserver.py` mutants TIMEOUT even at 120s
   (mutating the engine's own fork/pipe protocol makes the suite hang).
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

## Finding 4 (round 2) — the parent also runs the code under mutation

Finding 1's fix (`PYTHONPATH=<target>/src`) fixes the children's imports by
putting the target first on `sys.path` — **for every process the rig
launches, including the runner itself**. The runner parent is launched from
the rig with `PYTHONPATH` already exported, so `import moonbuggy` in the
parent resolves to the target's copy: probed directly, a `runner-venv`
interpreter with the rig's environment resolves `moonbuggy.__file__` to
`target/src/moonbuggy/__init__.py`, not to the pinned install. The pinned
venv's *engine* is then used only as a launcher; the code doing the mutation,
the forking, and the verdict assembly is the code under mutation. The
forkserver docstring's own invariant ("the parent must NEVER import the module
under test") is violated by construction — not by a moonbuggy bug, but by the
rig's environment.

The consequence is exactly the corruption the containment scheme was meant to
make impossible: a mutant of the target's `forkserver.py` mutates the engine
that is *currently running that engine*. In `pp4`, the mutant
`forkserver.py:615:constant_bool:0` was observed executing in-process with its
mutation applied (its parent-process behaviours changed with the mutant).

**The partial fix the rig can apply today:** launch the runner parent with
`python -E -m moonbuggy.cli` while still exporting `PYTHONPATH`. `-E` makes
the parent ignore `PYTHONPATH` (it imports the pinned install), and probe
confirmed that a child process spawned by such a parent with the inherited
environment *does* still resolve the target — `_env_for` copies `os.environ`
into each child. But this only holds for paths whose children are fresh
subprocesses (the cold `--workers`/xdist path and the coverage pass, both
verified to complete cleanly with `-E`). The fork-based warm path cannot be
fixed this way: a forked child inherits the parent's `sys.path`, so an `-E`
parent's grandchildren would import the pinned copy and every mutation would
be a no-op — the vacuity of Finding 1 from the other side. The `-E` variant
verifies containment (see Finding 6) at the cost of the warm path's speed;
making both true at once needs an engine change (an explicit
`--engine-src`/interpreter-selection surface, or a per-child env handoff in
the fork path) and is out of scope for the spike.

## Finding 5 (round 2) — the boundary-fixed rig dies silently at the endgame, deterministically

Five consecutive full runs with the identical boundary-fixed configuration
(`pp2`–`pp6`, one with purged bytecode caches) all completed the covered
phase with identical verdicts (see above) and then died without a traceback,
an empty log, and no crash report, in the interval between the last verdict
landing in `results.jsonl` and the final report being printed. The on-disk
signature is identical every time: `results.jsonl` is ~573.7KB with a
396,958-byte NUL prefix followed by the 281 streamed records intact. The
pattern — file prefix zeroed while later data survives, no Python-level
error, five-for-five determinism — matches an OS-level forced kill of the
parent during the end-of-run rewrite (the box was under chronic memory
pressure, 13.0/14.3GB swap used), but the trigger was not isolated within
the time-box. What matters for M1.1:

- **It is a rig-configuration problem, not (yet proven) a runner bug.** The
  control runs — six vacuous-era full runs and every `-E` scoped run —
  completed normally. The only variable that flips the behaviour is "the
  parent executes the target's engine" (Finding 4).
- **The streamed partial file is still a valid, parseable JSONL** (the
  streaming guarantee holds), so a killed run loses only the report, not the
  verdicts. But M1.1.6's receipt requires the *canonical* rewrite to survive,
  and under this rig it never did.
- The red-baseline gate worked exactly as designed in a separate containment
  demo: with a planted defect in `export.py:83`, the rig's run refused to
  start, naming the three failing tests (M1.4.4 exercised on ourselves).

Until Finding 5 is resolved, **M1.1.6's "run reaches a verdict on every
covered mutant" cannot be certified by the rig as currently configured** —
the rig must either adopt the `-E`+subprocess configuration or the endgame
kill must be explained. Both are recorded as rig requirements, not product
changes.

### Resolution (perf branch `perf/f5-endgame-silent-death`, 2026-09-29)

Root-caused to a runner bug, not the OS kill the round-2 note suspected. The
audit-hook log of an instrumented repro (`f5c`) shows the mutant on
`cli/__init__.py:176`'s entry guard `if __name__ == "__main__": run()`
(condition_negation) is a module-level statement, so the warm-path grandchild
applies it with `codeswap._exec_module_level`, which re-executes the whole
statement in the module's namespace. In a forked child `__name__` is
`"moonbuggy.cli"`, so the *inverted* guard is TRUE and `run()` executes — a
full recursive moonbuggy campaign on the inherited argv, in a process that
inherited the parent's open `results.jsonl` descriptor. The nested campaign
truncates that file (`StreamingJSONL` opens `mode='w'`), the parent's writes
at the inherited offset re-create the data as a NUL hole (matching the
396,958-byte prefix exactly), and the nested `run()` ends in `os._exit`,
killing the grandchild before it can report a verdict. Five-for-five
determinism and the "no traceback" signature follow.

Fix: `_exec_module_level` now refuses (`SwapFailed`) any module-level
statement that binds no names — re-execution exists to rebind names, and a
statement that binds none (`__all__ +=`, `del`-only, a bare `if`/`try` with
no `def`/assign/for targets) has no mutation to apply, only side effects to
re-trigger. The existing `_rerun_unapplied` machinery sends the refused
mutant down the cold fork path, where the import hook re-imports the module
from mutated source and the guard is evaluated once, at import, as it would
be in production. Regression tests: `tests/test_statement_side_effects.py`
(4 tests; RED before the fix, GREEN after). End-to-end verification on a
minimal entry-guard fixture under the fork path: run exits 0,
`results.jsonl` intact with no NUL prefix, guard mutants settled SUSPICIOUS
(the documented semantics of a mutated guard that breaks test collection —
`runner.py`'s `_status_from_exit`), no nested campaign.

## Finding 6 (round 2) — the `-E`+subprocess rig detects a planted defect, end to end

With the runner parent launched `-E` (pinned engine), `--workers 2` (fresh
pytest subprocess per mutant), and `PYTHONPATH` exported for the children:

- Clean target: the scoped run (`--include export.py`, 54 mutants) completed
  in 29.7s, produced the full report, and its 3 covered-mutant verdicts
  (KILLED, KILLED, KILLED — tests_run=598) are consistent with the full run's
  coverage data.
- Planted target (the same hand-edit moonbuggy's own `condition_negation`
  operator generates at `export.py:83`, `if record["accepted"]:` →
  `if not ...`): the run **refused to start** — the red-baseline gate fired,
  naming exactly the three `test_survivor_export` tests the plant breaks.
  That is the plant detected by moonbuggy's own machinery (M1.4.4 behaviour)
  before any mutation ran, and the refusal is the correct outcome: with the
  plant live, the gate's verdicts would be meaningless.
- After reverting the plant, the same rig re-ran green — the plant left no
  residue, and the pinned install's files stayed byte-identical
  (hash-checked) throughout.

This is the evidence behind M1.1.4 and M1.1.5 in the round-2 shape: the plant
is detected *by the gate*, the runner keeps its own integrity, and the
import boundary is real for every process that runs tests. It is a spike
demonstration, not a met criterion — the criteria require the rig itself to
perform and check the plant.

## Finding 7 (round 2) — a poisoned bytecode cache defeats "pinned install" silently

Round 2's containment demos initially failed for an unexpected reason worth
recording: the pinned venv's `export.py` bytecode disagreed with its own
source (`SCHEMA_VERSION` 2 vs 1). Mechanism: an earlier probe mutated the
venv's installed `export.py` in place, a `.pyc` was compiled from the
mutated source, and the source was restored within the same clock second —
so the `.pyc`'s staleness validation (which stores whole-second source
mtime + size) passed forever after, and every import of the "pinned"
package executed mutated engine code. Diagnosed by comparing the pyc's
disassembled constants against a fresh compile of the source; confirmed by
purging `__pycache__`, after which the pinned install behaved as pinned.

The milestone consequence is a new check for the rig (folded into M1.1.2):
**the import-boundary probe must verify semantics, not just paths** —
e.g. assert a known module-level constant or recompile-and-compare one
module — because `moonbuggy.__file__` pointing at the pinned install does
not prove the *bytecode* loaded from it is the pinned install's. Byte-level
`diff -r` of venv vs target source also does not catch this; only the
pyc-vs-source comparison does.

## What a self-run of moonbuggy actually looks like (with the boundary fixed)

The boundary-fixed runs are the strongest evidence the spike produced, and
also the runs that exposed round 2's findings. Across five full attempts
(`pp2`–`pp6`) the covered phase completed **identically** — 281 covered
mutants, 122 KILLED, 57 KILLED_BY_ERROR, 91 SURVIVED, 11 TIMEOUT, zero
verdict disagreements in three-way comparison (`pp2`/`pp3`/`pp4` record for
record) — then every one of the five runs **died silently in the endgame**
(see Finding 6) before the final report was written. The 281-record snapshot
is stable and consistent; the *receipt* (all 1702 records, canonical order,
`results.txt`/`summary.json`) was never produced by the boundary-fixed rig.
The killed share jumps from the vacuous 1% to roughly half of covered
mutants, and the ~91 SURVIVED records are precisely the self-findings
(unasserted covered behaviour) the milestone is meant to harvest. The full
receipt, drift-checkable, is M1.1.6's deliverable.

## Costs and consequences

- **Time.** With the boundary fixed, a covered mutant's median wall time is
  1.0–1.5s (the runner executes only the covering tests per mutant, in a
  forked grandchild); the mean is 9–11s only because 11 mutants of
  `forkserver.py` itself hit the full 120s `--timeout` (mutating the engine's
  own fork/pipe protocol makes the suite hang). The `--source src` surface is
  1702 mutants; in the boundary-fixed regime **281 have coverage** (the
  vacuous runs' `95 covered` figure was an artifact of the missing import
  boundary — see Finding 1). A full run costs ~35–45 wall minutes at `-j2` on
  this laptop. The 281/1702 covered ratio (16.5%), checked in as a receipt,
  is the milestone's honest headline number — and the 1607 NO_COVERAGE
  records are the test-suite gap the milestone exists to harvest.
- **No product changes.** Everything the spike needed already exists:
  non-editable install, separate checkouts, `PYTHONPATH` inheritance, the
  red-baseline gate, the flaky probe. The rig is glue, which is why it
  belongs as a script (M1.1.1) rather than new machinery.
- **Do NOT integrate into CI yet.** The stability question (Finding 3) is
  unresolved and the run cost is laptop-scale hours, not minutes. M1.1.8
  keeps the rig out of CI until M1.1.7 is answered with numbers.
