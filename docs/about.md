# About moonbuggy

moonbuggy is a fast, agent-first mutation testing tool for Python. It was
built by [Jennifer Hamon](https://github.com/jhamon) with a specific
philosophy: output should be machine-readable *and* human-readable, with
zero ambiguity.

## Why "moonbuggy"

The name is a nod to the Apollo Lunar Roving Vehicle — the "moon buggy."
Mutation testing finds the gaps in your test suite, and the metaphor of
exploring terrain that looks fine from a distance but has hazards up close
felt right.

## Design principles

**Refuse rather than guess.** A wrong status is worse than no status. A
flaky test makes its mutants `SUSPICIOUS` rather than confidently anything.
An already-failing suite is refused outright.

**One line per mutant.** Output is a greppable stream of status-keyword lines
with `key=value` tokens, backed by the same data as JSONL for anything that
wants to parse rather than scan.

**Only run the tests that could possibly notice.** One instrumented run of
your suite builds a coverage map. Each mutant then runs against a handful of
tests instead of all of them.

**Built for agents.** The help text is the best explanation of the tool.
Run `uv run --with moonbuggy moonbuggy -h`, show it to an agent, and let it
drive — no install, no setup, no configuration file.

## How this was built

moonbuggy was built by Jennifer Hamon as a project to explore different
models and ways of working with AI agents. It was created using a
variety of models and agent harnesses, including Anthropic's Claude
models and open-weight models through OpenRouter.

## License

moonbuggy is [MIT licensed](https://github.com/jhamon/moonbuggy/blob/main/LICENSE).

## Repository

[https://github.com/jhamon/moonbuggy](https://github.com/jhamon/moonbuggy)