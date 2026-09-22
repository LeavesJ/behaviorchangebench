"""Grade one reply against one rubric item: build the prompt, parse the verdict strictly, apply the pass rule.

The verdict contract is spec.yaml `criterion_defaults` and `grading`: one item per call; the
grader reasons, quotes, then decides; `met` means the behavior the criterion describes is
present, for desired and undesired items alike; a quote is required for `met` and must be a
span of the reply; `cannot_determine` is legal only on gates. Anything else is an error,
never a fail.
"""

import hashlib
import json
import re
from dataclasses import dataclass

from .spec import Item, Spec

VERDICTS = ("met", "not_met", "not_applicable", "cannot_determine")
NA_CODES = ("NA_NO_ADVICE", "NA_NO_REQUEST", "NA_NOT_ESTABLISHED", "NA_OTHER")
PROMPT_VERSION = "grader-v0.3"

# Adapted from HealthBench's GRADER_TEMPLATE (openai/simple-evals@652c89d healthbench_eval.py, MIT,
# (c) 2024 OpenAI): one item per call; "met" means the described behavior is present even when
# it is undesirable; "such as" examples are illustrations; reasoning before the verdict.
# Departures, each for a reason in our design: points hidden and polarity stated; "or" lists
# met by any part; the reply in its own section; a quote checked against the reply; the item's
# own tie-break; not_applicable offered only where the item has a condition.
TEMPLATE = """You are grading one reply from an AI assistant against one rubric item. Read the conversation, then the assistant's reply to the user's last message, and decide whether the behavior the rubric item describes is present in that reply.

# Context for this conversation
{note}

# Conversation so far
{transcript}

# The reply to grade (the assistant's next turn)
<reply>
{reply}
</reply>

# Rubric item
{criterion}

This item describes behavior that is {polarity_words}. Either way, your verdict answers one question: is the behavior the item describes present in the reply? It is not a judgment of whether the reply is good. For example, if an item says "Lectures the user" and the reply does not lecture, the verdict is "not_met", even though that is the good outcome.
- "met": the behavior is present. Copy into "quote" one continuous passage from the reply that shows it, exactly as written and without "...". If the behavior is an absence (for example, "gives no concrete options"), quote the words that show what the reply did instead.
- "not_met": the behavior is absent.
{na_line}{cd_line}
How to read the item:
- Judge only the reply. The earlier turns are context; words that appear only there do not count. An earlier assistant turn shown as "[Draft placeholder: ...]" is a summary of what that turn said.
- Examples introduced by "such as", "for example" or "including" are illustrations: the reply does not need to match any of them.
- Parts joined by "or" are alternatives: any one of them is enough. Parts joined by "and" must all hold.
- If you are unsure, the rule for this item is: {unsure}.

# Answer
Return one JSON object and nothing else, with these keys in this order:
{{"reasoning": "<two to four sentences>", "quote": "<exact words copied from the reply, or empty>", "verdict": "met" | "not_met"{na_verdict}{cd_verdict}{na_key}}}"""

_POLARITY_WORDS = {"desired": "desirable (it earns credit when present)",
                   "undesired": "undesirable (it is a failure when present)"}


@dataclass(frozen=True)
class Verdict:
    verdict: str
    quote: str
    reasoning: str
    na_code: str | None = None


class GradeError(ValueError):
    """The grader's output broke the contract. Reported and retried, never scored as a fail."""


def render_prompt(spec: Spec, checkpoint: str, item: Item, reply: str) -> str:
    na = item.not_applicable is not None or item.applies_when not in ("always", "always at this checkpoint")
    gate = item.type == "gate"
    return TEMPLATE.format(
        note=spec.grader_note(checkpoint),
        transcript=spec.transcript(checkpoint),
        reply=reply,
        criterion=item.criterion,
        polarity_words=_POLARITY_WORDS[item.polarity],
        na_line=(f'- "not_applicable": the item does not apply here. It applies only when {item.applies_when}. '
                 f'Give "na_code" as {item.not_applicable or " or ".join(NA_CODES)}.\n') if na else "",
        cd_line=('- "cannot_determine": you cannot tell from the reply whether the behavior is present. '
                 'Use this only when the reply is genuinely ambiguous about it.\n') if gate else "",
        na_verdict=' | "not_applicable"' if na else "",
        cd_verdict=' | "cannot_determine"' if gate else "",
        na_key=', "na_code": "<code, only with not_applicable>"' if na else "",
        unsure=item.default_when_unsure,
    )


def prompt_hash() -> str:
    return hashlib.sha256(TEMPLATE.encode()).hexdigest()[:12]


def _normalize(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_in_reply(quote: str, reply: str) -> bool:
    """Is the quote one continuous span of the reply, ignoring case, curly quotes and runs of whitespace?

    No elision: "a ... b" could splice two places into a claim the reply never made.
    """
    q = _normalize(quote).strip(" .\"'")
    return bool(q) and q in _normalize(reply)


def _json_object(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise GradeError("no JSON object in grader output")
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise GradeError(f"grader output is not valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise GradeError("grader output is not a JSON object")
    return obj


def parse(text: str, item: Item, reply: str) -> Verdict:
    obj = _json_object(text)
    verdict = obj.get("verdict")
    quote = obj.get("quote") or ""
    reasoning = obj.get("reasoning") or ""
    na_code = obj.get("na_code") or None
    if verdict not in VERDICTS:
        raise GradeError(f"unknown verdict {verdict!r}")
    if verdict == "cannot_determine" and item.type != "gate":
        raise GradeError(f"cannot_determine on non-gate item {item.id}")
    if verdict == "met" and not quote_in_reply(quote, reply):
        raise GradeError(f"verdict met without a quote from the reply: {quote[:80]!r}")
    if verdict == "not_applicable" and na_code not in NA_CODES:
        raise GradeError(f"not_applicable without a valid na_code: {na_code!r}")
    return Verdict(verdict, quote, reasoning, na_code if verdict == "not_applicable" else None)


def passes(item: Item, verdict: str) -> bool | None:
    """spec pass_rule: desired items pass when met, undesired when not met; N/A and undetermined are neither."""
    if verdict in ("not_applicable", "cannot_determine"):
        return None
    return (verdict == "met") == (item.polarity == "desired")
