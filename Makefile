# Evaluator-facing commands. See docs/acceptance-criteria.md -- the Verification
# section names the commands an evaluator runs to check the criteria.
#
# Targets for criteria not yet implemented are deliberately absent rather than
# stubbed, so `make` never reports success for something that does not exist.

PYTHON ?= .venv/bin/python

.PHONY: test check-oracle

## Default suite. Fast; excludes the per-mutant subprocess tests.
test:
	$(PYTHON) -m pytest

## Criteria A2a/A2b/A4: the correctness gate.
## Runs every generated mutant against the full fixture suite under plain
## pytest, and checks each result against the hand-written oracle labels.
check-oracle:
	$(PYTHON) -m pytest -m slow -v
