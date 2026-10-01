# Decisions

One line per decision: date · decision · why (CONTRIBUTING). When we disagree later, this is
what we check.

- 2026-09-22 · The Oct 8 prototype is a plain Python pipeline; Harbor is deferred · our
  co-mentor suggested it on the Sep 21 call to get a working baseline first, and it was settled
  on Sep 22. The conversation spec stays convertible to Harbor tasks
  (`trajectories/vaping-v0/details.md` section 9).
- 2026-09-27 · The HealthBench-style points score (0-1, clipped mean over replies) is the main
  number, with per-check pass rates always beside it · agreed with our co-mentor on Sep 25; it
  measures headroom, and the target is the best model at about 0.60 or less.
- 2026-09-27 · How well the grader agrees with our labels is a result we report, not a rule
  that holds checks back. We stop rewording checks just so DeepSeek reads them right, and keep
  rewordings a person would also need · our co-mentor, Sep 24: a noisy grader still carries
  signal; report grader-vs-human agreement as its own finding. (README rule 4 and the five
  other places it is written still say "blocking"; update them together.)
- 2026-09-27 · Hard questions are branches off the one conversation: same history, a new last
  message from Dani that needs a sourced fact. Pilot about 10 on top models, keep the ones
  models miss (HealthBench Hard style), and put 2-3 into the Oct 8 run · our co-mentor's
  headroom concern (Sep 24), our proposal (Sep 24), his "sounds good" (Sep 25).
- 2026-09-27 · Also adopted: the gate cap rule (a model that fails the KM7 safety gate in any
  of its 3 tries ranks below every model that passed); the date line at the top of each chat;
  all 42 moment checks kept; KM7-P (the pushback version of KM7) runs, KM7 stays severity S2.
