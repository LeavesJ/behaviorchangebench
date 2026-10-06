# BehaviorChangeBench

An evaluation of how general-purpose AI assistants handle conversations in which someone
asks for help changing a health behavior — quitting nicotine, drinking less, moving more,
eating or sleeping differently, taking medication as prescribed — across several turns and
several sessions. Scoring is pass/fail against rubric items derived from established
behavior science (Motivational Interviewing and MITI, the Behavior Change Technique
Taxonomy, COM-B, CBT), graded by LLM judges that will be validated against expert human raters.

**Status: pre-work.** Nothing here is final. Everything marked *strawman* exists to be
argued with.

## Milestones

| Date | What |
|---|---|
| Oct 8, 2026 | End-to-end prototype as a plain Python pipeline (Harbor deferred, see `docs/decisions.md`): one multi-session conversation, 2–3 rubrics as graders, several frontier models, per-item results, and every case where the automated grader disagreed with our manual verdict |
| Oct 12, 2026 | Design document (`docs/design/`) plus the prototype, sent to the PI as this repository |

## Layout

```
docs/
  anthropic-conformance.md   the guidance every design decision answers to
  architecture-proposal.md   how the pipeline could fit together (proposal)
  design/                    six sections, one owner each
  meetings/                  agendas and notes
  reports/                   short reports on each run
rubrics/                     rubric items as data files        (to come)
trajectories/                conversation specs, and each item's known-pass/known-fail examples
runs/                        run output: grader verdicts, manifests, summaries
bcb/                         the Python pipeline (so far: rendering, grader, item tests)
tests/                       offline tests; `make check` runs them, no API key needed
```

See `docs/design/README.md` for how sections are written and `CONTRIBUTING.md` for how we work.

## Running the item tests

Rule 4 below, as a command. Every rubric item has a known-pass and a known-fail reply in
`trajectories/vaping-v0/item-examples.yaml`; the grader has to get all of an item's examples
right before the item is used.

Needs Python 3.10 or newer.

```bash
make setup                      # once
make check                      # offline tests
make prompts                    # see exactly what the grader will be sent, no API call
make item-tests                 # needs OPENROUTER_API_KEY in your environment or in .env (Opus grades, about $1.50)
make item-tests GRADER=deepseek:deepseek-flash   # the second grader, needs DEEPSEEK_API_KEY (a few cents)
```

`make item-tests` exits with an error whenever any item is unusable; that is the result, not a
crash. Results land in `runs/item-tests/`, with the grader's model ID, the prompt, spec and
examples versions, and the date. Until the conversation is generated, the earlier assistant
turns the grader sees are marked placeholders, and the summary says so.

## Generating the conversation

Step 1 of the pipeline: a generator model writes the turns the spec does not pin, one chat at
a time, and a script checks the draft before anyone freezes it. Meeting 2 picks the generator;
it is never from the grader's family.

```bash
make preflight                  # check the spec itself: dates, pinned lines, drafts, no API call
make generate-prompts           # see exactly what the generator will be sent, no API call
make generate GENERATOR=openrouter:<vendor>/<model>   # needs that provider's key
```

Each run lands in `runs/generate/` with the draft conversation, a record of the model, prompt
and spec versions and every raw output, and `checks.md`: errors to fix, flags to read, and a
checklist for the human read. Freezing a reviewed draft is done by hand (see `bcb/generate.py`).
Once it is frozen, `python -m bcb.item_tests --conversation <file>` reruns the item tests on the
real conversation instead of placeholders.

## Ground rules for the eval itself

1. Every design decision names the row of `docs/anthropic-conformance.md` it satisfies.
2. Safety gates and technique fidelity are scored separately. Gates cap; fidelity does not
   average them away.
3. Both failure directions count: harmful compliance and overrefusal.
4. A rubric item is not used until its judge gets the item's own known-pass and known-fail
   examples right.
5. The judge model's family is never the family of the model being judged.
6. Results carry the model ID, prompt and rubric versions, and date that produced them.
