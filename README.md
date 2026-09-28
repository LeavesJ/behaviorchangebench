# BehaviorChangeBench

An evaluation of how general-purpose AI assistants handle conversations in which someone
asks for help changing a health behavior — quitting nicotine, drinking less, moving more,
eating or sleeping differently, taking medication as prescribed — across several turns and
several sessions. Scoring is pass/fail against rubric items derived from established
behavior science (Motivational Interviewing and MITI, the Behavior Change Technique
Taxonomy, COM-B, CBT), graded by LLM judges that are validated against expert human raters.

**Status: pre-work.** Nothing here is final. Everything marked *strawman* exists to be
argued with.

## Milestones

| Date | What |
|---|---|
| Oct 8, 2026 | End-to-end prototype in [Harbor](https://www.harborframework.com/): one multi-session conversation, 2–3 rubrics as graders, several frontier models, per-item results, and every case where the automated grader disagreed with our manual verdict |
| Oct 12, 2026 | Design document (`docs/design/`) plus the prototype, sent to the PI as this repository |

## Layout

```
docs/
  anthropic-conformance.md   the guidance every design decision answers to
  architecture-proposal.md   how the pipeline could fit together (proposal)
  design/                    six sections, one owner each
  meetings/                  agendas and notes
trajectories/<id>/           conversation spec, plan, conversation file, item examples
src/bcb/                     the pipeline: plain Python, steps 1-4 of the vaping-v0 plan
prompts/                     generator and grader prompts (hashed into every result)
configs/                     one run config per run; run.smoke.yaml is offline and mock-only
tests/                       pytest; the smoke pipeline runs end to end with no API keys
runs/                        pipeline output (gitignored)
```

See `docs/design/README.md` for how sections are written and `CONTRIBUTING.md` for how we work.

## Running the pipeline

The Oct 8 build is a plain Python pipeline (`trajectories/vaping-v0/details.md` section 9);
Harbor is deferred. Python 3.11+. Code runs from `src/` and is never installed.

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
export PYTHONPATH=src
python -m pytest                                  # includes a full mock run of every step

C=configs/run.vaping-v0.yaml                      # or configs/run.smoke.yaml, no keys needed
python -m bcb draft   --config $C                 # 1  generator writes conversation.draft.yaml
cp trajectories/vaping-v0/conversation.draft.yaml trajectories/vaping-v0/conversation.v1.0.yaml
python -m bcb check   --config $C                 #    every scriptable constraint; edit until clean
python -m bcb freeze  --config $C --draft trajectories/vaping-v0/conversation.draft.yaml --realism-reviewer NAME
python -m bcb replies --config $C                 # 2  models x checkpoints x samples; resumable
python -m bcb packets --config $C                 #    blind labeling packets; the key goes to sealed/
python -m bcb itemtest --config $C --grader primary   # rule 4: examples graded before real grading
python -m bcb grade   --config $C --grader primary    # 3  one call per (reply, item)
python -m bcb tabulate --config $C                # 4  tables/, disagreements, run_manifest.json
```

A step that needs a choice still marked `TK` in the config refuses to run and names it. With the
smoke config, skip `cp` and `freeze` (its conversation is the draft itself), and pass
`--allow-untested-items` to `grade`, since no item examples are written yet.

## Ground rules for the eval itself

1. Every design decision names the row of `docs/anthropic-conformance.md` it satisfies.
2. Safety gates and technique fidelity are scored separately. Gates cap; fidelity does not
   average them away.
3. Both failure directions count: harmful compliance and overrefusal.
4. A rubric item is not used until its judge gets the item's own known-pass and known-fail
   examples right.
5. The judge model's family is never the family of the model being judged.
6. Results carry the model ID, prompt and rubric versions, and date that produced them.
