# 02 · Rubric structure

**Owner:** TBD · **Reviewer:** TBD · **Guidance items:** A1, A4, A7, C2, C6

One template that every pass/fail rubric item can be written in, whatever framework it comes
from, with safety gates (binary, score-capping) kept apart from technique fidelity. Our five
take-home criteria are the test: the template is done when all of them fit.

## Process

1. Agree a strawman list of fields (below).
2. Each of us rewrites our own take-home criterion into it.
3. Whatever did not fit drives version 2.

## Strawman fields

| Field | Why it is there |
|---|---|
| `id`, `version` | Items will change; results must say which version they used |
| `source` | Framework and citation (page or code), so an expert can check it |
| `type` | `gate` (any failure caps the score) or `fidelity` |
| `domain` | Which behavioral domain it applies to, or `any`. Most fidelity items are `any`; most gates are not |
| `jurisdiction` | Whose guidance the item encodes, where guidance diverges (US and UK differ on e-cigarettes for cessation, for one) |
| `unit` | What is judged: a turn, a session, or the whole trajectory |
| `applies_when` | The condition under which the item is live. An MI item about ambivalence should not score a turn after the user has asked for a plan |
| `fails_when` | The observable failure, written so two readers agree |
| `default_when_unsure` | Pass or fail, and why. Say it once, in the item |
| `not_applicable` | Reason codes, so N/A is a finding and not a hole |
| `severity` | For gates |
| `evidence` | The judge must quote the span that triggered the verdict |
| `examples` | At least one known-pass and one known-fail turn. The judge must get these right before the item is used |

## Questions to settle

- **Aggregation.** Gates cap; fidelity is reported per item. Is there any single headline
  number, and should there be?
- **Applicability rates.** An item that is never live has not been tested. Report how often
  each item applied, next to its pass rate.
- **Theory consistency.** From the brief: the approach should not change mid-conversation
  without a behavior-science reason. Is that one trajectory-level item? What counts as a
  legitimate switch?
- **Tags vs verdicts.** Descriptive codes (MITI behavior codes, BCT labels) are useful but
  should not leak into pass/fail.
