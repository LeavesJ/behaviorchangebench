# 05 · Grader validation

**Owner:** TBD · **Reviewer:** TBD · **Guidance items:** A7, A8, A11, C1, C3, C5

How we measure agreement between the LLM judges and expert human raters, and what level we
hold ourselves to. For the October prototype the "experts" are us, which is a rehearsal of
the protocol, not a validation.

## Questions to settle

- **What is compared with what.** Judge vs expert consensus is the claim. Expert vs expert
  is the ceiling: a judge cannot be asked to agree with experts more than they agree with
  each other.
- **Which statistics.** Per item, and pooled per domain rather than over everything: a judge
  can be reliable on "did it ask before advising" and miss a withdrawal-risk cue entirely,
  and an overall number hides that. The confusion table, judge true-positive and
  true-negative rates with intervals, and a chance-corrected statistic. Kappa behaves badly when nearly
  everything passes, so report something robust to prevalence next to it.
- **The threshold.** A fixed cut-off, or "inside the expert–expert range"? Should gates be
  held to a stricter standard than fidelity items?
- **How many labels.** Enough double-coded turns per item for the interval to mean something.
- **Judge choice.** The judge's model family should differ from the family under test (C3).
  With Claude, GPT and Gemini all under test, that means more than one judge; report how
  much results move when the judge changes. The judge never sees which model wrote the turn.
- **Item tests.** Every item's known-pass and known-fail examples run before any real
  grading. A judge that gets its own examples wrong is not used.
- **Repeat runs** (A11): how many attempts per model, and how variance is reported.

## The disagreement report

Required for Oct 8: every case where the automated grader and our manual verdict differ.

| Item | Turn | Judge verdict and quoted evidence | Manual verdict and reason | Who was right | Cause | Change made |
|---|---|---|---|---|---|---|

Cause is one of: item wording ambiguous · judge error · manual error · genuinely borderline.
The last column is the point: disagreements are how the rubric improves.
