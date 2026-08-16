# Evaluator-facing commands. See docs/acceptance-criteria.md -- the Verification
# section names the commands an evaluator runs to check the criteria.
#
# Targets for criteria not yet implemented are deliberately absent rather than
# stubbed, so `make` never reports success for something that does not exist.

PYTHON ?= .venv/bin/python

.PHONY: test check-oracle check-spike check-mutmut check-robustness check-properties bench bench-coverage profile ab docs docs-test docs-linkcheck docstring-coverage lint format-check typecheck oss-hunt check-differential check-fresh-install check-all

## Default suite. Fast; excludes the subprocess-per-mutant tests.
test:
	$(PYTHON) -m pytest

## Criteria A2a/A2b/A4: the correctness gate.
## Runs every generated mutant against the full fixture suite under plain
## pytest, and checks each result against the hand-written oracle labels.
check-oracle:
	$(PYTHON) -m pytest -m slow tests/test_naive_oracle.py -v

## Criteria B1/B2: the Phase 0 spike gate.
## In-memory mutation coexisting with pytest's assert rewriting, reaching xdist
## workers, with the negative test that proves the xdist check has teeth.
check-spike:
	$(PYTHON) -m pytest -m slow tests/test_spike_inmemory.py -v

## Criteria G1-G4: the comparative benchmark.
## moonbuggy vs mutmut vs the naive baseline. See docs/benchmark-results.md.
bench:
	$(PYTHON) scripts/bench_mutation.py

## Milestone M3.1: build the documentation.
## -W turns warnings into errors, so a broken cross-reference fails the build
## rather than being noticed by nobody. Nothing is published anywhere (M3.1.5).
docs: docstring-coverage
	$(PYTHON) -m sphinx -b html -W --keep-going docs docs/_build/html
	@echo "docs -> docs/_build/html/index.html"

## Milestone M3.2.1/M3.2.2: docstring coverage and style, as a gate.
## Runs as part of `make docs`, so a new public function without a docstring
## fails the build.
docstring-coverage:
	$(PYTHON) -m interrogate -c pyproject.toml src/moonbuggy
	$(dir $(PYTHON))pydoclint --style=google --config=pyproject.toml src/moonbuggy

## Milestone M5.1: the lint gate.
## Config and the reason for every disabled rule live in pyproject.toml.
lint:
	$(dir $(PYTHON))ruff check .

## Milestone M5.2: the formatting gate. Checks only; `ruff format` reformats.
format-check:
	$(dir $(PYTHON))ruff format --check .

## Milestone M5.3: the type gate. Strict, over src/moonbuggy only.
## The override list in pyproject.toml is the remaining un-annotated work.
typecheck:
	$(PYTHON) -m mypy

## Milestone M3.3.10: every code example in the docs is executed.
docs-test:
	$(PYTHON) -m sphinx -b doctest -W docs docs/_build/doctest

## Milestone M3.1.3: no broken internal links.
docs-linkcheck:
	$(PYTHON) -m sphinx -b linkcheck -W docs docs/_build/linkcheck

## Milestone M4: run against five pinned open-source libraries.
## Clones read-only, builds an isolated venv each, refuses any target whose own
## suite is not green. Nothing is ever posted anywhere.
oss-hunt:
	$(PYTHON) scripts/oss_hunt.py

## Milestone M1.3: per-mutant differential against mutmut, over many projects.
## Fails if any disagreement is unclassified.
check-differential:
	$(PYTHON) scripts/differential.py

## Milestone M2.1: where the wall clock actually goes.
## Three workload shapes, five runs each, phases that must cover 95% of the
## total. Take this BEFORE attempting any optimisation -- see
## docs/perf-hypotheses.md for why that rule exists.
profile:
	$(PYTHON) scripts/profile_run.py

## Milestone M2.3: A/B two git refs with a significance test.
## Declares a winner only when the 95% intervals do not overlap.
## Usage: make ab BASELINE=<ref> CANDIDATE=<ref> [SHAPE=slow-tests] [RUNS=7]
ab:
	@test -n "$(BASELINE)" || { echo "usage: make ab BASELINE=<ref> CANDIDATE=<ref>"; exit 2; }
	@test -n "$(CANDIDATE)" || { echo "usage: make ab BASELINE=<ref> CANDIDATE=<ref>"; exit 2; }
	$(PYTHON) scripts/ab_compare.py --baseline $(BASELINE) --candidate $(CANDIDATE) \
		$(if $(SHAPE),--shape $(SHAPE),) $(if $(RUNS),--runs $(RUNS),)

## Milestone M1.2: property-based testing.
## Seven invariants over generated modules, 500 examples each. Runs about two
## minutes; the regression examples for every bug it has found are attached to
## the properties themselves.
check-properties:
	$(PYTHON) -m pytest -m slow tests/test_properties.py -v

## Milestone M1.4: hostile inputs.
## One test per row of the M1.4 table -- syntax errors, flaky tests, red
## baselines, threads, self-exiting tests, odd encodings, crash recovery.
check-robustness:
	$(PYTHON) -m pytest -m slow tests/test_robustness.py -v

## Criteria H1/H2: clean install, then bare `moonbuggy` on an unseen project.
check-fresh-install:
	./scripts/check_fresh_install.sh

## Criterion A5: advisory cross-check of the oracle against mutmut.
## Never gates; mutmut is never authoritative.
check-mutmut:
	$(PYTHON) scripts/check_mutmut_differential.py

check-all: test check-oracle check-spike check-properties check-robustness check-mutmut check-fresh-install

## Criterion B3: coverage mechanism benchmark.
## Prints wall-clock and map content for each candidate. See docs/spike-b-findings.md.
bench-coverage:
	$(PYTHON) scripts/bench_coverage.py
