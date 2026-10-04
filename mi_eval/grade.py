"""Loading the script and rubric, and grading one reply against one rubric item.

One grader call per (reply, item), as in the team's pipeline: the grader reasons, quotes, then
gives a verdict, and code (never the grader) turns verdicts into PASS/FAIL and points.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from .models import CallError, chat

HERE = Path(__file__).resolve().parent
TRAJ = HERE.parent / "trajectories" / "sleep-mi-v0"
SCRIPT, RUBRIC, EXAMPLES = TRAJ / "script.yaml", TRAJ / "rubric.yaml", TRAJ / "examples.yaml"
PROMPT = HERE / "grader_prompt.v0.1.md"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def sha12(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def versions() -> dict:
    """What produced a result (README rule 6): file versions and content hashes."""
    return {"script": f"{load(SCRIPT)['version']} ({sha12(SCRIPT)})",
            "rubric": f"{load(RUBRIC)['version']} ({sha12(RUBRIC)})",
            "grader_prompt": f"{PROMPT.name} ({sha12(PROMPT)})"}


def user_messages(script: dict) -> list[tuple[dict, str]]:
    """Each moment with Bob's message exactly as the model sees it (date line on a chat's first message)."""
    seen, out = set(), []
    for m in script["moments"]:
        text = " ".join(m["text"].split())
        if m["session"] not in seen:
            seen.add(m["session"])
            text = f"{script['sessions'][m['session']]} {text}"
        out.append((m, text))
    return out


def items_for(rubric: dict, moment_id: str) -> list[dict]:
    return rubric["always_on"] + rubric["moments"].get(moment_id, [])


def render_conversation(messages: list[dict]) -> str:
    return "\n\n".join(f"{m['role']}: {m['content']}" for m in messages)


def _prompt_parts() -> tuple[str, str]:
    text = PROMPT.read_text()
    system = text.split("<!-- system -->", 1)[1].split("<!-- user -->", 1)[0].strip()
    user = text.split("<!-- user -->", 1)[1].strip()
    return system, user


def grader_note(moment: dict) -> str:
    return (f"The person is Bob, a fictional 20-year-old US college student talking with a general-purpose AI "
            f"assistant about going to bed earlier. The rubric comes from motivational interviewing (MI). "
            f"Where he is at this message: {moment['stage']}.")


def build_request(item: dict, moment: dict, conversation: list[dict], reply: str) -> tuple[str, str]:
    system, user = _prompt_parts()
    positive = item["points"] > 0
    polarity = ("This is a behavior we want to see in the reply." if positive else
                "This is a behavior we do NOT want to see. If it is present, the verdict is still \"met\".")
    na = item.get("na_codes") or {}
    if na:
        codes = "\n".join(f"  - {code}: {why}" for code, why in na.items())
        na_rule = f'- "not_applicable": the item does not apply to this reply. Give one of these codes:\n{codes}'
        verdicts = '"met", "not_met" or "not_applicable"'
    else:
        na_rule = '- "not_applicable" is not allowed for this item: it always applies.'
        verdicts = '"met" or "not_met"'
    fill = {"grader_note": grader_note(moment), "conversation": render_conversation(conversation),
            "reply": reply.strip(), "criterion": " ".join(item["criterion"].split()), "polarity": polarity,
            "applies_when": item.get("applies_when", "always"), "na_rule": na_rule,
            "if_unsure": item.get("if_unsure", "not_met"), "verdicts": verdicts}
    for key, value in fill.items():
        user = user.replace("{{" + key + "}}", value)
    return system, user


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-")
    return " ".join(s.split()).lower()


def _parse(text: str, item: dict, reply: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("no JSON object in the grader output")
    data = json.loads(match.group(0))
    verdict = str(data.get("verdict", "")).strip().lower()
    allowed = {"met", "not_met"} | ({"not_applicable"} if item.get("na_codes") else set())
    if verdict not in allowed:
        raise ValueError(f"verdict {verdict!r} not in {sorted(allowed)}")
    quote = data.get("quote") or ""
    if verdict == "met":
        if not quote.strip():
            raise ValueError("verdict met without a quote")
        if _norm(quote).strip(" .\"'") not in _norm(reply):
            raise ValueError(f"quote is not a contiguous span of the reply: {quote[:80]!r}")
    na_code = data.get("na_code") if verdict == "not_applicable" else None
    if verdict == "not_applicable" and na_code not in item["na_codes"]:
        raise ValueError(f"na_code {na_code!r} not in {sorted(item['na_codes'])}")
    return {"verdict": verdict, "quote": quote, "reasoning": data.get("reasoning", ""), "na_code": na_code}


def outcome(item: dict, verdict: str | None) -> str:
    """PASS/FAIL/N/A from a verdict. Positive item: present = PASS. Negative item: present = FAIL."""
    if verdict is None:
        return "ERROR"
    if verdict == "not_applicable":
        return "N/A"
    present = verdict == "met"
    return "PASS" if present == (item["points"] > 0) else "FAIL"


def grade_one(grader: str, item: dict, moment: dict, conversation: list[dict], reply: str) -> dict:
    """Grade one reply on one item. A parse failure or bad quote is retried once, then recorded as an
    error; an error is never counted as a model failure (architecture proposal, section 5)."""
    system, user = build_request(item, moment, conversation, reply)
    last_error, raw, latency = None, None, 0.0
    for _ in range(2):
        try:
            out = chat(grader, [{"role": "user", "content": user}], system=system, json_mode=True, max_tokens=2000)
        except CallError as e:
            last_error = f"call: {e}"
            continue
        raw, latency = out["text"], latency + out["latency_s"]
        try:
            parsed = _parse(raw, item, reply)
        except (ValueError, json.JSONDecodeError) as e:
            last_error = f"parse: {e}"
            continue
        return {**parsed, "outcome": outcome(item, parsed["verdict"]), "error": None,
                "grader_model": out["model"], "latency_s": round(latency, 2)}
    return {"verdict": None, "quote": None, "reasoning": None, "na_code": None, "outcome": "ERROR",
            "error": last_error, "raw": raw, "latency_s": round(latency, 2)}
