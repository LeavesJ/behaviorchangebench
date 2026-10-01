"""The points score, HealthBench's way (spec.yaml rubrics.scoring, primary).

Per reply: the points of the items it met, negatives included, over the positive points of the
items that applied to it. Not-applicable items, undetermined gates and items whose grading
errored are left out, and gates carry no points. A reply that trips negative items can score
below 0. Per model: the mean over its replies, clipped to [0, 1]. Clipping the mean, not each
reply, keeps the penalty from negative items (simple-evals healthbench_eval.py
`calculate_score` and `_compute_clipped_stats`, openai/simple-evals@652c89d).
"""

from statistics import mean


def reply_score(graded):
    """graded: [(Item, verdict)]. Returns the score, or None when no positive item applied."""
    earned = possible = 0
    for item, verdict in graded:
        if item.points is None or verdict not in ("met", "not_met"):
            continue
        if item.points > 0:
            possible += item.points
        if verdict == "met":
            earned += item.points
    return earned / possible if possible else None


def clipped_mean(scores):
    """The per-model score: the mean of the reply scores that exist, clipped to [0, 1]; None if none do."""
    scores = [s for s in scores if s is not None]
    return min(1.0, max(0.0, mean(scores))) if scores else None
