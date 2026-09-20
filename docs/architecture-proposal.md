# Architecture proposal

**Status: proposal, for the group and our mentors to argue with. Nothing here is decided.**
Harbor details are from its docs and source at v0.23 (Sept 2026), read but not yet run. The
project moves fast; smoke-test every feature named here on day one and pin versions.

## 1. The loop

```
 humans edit            compile              run                    grade                 report
┌──────────────┐    ┌─────────────┐    ┌───────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│ trajectory   │    │ Harbor      │    │ models × k    │    │ one judge call   │    │ per-item table      │
│ spec  (YAML) │───▶│ tasks       │───▶│ attempts, via │───▶│ per rubric item, │───▶│ agreement vs manual │
│ rubric items │    │ (generated, │    │ a plain chat  │    │ judge family ≠   │    │ disagreement table  │
│       (YAML) │    │ never hand- │    │ agent         │    │ model family     │    │ run manifest        │
│ labels (CSV) │    │ edited)     │    │               │    │                  │    │                     │
└──────▲───────┘    └─────────────┘    └───────────────┘    └──────────────────┘    └──────────┬──────────┘
       └────────────────────── disagreements revise the rubric items ◀────────────────────────┘
```

The eval is the loop, not the dataset. "Expect the rubrics to change substantially" is cheap
if changing an item means editing one data file and rerunning the grader, and expensive if
it means editing task folders by hand.

## 2. What humans edit

Three kinds of file. Experts and teammates never touch Harbor TOML or Python.

- **Trajectory spec** — persona (what the user will say freely, what they hold back, and
  what it takes to draw it out), the sessions with the time gap and what changed between
  them, the user turns, which turns are *pinned* (verbatim for every model, because a rubric
  item hangs on them), and the checkpoints where items are live.
- **Rubric items** — one file per item, fields as in
  [`design/02-rubric-structure.md`](design/02-rubric-structure.md), including known-pass and
  known-fail examples.
- **Labels** — our manual verdicts: trial, item, turn, verdict, labeler, note. Labeled blind,
  before anyone looks at the judge's output.

## 3. One spec, two task shapes

| | **Checkpoints** (ships first) | **Live sessions** (second) |
|---|---|---|
| What the model gets | The exact conversation so far, up to a user turn | Session 1's first user turn; it then lives with its own replies |
| What it produces | The next assistant turn | Every assistant turn, session after session |
| Harbor shape | One small task per checkpoint, in a local dataset | One multi-step task, a step per session |
| Good for | Identical input for every model; clean comparison; easy to label; the unit for judge-vs-human agreement; scales toward 100+ items | Compounding effects: drift, theory consistency, whether an early mistake changes what the user later discloses |
| Blind to | Anything that depends on the model's own earlier turns. The prior assistant turns are ours, and may prime style | With scripted user turns, the user cannot react to what the model actually said |

Checkpoints alone meet the Oct 8 brief. Live sessions are the more interesting result. The
same agent and the same graders serve both: a checkpoint is a live run with zero turns left.

## 4. The model under test

Every built-in Harbor agent is a coding or computer-use agent. Routing a chat model through
one ("write your reply to a file") tests the coding agent's system prompt, not the chat
model, which is what guidance item A6 warns against. So: a small custom agent that sends
messages to the model through its API with no tools, and writes the transcript.

Two conditions must be declared, because they are ours to invent and they change results:

- **System prompt:** none, or one short stated prompt. Report sensitivity to it (C5).
- **Memory across sessions:** full history, a summary, or nothing. Consumer assistants differ
  here; pick one as primary and say why.

## 5. Grading

- One judge call per rubric item. The judge gives its reasoning and a quoted evidence span,
  then a verdict, parsed strictly. Aggregation happens in code, never in the judge.
- Safety gates and technique fidelity are separate reward keys. A failed gate caps the
  trajectory; the cap is applied in analysis code where it can be seen.
- The judge's family differs from the family under test. Harbor can regrade recorded
  outputs with a second judge at no new generation cost; the difference between judges is a
  result we report.
- **A judge timeout or parse failure is not a model failure.** Harbor's defaults turn both
  into zeros. The report must read error fields first and never count them as fails.
- Item tests run first: each item's known-pass and known-fail examples. An item whose judge
  gets its own examples wrong is not used.

## 6. Reports

1. **Per-item table** — model × item: pass rate over k attempts with an interval, and how
   often the item applied at all. Broken out by domain, never pooled across them: a model
   that coaches one domain well and handles another dangerously must not average to "fine".
2. **Agreement** — judge vs our manual verdicts, per item; human vs human as the ceiling.
3. **Disagreement table** — format in [`design/05-grader-validation.md`](design/05-grader-validation.md).
4. **Run manifest** — model IDs, judge ID, prompt hashes, rubric versions, Harbor version,
   date, cost per model.

## 7. What this does not solve

- Everything runs through an API, not a consumer app. For the one prototype conversation we
  can replay the pinned user turns by hand in the consumer apps, grade with the same items,
  and report the difference.
- One trajectory. It demonstrates the pipeline and nothing about models in general.
- The user is simulated or scripted, and we say how that differs from a real person.
- For now the "expert raters" are us.

## 8. Questions for our mentors

1. Is a custom chat-model agent acceptable, given the above? Why Harbor in particular:
   reviewer familiarity, reuse as an RL environment, or a lab standard?
2. "One simulated multi-session trajectory": a fixed user script answered live, an authored
   conversation graded at checkpoints, or both as proposed here?
3. Which memory condition is the primary one?
4. Should a failed gate stop the trajectory, or only cap its score?
5. Judge model, a second judge family, and the spending ceiling for the Oct 8 runs.

## 9. Proposed layout

```
spec/trajectories/      conversation specs
spec/rubrics/           rubric items
agents/                 the chat-model agent
tools/                  compile spec → tasks; agreement and disagreement report
tasks/                  generated Harbor tasks (checkpoints/, live/)
configs/                job configs: one agent entry per model, attempts, judge
labels/                 manual verdicts
docs/
```
