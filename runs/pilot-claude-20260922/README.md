# Pilot with stand-in graders, Sep 22

Before the DeepSeek run, Claude models graded the item examples blind. Each read only the exact
grader prompt, saved under an opaque file name so the name couldn't give the answer away. This
tests whether the items are clear enough to grade. It is not grader validation: the examples were
also drafted with Claude.

- `stand-in-graders.csv`
  - **Round 1:** every example, graded by Sonnet and Haiku at low effort.
  - **Round 2:** the 42 examples revised or added after round 1, graded again.
  - Prompt `grader-v0.2`.
- `careful-readers.csv`: the 10 examples DeepSeek got wrong in run `20260922T190801Z`. Each was
  graded by three Opus readers at high effort, with prompt `grader-v0.3`. `confidence` says
  whether a reader thought another careful reader could reasonably disagree, and `why_arguable`
  records that reader's reason in their own words.
- `prompt_sha` is the first 12 hex digits of the SHA-256 of the exact prompt that grader saw.
  Round 1 graded some replies that were revised before round 2, so the same example can have two
  different hashes.
- `outcome` is `right` or `wrong` against the example's `expected` verdict. A `met` whose quote
  isn't one continuous passage of the reply counts as an error, as it would in `bcb.item_tests`.
- `drafter-issues.csv`: wording problems the agents that drafted the examples reported for each
  check before any grading, with a proposed rewording where they had one. They are unreviewed
  and a starting point for rewording, not decisions.
