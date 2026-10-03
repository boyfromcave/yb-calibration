# ybcal — common tasks. `make` lists them.
PY      ?= .venv/bin/python
YBCAL   := PYTHONPATH=src $(PY) -m ybcal.cli
YCASH6  ?= $(YBCAL_YCASH6)
BUDGET  ?= quick
OUT     ?=

.DEFAULT_GOAL := help
.PHONY: help setup test test-all lint verify params-check quick recommend docs docs-check devnet-build devnet-validate clean

help:  ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

setup:  ## create .venv and install ybcal with dev extras
	test -x $(PY) || python3.11 -m venv .venv
	$(PY) -m pip install -e '.[dev]'

test:  ## pytest (without devnet tests)
	PYTHONPATH=src $(PY) -m pytest -q -m "not devnet"

test-all:  ## pytest including devnet / slow tests
	PYTHONPATH=src $(PY) -m pytest -q

lint:  ## ruff
	$(PY) -m ruff check src tests

verify:  ## kernel parity vs reference model + C++ worked examples (WP-1)
	$(YBCAL) verify

params-check:  ## drift vs registry + invariants (YCASH6=path to read live source)
	$(YBCAL) params check $(if $(YCASH6),--ycash6 $(YCASH6))

quick:  ## recommend --budget quick --synthetic (smoke run, <= 10 min on 4 cores; OUT=dir)
	$(YBCAL) recommend --budget quick --synthetic $(if $(OUT),--out $(OUT))

recommend:  ## full recommendation run (BUDGET=quick|standard|deep, OUT=dir)
	$(YBCAL) recommend --budget $(BUDGET) $(if $(OUT),--out $(OUT))

docs:  ## regenerate docs/parameters.md from the registry
	$(YBCAL) params doc --out docs/parameters.md

docs-check:  ## fail if docs/parameters.md is stale
	$(YBCAL) params doc --out docs/parameters.md --check

devnet-build:  ## build ycashd in a throwaway worktree (WP-9)
	$(YBCAL) devnet build $(if $(YCASH6),--ycash6 $(YCASH6))

devnet-validate:  ## simulator vs node differential suite (WP-9)
	$(YBCAL) devnet validate

clean:  ## remove caches (never touches data/local or reports)
	rm -rf .pytest_cache .ruff_cache .hypothesis build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -not -path './.venv/*' -exec rm -rf {} +
