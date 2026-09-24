# make setup        once: a local virtualenv with the one dependency
# make check        offline tests: spec rendering, grader contract, examples file (no API key needed)
# make item-tests   grade every item's known-pass/known-fail examples (needs a grader key)
# make preflight    check the spec's dates, structure, pinned lines and drafts (no API key needed)
# make generate     step 1, write the conversation (needs GENERATOR=provider:model and its key)
.PHONY: setup check item-tests prompts preflight generate-prompts generate

PYTHON ?= .venv/bin/python
GRADER ?= deepseek:deepseek-flash
GENERATOR ?=

setup:
	@python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "needs Python 3.10 or newer: python3 is " + sys.version.split()[0])'
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt

check:
	@$(PYTHON) -m unittest discover -s tests -t . -q

item-tests:
	$(PYTHON) -m bcb.item_tests --grader $(GRADER)

prompts:
	$(PYTHON) -m bcb.item_tests --dry-run

preflight:
	$(PYTHON) -m bcb.checks --preflight

generate-prompts:
	$(PYTHON) -m bcb.generate --dry-run

generate:
	@if [ -z "$(GENERATOR)" ]; then echo "generate: set GENERATOR=provider:model (chosen at Meeting 2; never the grader's family)" >&2; exit 2; fi
	$(PYTHON) -m bcb.generate --generator $(GENERATOR) --grader $(GRADER)
