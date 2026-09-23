# Read first

Twenty minutes before Sunday. If you only have five, read part 1 and the checklist.

## 1. What we are building

When someone asks an AI assistant for help changing a health behavior — quitting vaping,
drinking less, moving more, eating or sleeping differently, taking medication as prescribed
— does the assistant do what behavior science says works? Nobody has measured it.

Anthropic found health and wellness is 27% of the conversations where people ask Claude for
personal guidance. There are evals for mental health and for medical facts. There is none
for behavior change, even though the field has decades of evidence about what a good
counselor does.

So: **BehaviorChangeBench**. A set of pass/fail rubric items drawn from established behavior
science (Motivational Interviewing and its MITI coding manual, the Behavior Change Technique
Taxonomy, COM-B, CBT), applied to multi-session conversations, scored by LLM judges whose
verdicts are checked against expert human raters. Behavior scientists will eventually write
the items; our job is the machinery, and a way for them to write items quickly.

## 2. Three ideas everything else hangs on

**Safety gates versus technique fidelity.** Two different kinds of rubric item. A gate is
binary and about harm: it caps the score no matter how good the rest was. A fidelity item is
about whether the assistant used the right technique at the right moment. Keeping them apart
stops good technique from averaging away a harmful turn.

**Two ways to fail, not one.** Harmful compliance is helping with something harmful.
Overrefusal is refusing, hedging or lecturing when the person deserved real help. An eval
that measures only the first rewards a model that refuses everything. We need paired cases.

**A judge nobody checked is not evidence.** Our graders are LLMs. They have to be validated
against human labels, with the agreement reported, and the judge should not be from the same
model family as the model it grades.

Everything in `docs/design/` is one of these questions in more detail.

## 3. What is owed

| When | What |
|---|---|
| Mon Sep 21, 12:30–1:00 | First call with our co-mentor |
| Thu Oct 8 | Working prototype as a plain Python pipeline (Harbor deferred, see [`docs/decisions.md`](decisions.md)): one multi-session conversation, 2–3 rubrics as graders, several frontier models, per-item results, and every case where the automated grader disagreed with our manual verdict |
| Mon Oct 12 | Design document plus the prototype, sent to our PI as a GitHub repo |

Our PI's standard, in his words: "careful thought (rather than careless volume) for each
subcomponent of the eval."

## 4. Before Sunday

- [ ] **Read Anthropic's two pages on what makes a wellbeing eval rigorous** (`anthropic-wellbeing-evals.pdf` in this bundle, or the link in the PI's Sep 11 email). Every design decision we make has to point at something in it. It is genuinely two pages.
- [ ] **Bring your take-home in one line**: which framework, which technique, what a fail looked like. They are the raw material for the shared rubric template, so say whether you are happy to share yours with the group.
- [ ] **Fill the 3-minute form** (link in the group chat): interests, hours, GitHub username, and availability for the week of Oct 12. It is what lets us split the work in the meeting instead of spending the hour on it, and the last page is what our PI is waiting on — he asked for five one-hour slots that week and needs all five of us on it.
- [ ] **Skim** [`docs/meetings/2026-09-20-kickoff.md`](meetings/2026-09-20-kickoff.md) so the hour goes fast. Think about which of the pieces in the role grid you want.

If you have another half hour: skim [`docs/architecture-proposal.md`](architecture-proposal.md).
It was written for a Harbor build, which is now deferred, but its pipeline stages and design
rules still apply.

## 5. What this repo is

| | |
|---|---|
| `docs/design/` | Six sections, one owner each. Currently stubs with the questions to settle |
| `docs/anthropic-conformance.md` | The checklist every decision answers to |
| `docs/architecture-proposal.md` | A proposal for the pipeline. Argue with it |
| `docs/meetings/` | Agendas and notes |
| `CONTRIBUTING.md` | How we work: branches, review, citations, secrets |

Nothing here is decided. Everything is a strawman, including the dates.
