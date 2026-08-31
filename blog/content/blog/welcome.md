+++
title = "Welcome to the moonbuggy blog"
date = 2026-01-15
author = "Jennifer Hamon"
tags = ["announcement"]
description = "The moonbuggy blog is live — mutation testing findings, release notes, and practical advice for tightening your test suite."
+++

The moonbuggy blog is where we share what we learn while hunting surviving mutants. Expect:

- **Release notes** — what changed, why, and what it means for your test suite.
- **Findings** — interesting mutants we've found in real open-source projects and what they reveal.
- **How-to guides** — practical advice on reading output, investigating survivors, and convincing your team that mutation testing is worth it.
- **Comparisons** — how moonbuggy stacks up against other tools on real workloads.

## Why a blog?

The documentation tells you how moonbuggy works. The blog tells you what we're finding with it. A `SURVIVED` line in the output names a gap in your suite — down to the line and operator. The blog is where we tell the story of how we found it, what it means, and how to fix it.

## What's shipping now

The blog itself. This is the first post. The docs site at [jhamon.github.io/moonbuggy](https://jhamon.github.io/moonbuggy/) now has a unified top navigation: Docs, Blog, Benchmarks, and About. You can move seamlessly between the reference documentation and the blog without losing your place.

## Coming up

The next few posts will cover:

1. How moonbuggy's coverage-guided test selection actually works — and why "only run the tests that exercise the mutant" is harder than it sounds.
2. A walkthrough of the first OSS hunt findings: what survived, what didn't, and what we learned.
3. The output contract: why every line in moonbuggy's output is grep-able by design.
