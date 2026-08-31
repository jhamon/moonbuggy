# Content guidelines

This page is the public-facing home of the rules that govern everything we
write in this repository and on the blog. It mirrors the team's internal
brand guidelines and is the source of truth for anything a reader outside the
team can see. When the two disagree, this page wins, because it is what ships.

The whole point, in one line: **moonbuggy reports evidence, not deaths.** The
content should carry the same DNA as the tool: verified claims, labeled
speculation, no hand-waving.

## Hard rule: no internal references in user-facing content

**No internal agent names, ticket identifiers, task IDs, milestone labels, or
any artifact identifier appear in any user-facing content.**

Concretely, this means:

- No team handles, room names, or person/bot references in blog posts, docs,
  the README, or release notes. Cite anonymous sources instead, e.g.
  "moonbuggy project internal records &lt;date&gt;".
- No ticket or task IDs (task `t_<hex>`, milestone `M3.2.1`, `G4`, `H13`), no
  milestone labels (`milestone M1.3`), no internal dev-record paths
  (`docs/development/perf-hypotheses.md`) in any page a reader can reach.
- Internal planning documents (`docs/development`, `docs/contracts`,
  `docs/competitive-intel.md`) stay out of the build and out of any public
  link. They live in the tree for the team, not for users.

A member of the public must be able to read any page without tripping over an
identifier that means something only to us. If a reader would have to ask
"what is this?", it does not ship.

## Voice

A senior engineer explaining something they genuinely care about. Calm,
precise, a little wry. Authoritative, not aggressive. Never boastful, never
salesy, never AI-generated.

- Say what you know plainly; when you don't know, say so. False modesty is as
  bad as boasting.
- Ground every piece in the work: a real survivor triaged by hand, a benchmark
  you ran, a gap you found in another tool.
- Be specific. "Eight survivors, one human, a prayer" lands; "manual review is
  a pain point" does not.
- Say what moonbuggy does not do. An honest limit builds more trust than any
  slogan.
- Have opinions where the evidence supports them.

## Anti-slop checklist

Run this on every draft before it ships. Fix anything that triggers.

- No AI-tell words: delve, underscore, tapestry, landscape (abstract), pivotal,
  crucial, vibrant, testament, "it's not just X, it's Y", "let's dive in",
  "at the end of the day", "game-changer", "industry-leading", "robust
  solution".
- No rule-of-three padding, no synonym cycling, no false ranges ("from X to
  Y" where X and Y aren't on a scale).
- No excessive hedging ("could potentially perhaps"). State the claim and cite
  it, or label it speculation.
- No em-dash overuse. If a paragraph has more than one em dash, that's a
  rewrite signal. No emoji in body text or headings. No boldface decoration.
- Headers in sentence case (`##`, `###`); no `<h1>` in the body.
- No question asked and answered in the same breath. No mic-drop endings, no
  reassurance kickers. Make the point and stop.

## Sourcing

- Every number traces to a source: the metrics dashboard, the benchmark
  harness, the repo README, or a named external citation with a pull date.
- Competitor numbers stay labeled self-reported until we verify them on our
  own harness.
- Speculation is labeled "speculation", never whispered as fact.
- The 38x / 1.9x performance claims only appear in public content after they
  are re-verified on the benchmark harness by the team's performance engineer.
  Unverified numbers stay out.

## Code blocks and machine records

- Show real output. Verdicts, JSONL lines, and traces are quoted verbatim from
  real runs, never prettified.
- The machine record is the strongest demo the tool has. Case studies quote it
  verbatim and name which run and commit it came from.
- Keep code fragments small enough to read; link the rest.

---

If a sentence in this document sounds like marketing, it fails its own test.
That is a bug in this document, and it should be raised on the review thread
so the rule that produced it gets patched.