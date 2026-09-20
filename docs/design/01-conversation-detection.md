# 01 · Conversation detection

**Owner:** TBD · **Reviewer:** TBD · **Guidance items:** A1, A7, C6

The eval only applies to health-behavior-change conversations, so something has to decide
which conversations those are. This section must deliver a working definition, the hard edge
cases, a draft classifier prompt, and a small hand-labeled set with the prompt's results on it.

## Starting point

The project's working definition: the user raises changing something they do — starting,
sustaining or resuming a repeated, volitional, health-relevant behavior — where the
difficulty is the gap between intention and action.

## Questions to settle

- **Unit.** Is the label on the whole conversation, or on the turn where it *becomes* a
  behavior-change conversation? Rubric items need to know when they start applying.
- **Information vs change.** "How many calories are in rice?" vs "I keep overeating at
  night and want to stop." Where is the line, and what about a conversation that moves from
  one to the other?
- **Whose behavior.** "How do I get my dad to quit smoking?"
- **Direction of change.** A request to eat less from someone who may be underweight is
  behavior change. In scope with high severity, or handed to a different eval?
- **Border with mental-health evals.** The project is meant to complement them, not repeat them.
- **One-off vs repeated.** A single decision (get a vaccine) vs a habit.
- **Indirect and professional asks.** A nurse, a student writing an essay, a hypothetical.
- **Drift.** The conversation starts about exam stress and arrives at drinking.

## Labeled set

How many, who labels, how many are double-labeled, and what we report. Misses and false
alarms cost different things here: a miss is lost coverage, a false alarm applies a rubric
where it does not belong. Report both rates, not one accuracy number.
