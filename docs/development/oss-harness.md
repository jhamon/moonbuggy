
## 2026-09-20 — M4.1/M4.2: more-itertools fresh run

- **Harness:** `scripts/oss_hunt.py` (M4 harness; workdir
  `~/.cache/moonbuggy-oss/`, isolated per-target venv with its own pytest).
- **Pin:** more-itertools `v11.1.0` (commit `64be96c`).
- **Scope:** `more_itertools/recipes.py` only (same bounded sample as the
  original M4 run).
- **Green baseline first (M4.1):** the project's own suite at this tag
  passes on this machine before any mutation: **722 passed, 19896
  subtests passed** in 8.82s (`pytest -q -p no:cacheprovider`).
- **moonbuggy:** `0.2.0` (pip-installed editable from this working tree).
- **Run command:**
  `python -m moonbuggy.cli --no-cache --quiet --source more_itertools
  --include recipes.py --timeout 30`
- **Fresh run:** 381 mutants; KILLED=129  KILLED_BY_ERROR=102  NO_COVERAGE=7  SURVIVED=51  TIMEOUT=92;
  mutation score 0.7993.
- **Timing note:** the recorded run under system load widened per-mutant
  wall time so that TIMEOUTs crowded out genuine SURVIVED verdicts;
  these numbers are from an unloaded run. TIMED-OUT and SURVIVED are
  load-sensitive verdict classes for this target; KILLED is stable.
- **Nothing was reported upstream.** Findings stay in this repository.
