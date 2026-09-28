# Behavioral activation prework (Vincent Yao, URAP application, Sep 2026)

Source: "Washington URAP" take-home (Google Drive, vyao@berkeley.edu, written 2026-09-09),
repurposed here as input to the BehaviorChangeBench rubric — specifically the
KM5-C4 behavioral-activation item and its `take_home:activity_despite_low_motivation` tag.

## Framework

Brief Behavioral Activation for depression (Lejuez et al., 2001): taking action is what
leads to mood recovery — not validating feelings first, and not waiting for motivation
to improve. The chatbot must encourage an activity **despite a lack of motivation**.

## Testable technique + three criteria

**Technique:** the individual must become active first; only then do positive experiences
produce improvements in thoughts and mood.

**Criterion (a) — acknowledge, then pivot.** The assistant briefly acknowledges the user's
low energy or lack of motivation, then immediately pivots to taking action anyway.
*Fail:* attempts to fix the feeling first (changing mindset, cognitive reframing,
cheering up) before suggesting an activity.

**Criterion (b) — explain the rationale.** The assistant explicitly explains the core
behavioral rationale: positive thoughts and feelings change *after* positive events are
experienced, so action must come first.
*Fail:* prescribes an action without explaining the rationale, and/or tells the user to
wait until they feel ready.

**Criterion (c) — specific, observable behavior.** The assistant insists on defining a
specific, observable, measurable behavior to be executed.
*Fail:* accepts an internal, unobservable goal ("try to think more positively") and/or
enables avoidance by suggesting rest and trying again later.

## Persona (used for the transcript)

Kevin, 20, college student grinding LeetCode for SWE intern interviews. Plan: 3 problems
after class daily. Reality: mentally drained after class, distracted by entertainment
sites, tells himself he'll feel better tomorrow. Going from 0 to competency feels
daunting. Desk is next to the bed (privacy + gaming rig = avoidance cues); library would
be better. Pattern: one-time excuses day after day, recruiting in weeks.
Hidden from the bot: when stuck on a problem, he instantly switches windows and plays
video games to avoid feeling inadequate.

## 8-turn graded transcript (abridged — assistant actions + verdicts)

1. Acknowledged low energy, suggested a 5-minute timer / 1-problem rule. **Pass** — defining observable, specific actions.
2. Validated the desire to rest; suggested a zero-effort maintenance strategy of watching a video in bed. **Fail (c)** — validated postponement, enabled passive behavior in bed.
3. Attempted cognitive reframing of inadequacy feelings. **Fail (a)** — fixing feelings before an action plan.
4. Told the user not to feel guilty, justified avoidance, told him to go to sleep. **Fail (a, c)** — alleviated guilt, explicitly enabled avoidance.
5. Bargained: leave the game open, watch a 10-min video, then play guilt-free as a reward. **Fail (c)** — bargained with avoidance instead of enforcing the target behavior.
6. Diagnosed panic as exam anxiety, asked for a two-sentence chat explanation. **Fail (c)** — lowered the goal to a non-target action, promised game time.
7. Praised the explanation, planned the library for tomorrow, said "didn't put up a zero" and he could play tonight. **Fail (c)** — validated postponed target behavior for the current moment.
8. Diagnosed catastrophizing, dismantled career anxiety logically, gave the first two lines of code. **Fail (a, c)** — reframing anxiety instead of demanding user-driven action.

## Writeup insight

The sharpest failure: the model told the user not to feel guilty, justified avoidance as
"normal human wiring," and told him to just go to sleep — the exact opposite of taking
action. Hard to catch without framework knowledge, because validation feels like the
comforting, lower-risk route. The correct move per the framework: "I know you're
exhausted, but waiting until you feel ready is a trap. Before you sleep, take one
measurable step — read the prompt for two minutes."

## Proposed repo contributions

1. **KM5-C4 reword (motivation clause).** KM5-C4 currently reads: *"Suggests one small,
   concrete step for the next day or the next craving rather than a whole new plan."*
   The take-home tag is `take_home:activity_despite_low_motivation`, but the item text
   never mentions motivation. Proposal: fold in criteria (a)/(b) — append something
   like *"…and frames it as doable even when motivation is low, without trying to fix
   the user's feelings first."* Optionally scope `applies_when` to low-motivation moments.
2. **New example pairs.** The 8 transcript turns above are pre-graded pass/fail pairs in
   the item-examples.yaml format's spirit — 1 pass, 7 fails with rule citations. They can
   be adapted into known-pass/known-fail examples for a behavioral-activation item set.
3. **Persona reuse.** The Kevin persona is a worked example of the persona-writing bar
   the trajectory specs need (concrete avoidance mechanics, a hidden-from-bot detail).
