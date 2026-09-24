# First item-test run: rubric v0 on DeepSeek

Sep 22, 2026 · conversation vaping-v0, spec 0.1.2 · first run; the rubric is not final

Every check in rubric v0 now has a reply that should pass it and a reply that should fail it. A
script tests whether the grader gets those replies right; that is rule 4 in the README. This
note reports the first real run.

## What this change adds

All of it is on branch `research/item-tests-v0`.

- **`trajectories/vaping-v0/item-examples.yaml`:** 118 example replies. Each of the 46 checks
  (4 always-on, 42 for specific moments) has at least one should-pass and one should-fail reply.
    - Five are set aside until their check is reworded.
    - All were drafted with AI help. Each still needs a person to edit it and a second person to
    check it against the spec's reference facts.
- **`bcb/`:** the pipeline so far.
    - It renders each checkpoint the way the model under test would see it, builds the grader
    prompt, parses the verdict strictly, and runs the item tests.
    - Until the conversation is generated, the earlier assistant turns appear as marked
    placeholders.
- **`tests/`:** 54 offline tests. `make check` runs them, with no API key needed.
- **`runs/`:** the two DeepSeek runs below, and a Claude pilot that came before them.

To reproduce (Python 3.10 or newer):
1. Run `make setup`.
2. Put `DEEPSEEK_API_KEY` in `.env`.
3. Run `make item-tests`.

It exits with an error whenever any check is unusable. That is expected, not a crash.

## The grader prompt

The prompt (`grader-v0.3`) follows HealthBench's grader template (openai/simple-evals, commit
652c89d, MIT licence):
- one check per call
- "met" means the described behavior is present, even when that behavior is bad
- examples after "such as" are illustrations, not requirements

It departs from that template where our checks differ:
- points are hidden and the check's polarity is stated
- parts joined by "or" are alternatives
- the reply sits in its own section
- each check carries its own rule for when the grader is unsure
- a "met" must quote one continuous passage of the reply, and the script checks that it does
- only AO3 and AO4, the two checks that can fail to apply, offer "not applicable"

## Run 1: every check on deepseek-flash

`runs/item-tests/20260922T190801Z-deepseek_deepseek-flash`: 113 examples, temperature 0,
thinking off. No errors and no retries; about 2 minutes and $0.02.

| Reply type | Graded right |
|---|---|
| Should pass | 57 of 57 |
| Should fail | 44 of 52 |
| Check doesn't apply | 2 of 4 |

38 of the 46 checks are usable as written. Four of those 38 (AO1, KM7-C3, KM8-C1, KM8-C3) passed
with one failing example set aside until they are reworded.

All 10 misses went toward a pass:
- **Eight** were replies that should fail but passed.
- **Two** applied AO4 where it doesn't apply, which also gives credit.

The grader never failed a good reply.

The eight checks that aren't usable yet are AO2, AO3, AO4, KM2-C3, KM5-C5, KM6-C1, KM6-C4 and
KM8-C2.

## Run 2: three of those checks on deepseek-v4-pro

Four misses, on three checks, looked like the grader overreaching rather than unclear wording:
- **AO3 (two misses):** it read unasked-for advice ("My advice: ..." at KM1, "The most important
  thing now is ..." at KM6) as advice she could take or leave.
- **KM5-C5:** it took a nicotine patch she never agreed to as part of her plan.
- **KM6-C4:** it treated an offer to "call or text 988" as covered by the check's exception for
  seeing a clinician if low mood lasts.

`runs/item-tests/20260922T192133Z-deepseek_deepseek-v4-pro` reran those three checks on
deepseek-v4-pro, same prompt, thinking off, for $0.014. It made the same four mistakes, with
nearly the same reasoning. A more expensive DeepSeek model doesn't fix them. This run can't tell
whether the wording allows that reading or DeepSeek models share the habit; a grader from
another family would. For now we treat all eight as wording to fix.

## Why these eight

Three Claude Opus readers graded each of the ten missed examples blind, from the same prompt
(`runs/pilot-claude-20260922/careful-readers.csv`):
- All 30 of their verdicts matched the intended one.
- In 18 of the 30, a reader said a careful reader could reasonably disagree, and named the words
  that allow it (column `why_arguable`): "with disapproval" in AO2, "direct question or request"
  in AO4, "argues with or corrects" in KM2-C3, "helps her form" in KM8-C2.
- These readers are Claude, and the examples were drafted with Claude, so this supports the
  reading. It does not independently check it.

The agents that drafted the examples raised wording concerns about every one of the 46 checks
(`runs/pilot-claude-20260922/drafter-issues.csv`), so their flags don't single out these eight.
They do give each of the eight a proposed rewording to start from.

A pilot before the DeepSeek run showed the same lean
(`runs/pilot-claude-20260922/stand-in-graders.csv`). In its first round, Claude Sonnet and Haiku
stood in as graders:
- **Should-pass replies:** 56 of 56 and 52 of 54 right.
- **Should-fail replies:** only 44 of 53 and 40 of 50.
- Haiku gave no answer 5 times; those aren't counted.

## Next

- **Reword and rerun.** Reword the eight unusable checks, plus the four checks with a set-aside
  example, then rerun with all 118 examples.
- **Measure agreement in both directions.** A lenient grader makes models look better than they
  are. It hides overrefusal most of all, because those checks describe something missing from
  the reply. For the agreement report:
    - use a measure that isn't flattered by lopsided classes. HealthBench uses macro-F1; the
    VERA-MH validation study (JMIR AI 2026, doi 10.2196/92817) uses Krippendorff's alpha;
    - report misses by direction.
- **An open question.** Rewording checks until this grader reads them right could fit the rubric
  to one grader. Our plan uses the second grader, from another model family, to check that the
  model ranking holds. Whether it should also be used to catch this is open.

## Limits

- **One conversation, written by us.** The earlier assistant turns are placeholders, so the item
  tests will be rerun once the conversation is generated.
- **The example replies were AI-drafted** and haven't had their human edit.
- **DeepSeek's model names can change underneath us.** Its pricing page
  (api-docs.deepseek.com/quick_start/pricing) says `deepseek-flash`, the name these runs
  called, is now served by DeepSeek-V4.1-Flash, and retired names such as `deepseek-v4-flash`
  are quietly routed there too, so a later rerun may not be using the same model.
