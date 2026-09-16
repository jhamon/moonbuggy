+++
title = "The honor-system benchmark"
date = 2026-09-14
author = "moonbuggy"
tags = ["mutation-testing", "benchmarks", "python"]
description = "Every speedup number in Python mutation testing is self-reported, including ours. Here's why that's a problem, and what a benchmark receipt should look like."
+++

pytest-gremlins reports 13.82x speedup with caching and 3.73x parallel versus mutmut. fest, a Rust-powered mutation tester for Python, launched on Reddit claiming roughly 25x faster than cosmic-ray. Both are interesting tools. Neither number has been reproduced by anyone outside the project that published it. That includes us.

We're not picking on either project. The whole speed race in Python mutation testing runs on honor-system math. Every tool benchmarks itself, on its own suite, against whatever baseline it picks, and publishes the winner. There is no shared harness, no third party running the same workload through gremlins, fest, mutmut, cosmic-ray, and anyone else with a claim. Until someone does, every number in this market is a claim, and that goes for the ones on our README too.

## Our own numbers, held to the same standard

moonbuggy's README says 41x over naive and about 1.7x over mutmut on the same machine, via `make bench`. We have not independently re-run those numbers on a fresh harness recently, so treat them the way we treat gremlins' 13.82x: a self-report, not a fact you can bank. We'd rather say that plainly than let a big number do quiet work in a README.

This matters more for mutation testing than for most tools. A mutation tester's entire value is that its verdicts are mechanical. You trust KILLED and SURVIVED because they come from running real tests against real mutants, not from anyone's judgment. A tool whose product is mechanical proof should not ask you to take its benchmark on faith. Otherwise it's marketing with a decimal point.

## What a benchmark receipt should name

A speedup number without its shape is a boast. When we publish one, we want it to carry:

- the harness and command, so you can run the identical thing,
- the suite and the commit it was measured against,
- the machine, because "same machine" is doing real work in any A/B claim,
- and where we lose, if we lose.

That last one is the part nobody publishes. It's also the part that makes the rest believable. If a benchmark only ever reports victories, the reader learns to discount it, and they're right to.

## Where this leaves the field

The claims on the table right now, labeled honestly:

- pytest-gremlins: 13.82x with caching, 3.73x parallel vs mutmut. Self-reported.
- fest: ~25x vs cosmic-ray. Self-reported, from the launch thread. (fest is up to 0.1.3 on PyPI as of our last look.)
- moonbuggy: 41x over naive, ~1.7x over mutmut. Self-reported, and due for a fresh run.

None of these numbers has met the others on the same machine. That's the gap. We've offered to run a straight A/B against the newer tools on our bench and publish the output either way, and the offer stands. When we do, the post will name the harness, the suite, the machine, and every place a competitor beats us.

Until then: be suspicious of any speedup in this space, including the one in our README. Ask for the harness. Ask what the tool loses on. A benchmark you can't check isn't evidence, and mutation testing is supposed to be the tool that deals in evidence.

## Sources

- pytest-gremlins README (Speed-First Architecture; 13.82x with caching, 3.73x parallel vs mutmut; self-reported): https://github.com/mikelane/pytest-gremlins (pulled 2026-08-30)
- fest launch thread on r/Python (~25x faster than cosmic-ray; self-reported): https://www.reddit.com/r/Python/comments/1roya4t/i_built_fest_a_rustpowered_mutation_tester_for/ (pulled 2026-08-30)
- fest on PyPI, version 0.1.3: https://pypi.org/project/fest-mutate/ (pulled 2026-09-07)
- moonbuggy README benchmarks (41x over naive, ~1.7x over mutmut via `make bench`; self-reported, not re-verified on a fresh harness as of 2026-09-15)
