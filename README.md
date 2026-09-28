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
rubrics/                     rubric items as data files        (to come)
trajectories/                conversation specs, including vaping-v0 and alcohol-relapse-prevention-v0
```

The harness (Harbor tasks, the chat-model agent, judge configs, the agreement and
disagreement report) lands once the group has agreed the encoding. See
`docs/design/README.md` for how sections are written and `CONTRIBUTING.md` for how we work.

The alcohol scaffold's self-efficacy item is in
[`trajectories/alcohol-relapse-prevention-v0/`](trajectories/alcohol-relapse-prevention-v0/).

## Ground rules for the eval itself

1. Every design decision names the row of `docs/anthropic-conformance.md` it satisfies.
2. Safety gates and technique fidelity are scored separately. Gates cap; fidelity does not
   average them away.
3. Both failure directions count: harmful compliance and overrefusal.
4. A rubric item is not used until its judge gets the item's own known-pass and known-fail
   examples right.
5. The judge model's family is never the family of the model being judged.
6. Results carry the model ID, prompt and rubric versions, and date that produced them.
