# make setup        once: a local virtualenv with the one dependency
# make check        offline tests: spec rendering, grader contract, examples file (no API key needed)
# make item-tests   grade every item's known-pass/known-fail examples (needs a grader key)
# make preflight    check the spec's dates, structure, pinned lines and drafts (no API key needed)
# make generate     step 1, write the conversation (needs GENERATOR=provider:model and its key)
# make pilot-dry    check the hard-question branches and write every model input (no API key needed)
# make pilot-refs   grade each branch's own reference and near-miss replies (needs a grader key)
# make pilot        the hard-question pilot on the models under test (needs a grader key and an OpenRouter key)
.PHONY: setup check item-tests prompts preflight generate-prompts generate pilot-dry pilot-refs pilot

PYTHON ?= .venv/bin/python
GRADER ?= deepseek:deepseek-flash
GENERATOR ?=
PILOT_MODELS ?= openrouter:anthropic/claude-opus-5.5,openrouter:openai/gpt-6-astra,openrouter:google/gemini-3.1-pro-preview
CONVERSATION ?=
PILOT_ARGS = $(if $(CONVERSATION),--conversation $(CONVERSATION)) $(if $(ONLY),--only $(ONLY))

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

pilot-dry:
	$(PYTHON) -m bcb.pilot --dry-run $(PILOT_ARGS)

pilot-refs:
	$(PYTHON) -m bcb.pilot --references --grader $(GRADER) $(PILOT_ARGS)

pilot:
	$(PYTHON) -m bcb.pilot --models $(PILOT_MODELS) --grader $(GRADER) $(PILOT_ARGS)
