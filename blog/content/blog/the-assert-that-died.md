+++
title = "The assert that died"
date = 2026-08-31
author = "moonbuggy"
tags = ["mutation-testing", "verdicts", "oss-findings"]
description = "KILLED is the weakest sentence in mutation testing, and it's the only one most tools say. Here's why that breaks down for humans and for agents."
+++

"KILLED" is the weakest sentence in mutation testing, and it's the only one most tools say.

Think about what it actually tells you. A mutant died. That's the whole message. Not which test killed it, not which assertion did the damage, not whether the death was meaningful or noise. If your codebase has a survivor, the tool shrugs and hands you a file name, and you go read the code yourself to find out whether the mutant deserved to live.

That manual step is not a small inconvenience. It is the reason teams quit mutation testing. The NSF-funded comparison of Python mutation tools names it outright: "One major obstacle is the manual effort required to review both incompetent and equivalent mutants" (Diallo et al., [An Analysis and Comparison of Mutation Testing Tools for Python](https://par.nsf.gov/servlets/purl/10573281)). The people who write field reports live it. Ned Batchelder ran mutmut and ended up hand-examining eight survivors, then settling a "philosophical decision" about exemption pragmas by himself, on a blog post, with no help from the tool ([mutmut](https://nedbatchelder.com/blog/201903/mutmut)). Eight survivors. One human. A prayer.

And the tools that could be doing that work are, almost without exception, holding out on us. The ACM comparison notes that MutMut "is the only one that hides killed mutants" and calls Cosmic Ray's kill matrix "in a primitive form" ([10.1145/3701625.3701659](https://dl.acm.org/doi/10.1145/3701625.3701659)). MutMut, per the NSF study, provides "no extra information about the killed mutants . . . which asserts made the KILLED." So the industry-standard answer to "why did this mutant die?" is: it died. Trust us.

That was already a bad deal for humans. It is worse now, because the human doing this triage works alongside agents that write more tests than ever and still does not trust a single one of them. More tests is not the problem; proven tests is. And a report that cannot say what a kill proves is a report that cannot restore trust.

The agent angle makes the same point from the other side. Atlassian's engineering blog on automating mutation coverage with AI says mutation reports are "dense . . . full of mutant jargon," and "LLMs handle this kind of structured-but-verbose input surprisingly well, if guided correctly" ([Automating mutation coverage with AI](https://www.atlassian.com/blog/development/automating-mutation-coverage-with-ai)). A report that records the reason behind every verdict is exactly the input an AI triage loop can chew on, and exactly what a human needs to believe a test suite again. A report that just says KILLED or SURVIVED leaves both guessing, the same way it has always left a human guessing, and the same way it leaves a human quitting the whole practice.

moonbuggy ships JSONL output with a fixed set of verdict keywords in the plaintext view (KILLED, KILLED_BY_ERROR, SURVIVED, NO_COVERAGE, TIMEOUT, SUSPICIOUS, SKIPPED; repo README). KILLED vs KILLED_BY_ERROR is already the seed of a real vocabulary: it says what the kill *proves*, not just that a death happened. The next step is a stable `killreason` vocabulary that every verdict maps to, published in the machine record and derivable from the human trace, so the two can never disagree.

Three rules keep that vocabulary honest, and they are hard rules, not design preferences.

- One reason identifier per verdict. No free text that could mean anything.
- SUSPICIOUS splits into two distinct causes, execution crash versus flaky probe, because those mean opposite things and must never collapse into one code.
- The identifier in the JSONL record is the same identifier a human can read in the trace. The machine line and the human line are the same statement, in two encodings.

What you get at the end is a report that does not make you or your agent re-litigate every mutant. A KILLED carries the assertion that died. A SURVIVED carries why it lived. The manual triage that the research names as the top adoption blocker stops being archaeology and becomes reading.

That, and only that, is the difference between a tool that reports deaths and a tool that reports evidence.

## Sources

All external sources pulled 2026-08-30.

- Diallo et al., *An Analysis and Comparison of Mutation Testing Tools for Python* (NSF): https://par.nsf.gov/servlets/purl/10573281
- ACM comparison of Python mutation tools: https://dl.acm.org/doi/10.1145/3701625.3701659
- Ned Batchelder, mutmut field report: https://nedbatchelder.com/blog/201903/mutmut
- Atlassian, Automating mutation coverage with AI: https://www.atlassian.com/blog/development/automating-mutation-coverage-with-ai
- moonbuggy README facts (JSONL output, 7 verdict keywords): verified against the moonbuggy repository README, 2026-08-30
- killreason invariants: moonbuggy project internal records, 2026-08-30