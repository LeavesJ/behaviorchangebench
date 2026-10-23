# Expert meeting · Fri Oct 16 · Zoom

With Dr. Washington (Peter), the UCSF experts (Dr. Nhung Nguyen, Dr. Sameer Neupane, Dr. Arka
Ghosh) and Sam. Attendance: [TK]. Notes: Jarron.

If you missed it, this page catches you up in about five minutes. The next check-in is
**Fri Nov 6, 12-1 PT, same Zoom room**.

## What we showed

The Oct 8 prototype: one made-up conversation (Dani, 21, quitting vaping over five chats in six
weeks), frozen at fixed moments. Three models replied at each moment, three times each. Claude
Opus graded every reply against pass/fail checks, and we labeled the same replies by hand and
reviewed every disagreement. The write-up is `docs/reports/2026-10-08-oct8-results.md`.

## What we heard

These are from Jarron's notes, not a transcript. If you remember something differently, edit this
page.

- **The experts now write the rubrics and the conversations.** That was always the plan ("it is
  not your job to ultimately create the rubric", Peter, Sep 11). Our part is the paper, the
  infrastructure, and test conversations.
- **Keep the scope constrained.** Our vaping work leans on motivational interviewing (MI), but not
  every behavior science applies. The rubric should draw on evidence-based behavior-change
  techniques, including the ones already in the prototype, and not try to cover everything.
- **One conversation can switch approach.** As the person moves along (unsure, then decided, then
  slipping), the right approach can move from MI to CBT to relapse prevention. So each check has
  to say which approach it assumes.
- **What would experts want an agent eval to test?** We asked this, with overrefusal as our
  example: what it costs the user when the assistant refuses, or just says "see a doctor".
- **Other signals came up in the answer:** facial expression (alongside an approach like MI),
  motion, voice cadence, sound. We haven't been asked to build anything there. We're asking Peter
  whether to brainstorm it now.

## Peter's next steps (his email, word for word)

> - Draft NeurIPS Intro, Related Work, and Methods sections (including beautiful Methods figures) in the format of a NeurIPS eval track paper (look at HealthBench, MentalHealthBench, and existing NeurIPS eval papers as inspirations)
> - Make sure that the eval infrastructure can support any multi-turn convo, set of rubrics, and models to be evaluated using best practices for deploying an eval in the real world
> - Talk with friends to get some sample conversations to start testing with related to (1) tobacco/nicotine use, (2) physical activity, and (3) TBD

## What "done" looks like on Nov 6

| Next step | Done means | Who |
|---|---|---|
| Paper | A full draft of Intro, Related Work and Methods, with 3-4 figures. Every citation checked, every number traced to a run file. A v0 is in `paper/` so nobody starts from a blank page | [TK: split by part, see the group chat] |
| Infrastructure | One command runs any conversation file with any rubric file against any list of models, and nothing in the code is specific to vaping | [TK] |
| Sample conversations | 2-3 per domain (nicotine, physical activity), role-played, with consent | everyone; how-to in `docs/sample-conversations.md` |

The Intro is the place to start. It's more than one paragraph: some history of behavior-change
science, what's new in AI, and how the way these models are built (predicting the next word,
then tuned to please people) shows up in their answers and in how our prototype tests for it.

## Questions we've sent back

1. Should we brainstorm non-text signals (facial expression, motion, voice) now, or later?
2. Can friends' conversations be about their own nicotine use, or does that need IRB review
   first? Until we hear, role-play only.
3. What's the third domain?

## Running log

After each meeting we add three short lists here.

- **Current data:** the Oct 8 run (117 replies, our labels, the disagreement review). It's enough
  to write the Methods and to test the new infrastructure against old numbers.
- **Future data:** expert-written conversations and rubrics; friends' role-played conversations
  for nicotine and physical activity.
- **Reference technologies:** HealthBench, MentalHealthBench, NeurIPS eval-track papers; eval
  frameworks to compare our pipeline against (Inspect, Harbor).

## Words you'll hear

| Word | Meaning here |
|---|---|
| NeurIPS eval track | The NeurIPS conference track for papers that introduce benchmarks and datasets |
| MI | Motivational interviewing: helping someone find their own reasons to change, mostly by asking rather than telling |
| CBT | Cognitive behavioral therapy: practical skills for changing thoughts and habits once someone has decided to change |
| Relapse prevention | Planning for slips and recovering from them without giving up |
| Overrefusal | When the assistant refuses or deflects help it should have given |
| IRB | The review board that approves research involving people |
