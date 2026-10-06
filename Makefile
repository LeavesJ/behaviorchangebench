# make setup        once: a local virtualenv with the one dependency
# make check        offline tests: spec rendering, grader contract, examples file (no API key needed)
# make item-tests   grade every item's known-pass/known-fail examples (needs a grader key)
# make preflight    check the spec's dates, structure, pinned lines and drafts (no API key needed)
# make generate     step 1, write the conversation (needs GENERATOR=provider:model and its key)
# make pilot-dry    check the hard-question branches and write every model input (no API key needed)
# make pilot-refs   grade each branch's own reference and near-miss replies (needs a grader key)
# make pilot        the hard-question pilot on the models under test (needs a grader key and an OpenRouter key)
# make draw         replies only, no grader, so people can label first (needs an OpenRouter key); grade them
#                   later with make pilot RESUME=<run dir>. SAMPLES, REASONING (on, off), OUT and BRANCHES (another
#                   branches file) also pass through. When grading a drawn run, GRADE_ONLY=1 stops before any call
#                   if the resume would draw a reply, and NORMALIZE=1 grades the text labelers saw.
.PHONY: setup check item-tests prompts preflight generate-prompts generate pilot-dry pilot-refs pilot draw

PYTHON ?= .venv/bin/python
# The grader is Opus 5.5 (decided Oct 2, provisional until the Oct 4 human labels); DeepSeek flash grades every
# run a second time: make pilot RESUME=<copy of the run dir> GRADER=deepseek:deepseek-flash. Opus refuses calls
# with reasoning off, so grader reasoning defaults on for it and off for any other grader.
GRADER ?= openrouter:anthropic/claude-opus-5.5
GRADER_REASONING ?= $(if $(findstring anthropic/claude-opus,$(GRADER)),on,off)
GENERATOR ?=
# Never a family the grader belongs to (README rule 5), so no Claude model is under test.
PILOT_MODELS ?= openrouter:openai/gpt-6-astra,openrouter:google/gemini-3.1-pro-preview,openrouter:x-ai/grok-4.7
CONVERSATION ?=
PILOT_ARGS = $(if $(CONVERSATION),--conversation $(CONVERSATION)) $(if $(ONLY),--only $(ONLY)) \
	$(if $(SAMPLES),--samples $(SAMPLES)) $(if $(REASONING),--reasoning $(REASONING)) $(if $(GRADER_REASONING),--grader-reasoning "$(GRADER_REASONING)") $(if $(OUT),--out $(OUT)) \
	$(if $(RESUME),--resume $(RESUME)) $(if $(BRANCHES),--branches $(BRANCHES)) $(if $(GRADE_ONLY),--grade-only) \
	$(if $(NORMALIZE),--normalize)

setup:
	@python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "needs Python 3.10 or newer: python3 is " + sys.version.split()[0])'
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt

check:
	@$(PYTHON) -m unittest discover -s tests -t . -q

item-tests:
	$(PYTHON) -m bcb.item_tests --grader $(GRADER) $(if $(ONLY),--only $(ONLY)) $(if $(GRADER_REASONING),--grader-reasoning "$(GRADER_REASONING)")

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

draw:
	$(PYTHON) -m bcb.pilot --models $(PILOT_MODELS) --no-grade $(PILOT_ARGS)
