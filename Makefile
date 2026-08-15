# Evaluator-facing commands. See docs/acceptance-criteria.md -- the Verification
# section names the commands an evaluator runs to check the criteria.
#
# Targets for criteria not yet implemented are deliberately absent rather than
# stubbed, so `make` never reports success for something that does not exist.

PYTHON ?= .venv/bin/python

.PHONY: test check-oracle check-spike bench bench-coverage check-fresh-install check-all

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

## Criteria H1/H2: clean install, then bare `moonbuggy` on an unseen project.
check-fresh-install:
	./scripts/check_fresh_install.sh

check-all: test check-oracle check-spike check-fresh-install

## Criterion B3: coverage mechanism benchmark.
## Prints wall-clock and map content for each candidate. See docs/spike-b-findings.md.
bench-coverage:
	$(PYTHON) scripts/bench_coverage.py
