# How we work

*Strawman for the first meeting. Change it together.*

## Changes

- Branch, then a pull request into `main`. Keep them small enough to review in ten minutes.
- One review before merging. For a design section, the reviewer is the one named in
  `docs/design/README.md`; their job is to try to break it.
- No force-pushes to `main`.

## Writing

- A design section ends in decisions (see `docs/design/README.md`), about two pages.
- Cite primary sources with links. If a model drafted a paragraph or supplied a citation,
  open the source and check it before the pull request. A made-up reference in front of our
  collaborators would cost more than any amount of time saved.
- Someone else's section is theirs. Suggest changes in a pull request or a comment.

## Data files

- Rubric items and conversation specs are data, not prose. Changing one bumps its version,
  and results always record which version they used.
- Every rubric item carries at least one known-pass and one known-fail example.

## Decisions

Anything the group decides goes in `docs/decisions.md` as one line: date, decision, why.
When we disagree later, that file is what we check.

## Secrets and data

- API keys live in your environment or an untracked `.env`. Never in a commit.
- No real user conversations in this repository until consent and de-identification are
  settled with the PI. Simulated conversations only.
