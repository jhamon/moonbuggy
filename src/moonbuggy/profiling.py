"""Wall-clock accounting by phase (milestone M2.1).

The Phase 1 benchmark record contains two changes implemented on a hunch and
measured as noise, with a note that the profile which would have prevented it
was cheap. This module is that profile, and M2 makes taking it the mandatory
first step before any performance work.

Design constraints, in order of importance:

1. **The phases must add up.** M2.1.2 requires the named phases to account for
   at least 95% of measured wall clock, with the remainder reported explicitly
   as `other`. An "other" bucket quietly holding 30% is how the last round went
   wrong: it looks like a rounding error and is actually the answer.
2. **Off by default, and free when off.** A profiler that changes the thing it
   measures is worse than no profiler. When `MOONBUGGY_PROFILE` is unset every
   method here is a few attribute lookups on a disabled object.
3. **Honest about concurrency.** Mutants run in parallel, so the durations
   children report sum to more than the wall clock they occupied. Summing them
   into a wall-clock breakdown would produce a total over 100%. Instead the
   parent measures the wall clock of the whole mutant phase and splits it in
   the ratio the children reported -- which is a real attribution of shared
   time, and is labelled as such rather than presented as a measurement.
"""

import json
import os
import time
from contextlib import contextmanager

ENV_VAR = "MOONBUGGY_PROFILE"

# The phases M2.1.1 names. Declared here rather than created on first use so a
# phase that never ran shows as 0.0 instead of vanishing -- "the coverage pass
# took no time" and "the coverage pass did not happen" are different findings.
PHASES = (
    "discovery",
    "generation",
    "warm-session startup",
    "coverage pass",
    "planning",
    "per-mutant fork",
    "in-child test execution",
    "cache I/O",
    "reporting",
)


class Profiler:
    """Accumulates time per named phase for one run."""

    def __init__(self, enabled=False):
        self.enabled = enabled
        self.totals = dict.fromkeys(PHASES, 0.0)
        self.started = time.perf_counter()
        self.notes = {}

    @contextmanager
    def span(self, name):
        """Time a block and add it to `name`.

        Nested spans are not supported and not needed: a nested span's time
        would be counted twice, and the phases are chosen to be disjoint.
        """
        if not self.enabled:
            yield
            return
        began = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - began)

    def add(self, name, seconds):
        """Add measured seconds to a phase."""
        if not self.enabled:
            return
        self.totals[name] = self.totals.get(name, 0.0) + seconds

    def split(self, name, seconds, weights):
        """Attribute `seconds` of wall clock across phases in a given ratio.

        Used for the concurrent mutant phase, where per-child durations sum to
        more than the elapsed time. `weights` maps phase name to the measured
        (overlapping) time for that phase; each gets the same share of the real
        wall clock that it had of the measured total.

        :param name: the phase to record the remainder against if weights are
            empty, so the time is never lost.
        :param seconds: real wall clock elapsed for the whole phase.
        :param weights: ``{phase: measured_seconds}``.
        """
        if not self.enabled:
            return
        total = sum(weights.values())
        if total <= 0:
            self.add(name, seconds)
            return
        for phase, measured in weights.items():
            self.add(phase, seconds * measured / total)

    def note(self, key, value):
        """Record a non-timing fact worth reading next to the numbers."""
        if self.enabled:
            self.notes[key] = value

    def summary(self):
        """The report: per-phase seconds, wall clock, and what is unattributed.

        :returns: a dict with ``wall``, ``phases``, ``other``, ``attributed``
            (the fraction M2.1.2 gates on) and any recorded notes.
        """
        wall = time.perf_counter() - self.started
        accounted = sum(self.totals.values())
        return {
            "wall": wall,
            "phases": dict(self.totals),
            # Never clamped to zero. If attribution exceeds wall clock the
            # accounting is wrong, and a negative number here says so loudly
            # instead of hiding behind a max().
            "other": wall - accounted,
            "attributed": (accounted / wall) if wall > 0 else 0.0,
            "notes": dict(self.notes),
        }

    def write(self, path=None):
        """Write the summary as JSON, if profiling is on."""
        if not self.enabled:
            return None
        path = path or os.environ.get(ENV_VAR)
        if not path:
            return None
        summary = self.summary()
        with open(path, "w") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
        return summary


_ACTIVE = Profiler(enabled=bool(os.environ.get(ENV_VAR)))


def active():
    """The profiler for this process."""
    return _ACTIVE


def reset(enabled=None):
    """Start a fresh profiler. Used by tests and by the in-process harness."""
    global _ACTIVE
    if enabled is None:
        enabled = bool(os.environ.get(ENV_VAR))
    _ACTIVE = Profiler(enabled=enabled)
    return _ACTIVE
