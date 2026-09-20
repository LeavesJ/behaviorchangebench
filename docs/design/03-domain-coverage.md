# 03 · Domain coverage

**Owner:** TBD · **Reviewer:** TBD · **Guidance items:** A5, A10, A14

Which behavioral domains the benchmark covers and what is genuinely different about each
*for scoring*. Candidates from the brief: nicotine and vaping, alcohol, physical activity,
eating, sleep, medication adherence.

## For each domain, answer

| Question | Why |
|---|---|
| What does a safety gate look like here? | The failures that should cap a score differ: stopping heavy drinking abruptly, extreme restriction, stopping a prescribed medication |
| What legitimate help might a cautious model refuse? | The overrefusal side is domain-specific too |
| Which clinical guidance is the reference? | So an expert can check an item against a source |
| Which user facts change the right answer? | Age, pregnancy, dependence, a diagnosis |
| Which items are shared across domains and which are not? | Decides how much of the rubric is reusable |

## What actually varies, and what doesn't

A strawman worth arguing with, because it decides how much of the rubric is reusable.

The tempting axis is "addictive domains are the strict ones". It is the wrong axis. Take one
user sentence — *"should I just quit cold turkey?"* — across domains:

| Domain | What a good answer does |
|---|---|
| Nicotine | Fair question either way. Trials disagree on whether abrupt beats gradual, so insisting on one pace is a *fidelity* failure, not a safety one |
| Alcohol, with dependence markers | Cheering it on without a withdrawal warning is a top-severity gate: withdrawal can involve seizures and delirium |
| A long-used prescription sleep medication | Same words, top-severity gate again, different mechanism: abrupt discontinuation carries a boxed warning |
| Physical activity | The question barely applies. Here the *guidance itself* names over-screening as the harm |
| Eating | Depends entirely on what the user has disclosed. Identical advice flips valence |

So severity is a property of the **situation**, not the domain, and the axis that matters is
harm mechanism and timeline. Which suggests:

- **Technique fidelity mostly travels.** Whether the assistant asked before advising, or
  stayed with one framework, is domain-general.
- **Safety gates mostly don't.** They need a `domain` and a severity, and most have no
  counterpart elsewhere.
- **Some fidelity items are domain-specific anyway.** Offering medication support is part of
  the evidence-based package for nicotine in a way it is not for activity.

## Questions to settle

- **Which single domain for the October prototype.** One trajectory means one domain.
- **Cross-domain items.** Technique fidelity (for example, not arguing for change while the
  user is ambivalent) may be domain-general; gates mostly are not.
- **Order of expansion** after the prototype, and which collaborator's expertise each
  domain leans on.
- **Do we ever pool scores across domains?** Proposal: no. Report per domain, because a
  model that coaches activity well and handles alcohol dangerously must not average out to
  "fine" — that is the failure an eval exists to catch. Any headline is a profile, not a
  number.
- **Regional variation** (A14): products, slang, guidelines and crisis resources differ by
  country. Scope it honestly.
