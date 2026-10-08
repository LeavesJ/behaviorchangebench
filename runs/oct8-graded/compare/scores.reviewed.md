# Scores before and after the disagreement review

The points score per model (0-1, clipped mean over replies), first as the grader gave it, then with the label's answer wherever the reviewer said the grader misread. Rows where label and grader agreed are taken as right; rows the reviewer called a label error, both fair, a close call or both wrong keep the grader's answer.

| Model | Graded: all | main | carried | Reviewed: all | main | carried | Verdicts changed |
|---|---|---|---|---|---|---|---|
| openrouter:google/gemini-3.1-pro-preview | 0.68 | 0.65 | 0.76 | 0.76 | 0.76 | 0.75 | 23 |
| openrouter:openai/gpt-6-astra | 0.67 | 0.72 | 0.50 | 0.74 | 0.79 | 0.60 | 22 |
| openrouter:x-ai/grok-4.7 | 0.52 | 0.50 | 0.58 | 0.59 | 0.59 | 0.61 | 18 |
