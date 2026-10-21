+++
title = "Our benchmark gate failed on noise. We published the autopsies."
slug = "our-own-gate-failed-on-noise"
date = 2026-10-21
author = "moonbuggy"
tags = ["mutation-testing", "benchmarks", "ci"]
description = "Our nightly speed gate failed on unchanged code, three times, and the fix took three attempts. The wrong attempts stay in the record on purpose."
+++

Last month we wrote that the speed race in Python mutation testing runs on honor-system math: self-reported numbers, no shared harness, every claim a claim until someone reproduces it. The honest response to that post is to hold your own numbers to the same standard, so we do. There's a nightly CI job whose entire job is to check that our speed still holds, and it re-primes a baseline from real runs instead of trusting the README.

Here is what that gate did for the past four weeks: it failed. Repeatedly. On code that had not changed. And the fix took three attempts, because the first two were wrong in interesting ways.

All of it is written down, prediction by prediction, in a file in the repo. Including the parts where we were wrong.

## The failure, in one night

The gate works like this: run the speed workload, compare it to the last committed baseline, and fail if the run is more than 25% slower. Simple. The trap was in where the baseline comes from. If a nightly run happens to draw a fast number, the baseline ratchets down to it. So on October 8, a lucky draw of 0.83s landed as the baseline, which armed the wall at about 1.04s. The next two nights drew ordinary numbers, 1.35s both times on identical code and the same runner image, and failed both times.

Run [37758951785](https://github.com/jhamon/moonbuggy/actions/runs/37758951785) and [37913952284](https://github.com/jhamon/moonbuggy/actions/runs/37913952284), if you want to see the failures yourself. Nothing regressed. The comparison verdicts against mutmut were green both nights. The wall was simply below where an ordinary night lands, because an ordinary night is defined by a machine you don't control: shared GitHub runners carry roughly ±30% run-to-run scheduling noise on a one-to-two-second measurement.

One lucky number had armed a trap, and the trap worked exactly as designed. That's the worst kind of bug.

## Fix one: more reps. Wrong.

The obvious move is to stop trusting a single measurement. We ran the workload three times per night and gated on the minimum. Under one-sided noise, the minimum is the robust statistic, because scheduling noise only ever makes you slower, never faster.

The prediction, written down before the change shipped: nightly minima would land within about 10% of each other, well inside the wall.

Falsified. Min-of-3 on a single cold subprocess still carried more than 25% cross-night spread on the runners. The lucky 0.83s baseline came *from a min-of-3 run*; all three reps that night drew fast. Running it five times would have sunk the ratchet even lower. More statistics cannot rescue a statistic whose bad draw you already committed.

## Fix two: time, not statistics. Right mechanism, wrong calibration.

The next insight: runner noise doesn't repeat on consecutive nights, but a real regression does. So the gate stopped failing on a single crossing. One bad night earns a loud warning and names the next nightly run as the decision point; only two consecutive nights above the wall fail the gate. Symmetrically, the baseline only ratchets down after two consecutive fast nights, so a single lucky draw can never again arm a trap wall.

The mechanics worked, and we can prove it, because the next two nights exercised both halves. October 13 crossed the wall and warned without touching the baseline, exactly as specified. October 14 crossed again and the gate failed as sustained.

Except nothing had regressed. We checked the obvious suspect (the runner environment shifting under us) with a control: the naive-baseline leg of the same nightly bench, which re-runs the same tests the slow way, sat flat across all four nights (51.86, 52.72, 56.31, 53.01 seconds; a ±4% band). The comparison margins held in their usual band too. The draws weren't shifting. The wall was simply sitting *below the ordinary draw band* (the nightly minima ranged 1.34 to 1.47 seconds), so crossing was the norm, and any two consecutive ordinary nights tripped the sustained rule.

The gate's own verdict, in the record: "the mechanics work, the wall is miscalibrated."

## Fix three: measure the spread, then set the wall from it

That leaves one honest move. The 25% slack was an arbitrary constant; the observed spread was a measurement. The nightly minima spanned 0.83 to 1.47 seconds on identical code and host, a ratio of 1.77. So the slack is now calibrated to cover that span with margin: 1.8x, with the derivation in a comment next to the constant.

We had previously rejected this exact option, on the record, with the argument that a wider wall "loosens the wall for real regressions." The falsification reversed that trade. A wall that trips on noise every night has zero value as a gate: it fails ordinary nights while never having measured a genuine regression at all. The new wall still catches what it can actually catch: a sustained environmental shift bigger than 1.8x, like a runner-image or Python change doubling the workload time, which still goes red on the second night. Shifts smaller than that are covered by other instruments (the per-commit A/B harness and the comparison margin) that measure relative change and don't share this failure mode.

Before merging, we replayed the real archived nightly rows against the new wall. The night that warned under the old wall now passes cleanly, and the full observed span sits inside it for any baseline in the observed range. The gate's unit tests were updated to the new numbers, plus three new ones, 24 passing.

Since the fix merged, the confirmation runs have landed: three consecutive nightly benches passed clean — [39108356157](https://github.com/jhamon/moonbuggy/actions/runs/39108356157) on October 18 (min 1.41s), [39221977224](https://github.com/jhamon/moonbuggy/actions/runs/39221977224) on October 19 (min 1.32s), and [39390772268](https://github.com/jhamon/moonbuggy/actions/runs/39390772268) on October 20 (min 1.40s) — all inside the new 1.49s wall, no warnings, and the baseline re-prime forced by the Ubuntu 26 runner migration behaved exactly as predicted. The wall now has measurements behind it, not just a derivation.

## What you can copy

Three things, none of them specific to mutation testing:

1. **A gate anchored to a lucky measurement is a trap, not a gate.** If your CI threshold ratchets to the best observed value of a noisy measurement, you have armed a failure against ordinary days. Check where your baseline comes from before you trust where your wall sits.
2. **Write the prediction before the fix.** All three attempts here were logged as falsifiable predictions with explicit confirmation criteria, which is the only reason "fix two" could be *falsified* rather than quietly patched again. The wrong predictions stay in the document on purpose; a log that only records wins teaches nothing.
3. **Calibrate constants from measurements, not round numbers.** "25% sounds like enough" is how a wall ends up below the noise floor. The observed spread of your own statistic, over enough nights to include its bad draws, is the only defensible source for that number.

The full record, including the run IDs and the two falsified predictions in full, lives in the moonbuggy repo's performance-hypotheses document. It is not a highlight reel. That is the point.
