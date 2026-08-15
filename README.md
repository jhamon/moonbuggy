# moonbuggy

Fast, agent-first mutation testing for Python.

**Status: pre-alpha, under construction.** Nothing here is usable yet.

Mutation testing measures whether your tests would actually notice if the code
broke. It makes small changes to your source — flipping a `<` to a `<=`, a
`True` to a `False` — and reruns the tests. A change no test objects to is a
gap: either a missing test, a weak assertion, or a line nothing exercises.

Two things make moonbuggy different from existing Python tools:

- **Speed.** Mutation testing multiplies your suite's runtime by the number of
  mutants, so it is usually too slow to run often. moonbuggy runs only the tests
  that actually cover each mutated line, applies mutations in memory rather than
  writing files to disk, and caches results across runs.
- **Output built for agents.** Results are JSON Lines, with a derived plaintext
  view whose every line starts with a fixed keyword, so `grep SURVIVED` works
  with no knowledge of the schema.

## Requirements

Python 3.12+ (moonbuggy uses `sys.monitoring`, added in 3.12) and pytest.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

Run moonbuggy's own tests:

```bash
.venv/bin/python -m pytest
```

The project under `tests/fixtures/` is deliberately excluded from that run. It
is input data — a small pytest project with known mutation outcomes, used to
verify moonbuggy against a ground truth. Some of its tests hang or fail by
design once mutated.

## What "done" means

[docs/acceptance-criteria.md](docs/acceptance-criteria.md) defines completion as
a checklist an evaluator can run, rather than a judgement call.
