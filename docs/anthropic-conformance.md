# Conformance to Anthropic's wellbeing-eval guidance

Source: Anthropic, ["What we look for in a wellbeing evaluation"](https://www-cdn.anthropic.com/files/4zrzovbb/website/5ecb637cb206057cb93cf4a9e72e843fda5e9892.pdf) (2 pages).
Every design decision in `docs/design/` should name the item it satisfies. A row stays
**open** until a section makes a decision about it; **not yet** is an acceptable status for
the October prototype as long as it is written down.

## Qualities they look for

| ID | In short | Where we answer it | Status |
|---|---|---|---|
| A1 | Clearly defined construct: what counts as pass and fail, why it matters, evidence cited, gaps noted | 01 detection, 02 rubric structure | open |
| A2 | Measure failure as both harmful compliance and overrefusal | 04 severity and overrefusal | open |
| A3 | Multiple severity levels, with a rubric saying which cases are more severe | 04 | open |
| A4 | Decomposed scoring: separate pass/fail dimensions rather than one blended score | 02 | open |
| A5 | Realistic multi-turn tasks where risk escalates or context shifts; varied tone and cues | trajectories, 03 domains | open |
| A6 | External validity: the access method matches the surface the claims are about | trajectories, harness | open |
| A7 | Internal validity: no ambiguous items, scoring artifacts or contamination | 02, 05 | open |
| A8 | Graders validated against human experts, agreement reported, judge differs from models under test, transcripts read by hand | 05 grader validation | open |
| A9 | Honesty about how simulated users differ from real ones | trajectories | open |
| A10 | Built with domain experts, especially at the pass/fail border | 06 expert authoring | open |
| A11 | Reliability and reproducibility: repeated runs, stable prompts, enough released to reproduce | harness | open |
| A12 | Sample size: at least ~100 items for complex multi-turn evals | roadmap; the October prototype is n = 1 by design | not yet |
| A13 | Multimodal coverage where the risk needs it | out of scope for now; say why | open |
| A14 | Regional and linguistic variation | 03, roadmap | not yet |

## Common problems they name

| ID | Problem | Our guard | Status |
|---|---|---|---|
| C1 | No human validation | 05: expert labels, agreement reported per item | open |
| C2 | Underspecified grading criteria, borderline cases ignored | 02: every item states its default when unsure and ships with known-pass and known-fail examples | open |
| C3 | Circular AI-as-judge | 05: judge family never equals the family under test; sensitivity to judge choice reported | open |
| C4 | Detached from real use: single-turn, shock-value, one-sided | multi-session trajectories; paired harmful-compliance and overrefusal cases | open |
| C5 | Trivially gameable: scores move with superficial prompt changes | harness: report sensitivity to a one-line system prompt and to paraphrased user turns | open |
| C6 | Ambiguous items | 02: applicability condition per item; 05: disagreement analysis feeds item revisions | open |
| C7 | Saturation | run several frontier models early; drop or harden items every model passes | open |
