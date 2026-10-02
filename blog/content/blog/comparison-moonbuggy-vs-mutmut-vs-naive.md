+++
title = "Which mutation testing tool should you actually run? We benchmarked three on the same code."
slug = "comparison-moonbuggy-vs-mutmut-vs-naive"
date = 2026-10-01
author = "moonbuggy"
tags = ["mutation-testing", "benchmarks", "comparison"]
description = "One library, one commit, three tools, one recorded run: moonbuggy 277.8s, mutmut 333.6s, naive baseline 3,716.4s — with every caveat on the table."
+++

277.8 seconds. That's what it took moonbuggy to generate 381 mutants of more-itertools and run the library's own test file against every one of them. The same job took a naive re-run-everything baseline 3,716.4 seconds. mutmut, in between, took 333.6 seconds, but it also generated 1,085 mutants, because its operator set is bigger. Those two numbers are not the same job, and this post is not going to pretend they are.

If you have a Python test suite and you're trying to pick a mutation testing tool, this is the comparison we wish existed: one real library, one fixed commit, three tools, every number from a single recorded run with the receipt attached.

## The ground rules

What we ran, and what the numbers do and don't mean:

- **Subject:** more-itertools v11.1.0, commit 64be96ce. Scope: `more_itertools/recipes.py` and its dedicated test file, `tests/test_recipes.py`. Real code, real tests, not a toy fixture.
- **Tools under test:** moonbuggy 0.2.0 (commit 2ba50e5, code-identical to main at the time), mutmut **3.7.0**, and a naive baseline that re-runs the test suite once per mutant with no selection or reuse at all.
- **One run, one machine:** macOS (Darwin 24.1.0), Python 3.12.13, single execution per tool. Under heavy load: all three legs ran on the same machine in sequence while other work was happening. The ratios below are within-run, so the contention hits all three legs, but treat everything as a single data point, not a median.
- **Not like-for-like:** mutmut generated 1,085 mutants to moonbuggy's 381. A bigger operator set finds more potential bugs and costs more time by design. Comparing 333.6s to 277.8s directly would be dishonest, so we won't do it.
- The receipt for every number here is the harness row and final report from the run, recorded 2026-09-28.

## The numbers

| tool | wall clock | mutants | verdict breakdown |
|------|-----------|---------|-------------------|
| moonbuggy | 277.8s | 381 | KILLED=121, KILLED_BY_ERROR=78, NO_COVERAGE=43, SURVIVED=102, TIMEOUT=37 |
| mutmut 3.7.0 | 333.6s | 1,085 | KILLED=893, SURVIVED=100, TIMEOUT=62 (the remaining 30 of the 1,085 fell in categories our harness doesn't parse from mutmut's progress line) |
| naive baseline | 3,716.4s | 381 | KILLED=170, SURVIVED=145, SUSPICIOUS=9, TIMEOUT=57 |

Three things worth pulling out of that table.

**moonbuggy vs the naive baseline: 13.38x, and this one is like-for-like.** Same 381 mutants, same operator set. The naive baseline is what mutation testing costs with no engineering: fork, mutate on disk, run the whole test file, restore, repeat, 381 times. moonbuggy's answer to that is architectural (mutate in memory, no disk round-trips), and the 13.38x is what the architecture buys. The "no mutants pruned" check also passed: moonbuggy produced exactly the same 381 mutants as the naive baseline, so the speed isn't coming from quietly skipping hard cases.

**moonbuggy vs mutmut: faster on wall clock, but read the fine print.** 277.8s vs 333.6s. mutmut 3.7.0 generated 2.8x as many mutants in that time, which means its per-mutant throughput was actually higher (3.3 mutants/sec vs 1.4). If you want the widest operator net and don't mind the extra time, that's a real trade mutmut offers. If you want the operator set we consider the useful core, moonbuggy finished the job in less wall time. We're flagging this instead of burying it, because "1.20x faster" as a bare headline would be the kind of number that doesn't survive a skeptic.

**The naive baseline is the honest floor, and it's brutal.** 3,716.4 seconds is over an hour for one file of one library. That's the cost curve that made mutation testing a thing teams run weekly at best. It's also the comparison every speed claim should be measured against, and most aren't.

## What we deliberately didn't compare

Correctness and workflow, mostly, because this run wasn't designed to measure them:

- **mutmut 3.8.0.** The venv resolved 3.7.0 and we're labeling everything accordingly. 3.8.0 shipped forkserver process isolation after this run; benchmarking it is a future run, not a claim here.
- **Survivor quality.** mutmut reported 100 survivors from 1,085 mutants; moonbuggy reported 102 from 381. Survivor counts across different operator sets don't compare cleanly, and "who has fewer survivors" is the wrong question anyway. What matters is whether each survivor comes with a reason you can act on, and that's a separate piece of work from this benchmark.
- **Timeout handling, CI integration, incremental runs.** All real selection criteria. Not measured in this run; not asserted here.

## So which tool should you run?

Our honest decision guide, given what this run actually measured:

- **You want to know what your tests actually catch, on a real project, without an hour-long run:** moonbuggy's 13.38x over the naive floor is measured, same-mutants, and the receipt is public. This is the job the tool is built for.
- **You want the largest possible operator net and wall time is your constraint, not mutant count:** mutmut generated nearly 3x the mutants in comparable wall time. That's a legitimate choice and the numbers above support it.
- **You're deciding whether mutation testing is worth running at all:** look at the naive row. That's the baseline cost you're avoiding no matter which tool you pick.

One run, one library, one machine. We're publishing it anyway because a verified single data point beats an impressive unverified one, and because this baseline now exists to be re-run. When mutmut 3.8.0 gets benchmarked, this post gets a successor, not a silent edit.

## Sources

- Harness row (JSONL, schema 1): suite `real-more-itertools-v11.1.0`, moonbuggy commit 2ba50e5, written 2026-09-29T01:43:05+00:00, archived in the project's internal benchmark records.
- Run conditions (subject commit 64be96ce, host Darwin 24.1.0, Python 3.12.13, mutmut 3.7.0 pin, contention note): run report header, 2026-09-28.
- Naive-baseline design rationale (per-mutant full test re-run as the honest cost selection exists to avoid): run report header, 2026-09-28.
- mutmut 3.8.0 release and forkserver isolation (not benchmarked here): [mutmut on PyPI](https://pypi.org/project/mutmut/#history) and its changelog, pulled 2026-09-26.
- more-itertools v11.1.0 at 64be96ce as the benchmark subject: run report header, 2026-09-28.
- moonbuggy's in-memory mutation architecture and the earlier benchmark numbers this run supersedes: [moonbuggy on GitHub](https://github.com/jhamon/moonbuggy), and the project's [benchmark results page](https://jhamon.github.io/moonbuggy/benchmark-results.html) (pulled 2026-10-01).
