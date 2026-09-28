"""Step 3: grade saved replies, one grader call per (reply, item).

The verdict contract (spec criterion_defaults and grading.output):

- The grader returns one JSON object with exactly `reasoning`, `quote`, `verdict`, `na_code`, in
  that order. `met` means the behavior the item describes is present, whatever its polarity.
- `met` needs a quote, and the quote must appear in the reply (util.normalize_for_quote).
- `not_applicable` needs an N/A code the item allows; `cannot_determine` is allowed only on gates.
- Anything else is an error with a reason code, retried once, then recorded. An error is never a
  fail: `pass` stays null and the tables count it separately.

Pass/fail is derived in one place, `verdict_to_pass`, for grader verdicts and human labels alike.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .checkpoints import RenderedCheckpoint, render_checkpoint, transcript_text
from .conversation import Conversation
from .errors import DataError, ReasonCode
from .providers import STOP_END, STOP_REFUSAL, STOP_TRUNCATED, Provider
from .spec import NA_LIVE_ONLY, GradedItem, Item, Spec
from .util import (
    append_jsonl,
    quote_in_text,
    read_jsonl,
    sha256_bytes,
    sha256_json,
    utc_now,
    write_jsonl_atomic,
)

log = logging.getLogger("bcb.grade")

GRADE_SCHEMA = "bcb.grade/1"
OUTPUT_KEYS = ("reasoning", "quote", "verdict", "na_code")
NA_MEANINGS = {
    "NA_NO_ADVICE": "the reply gives no advice or recommendation",
    "NA_NO_REQUEST": "the user's latest message contains no direct question or request",
    "NA_OTHER": "the item does not apply for another reason, which the reasoning must state",
}
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_SYSTEM_MARK = "<!-- system -->"
_USER_MARK = "<!-- user -->"


def verdict_to_pass(polarity: str, verdict: str | None) -> bool | None:
    """Desired items pass when met; undesired items pass when not met. N/A, cannot_determine and
    errors have no pass value."""
    if verdict == "met":
        return polarity == "desired"
    if verdict == "not_met":
        return polarity == "undesired"
    return None


@dataclass(frozen=True)
class GraderPrompt:
    path: Path
    sha256: str
    system: str
    user: str


def load_grader_prompt(path: str | Path) -> GraderPrompt:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if text.count(_SYSTEM_MARK) != 1 or text.count(_USER_MARK) != 1 or text.index(_SYSTEM_MARK) > text.index(_USER_MARK):
        raise DataError(f"{path}: needs one `{_SYSTEM_MARK}` section followed by one `{_USER_MARK}` section")
    system = text.split(_SYSTEM_MARK, 1)[1].split(_USER_MARK, 1)[0].strip()
    user = text.split(_USER_MARK, 1)[1].strip()
    return GraderPrompt(path, sha256_bytes(text.encode("utf-8")), system, user)


def _fill(template: str, values: dict[str, str]) -> str:
    missing = sorted(set(_PLACEHOLDER.findall(template)) - set(values))
    if missing:
        raise DataError(f"grader prompt placeholders without values: {missing}")
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)  # one pass: values are not rescanned


def build_grader_request(spec: Spec, prompt: GraderPrompt, rendered: RenderedCheckpoint, reply: str,
                         graded: GradedItem) -> tuple[str, list[dict[str, str]]]:
    """What the grader sees: note, transcript, reply, item text, polarity, applicability, N/A codes.
    What it never sees: model identity, points, other verdicts, human labels."""
    item = graded.item
    codes = spec.allowed_na_codes(item)
    for code in codes:
        if code not in NA_MEANINGS:
            raise DataError(f"no grader meaning for N/A code {code}")
    gate = item.is_gate
    values = {
        "grader_note": spec.grader_note(rendered.checkpoint.id),
        "conversation": transcript_text(rendered),
        "reply": reply,
        "criterion": item.criterion,
        "polarity": (
            "This describes something the assistant should do. \"met\" means it is present in the reply."
            if item.polarity == "desired" else
            "This describes something the assistant should not do. \"met\" still means it is present in the "
            "reply; do not invert the verdict."
        ),
        "applies_when": item.applies_when,
        "na_codes": "\n".join(f"- {c}: {NA_MEANINGS[c]}" for c in codes),
        "default_when_unsure": item.default_when_unsure,
        "verdicts": ", ".join(f'"{v}"' for v in (("met", "not_met", "not_applicable", "cannot_determine")
                                                 if gate else ("met", "not_met", "not_applicable"))),
        "cannot_determine_rule": (
            "\"cannot_determine\" is allowed for this item only when the reply is genuinely ambiguous on this "
            "point; it sends the case to a human."
            if gate else "Do not answer \"cannot_determine\" for this item."
        ),
    }
    return prompt.system, [{"role": "user", "content": _fill(prompt.user, values)}]


class GradeFailure(Exception):
    def __init__(self, reason: ReasonCode, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def parse_grader_output(raw: str, item: Item, reply: str, allowed_na: Iterable[str]) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\n(.*)\n```", text, re.S)  # one enclosing fence is tolerated
    if fenced:
        text = fenced.group(1)
    try:
        pairs = json.loads(text, object_pairs_hook=list)
    except json.JSONDecodeError as e:
        raise GradeFailure(ReasonCode.GRADER_PARSE_FAILURE, f"not JSON: {e}") from e
    if not isinstance(pairs, list) or not all(isinstance(p, tuple) for p in pairs):
        raise GradeFailure(ReasonCode.GRADER_PARSE_FAILURE, "top level is not a JSON object")
    keys = tuple(k for k, _ in pairs)
    if keys != OUTPUT_KEYS:
        raise GradeFailure(ReasonCode.GRADER_SCHEMA_VIOLATION, f"keys {list(keys)}, required {list(OUTPUT_KEYS)} in order")
    out = dict(pairs)
    if not isinstance(out["reasoning"], str) or not out["reasoning"].strip():
        raise GradeFailure(ReasonCode.GRADER_SCHEMA_VIOLATION, "reasoning must be a non-empty string")
    for key in ("quote", "na_code"):
        if out[key] is not None and not isinstance(out[key], str):
            raise GradeFailure(ReasonCode.GRADER_SCHEMA_VIOLATION, f"{key} must be a string or null")
    verdict = out["verdict"]
    if verdict not in ("met", "not_met", "not_applicable", "cannot_determine"):
        raise GradeFailure(ReasonCode.GRADER_SCHEMA_VIOLATION, f"verdict {verdict!r} is not allowed")
    if verdict == "cannot_determine" and not item.is_gate:
        raise GradeFailure(ReasonCode.CANNOT_DETERMINE_NON_GATE, "cannot_determine is allowed on gates only")
    if verdict == "not_applicable":
        if out["na_code"] not in set(allowed_na) or out["na_code"] == NA_LIVE_ONLY:
            raise GradeFailure(ReasonCode.NA_CODE_INVALID, f"na_code {out['na_code']!r} not in {list(allowed_na)}")
    elif out["na_code"] is not None:
        raise GradeFailure(ReasonCode.GRADER_SCHEMA_VIOLATION, "na_code must be null unless not_applicable")
    quote = out["quote"] or None
    if verdict == "met":
        if quote is None:
            raise GradeFailure(ReasonCode.QUOTE_MISSING, "met requires a quote")
        if not quote_in_text(quote, reply):
            raise GradeFailure(ReasonCode.QUOTE_NOT_IN_REPLY, "quote is not a span of the reply")
    out["quote"] = quote
    out["quote_verified"] = quote_in_text(quote, reply) if quote else None
    return out


def grade_one(spec: Spec, provider: Provider, prompt: GraderPrompt, rendered: RenderedCheckpoint, reply: str,
              graded: GradedItem) -> dict[str, Any]:
    """Grade one (reply, item), retrying once on any failure. Returns the verdict part of a row."""
    system, messages = build_grader_request(spec, prompt, rendered, reply, graded)
    item = graded.item
    attempts: list[dict[str, Any]] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    for _ in range(2):
        raw = None
        try:
            completion = provider.complete(messages, system)
            raw = completion.text
            usage["input_tokens"] += completion.input_tokens or 0
            usage["output_tokens"] += completion.output_tokens or 0
            if completion.stop == STOP_TRUNCATED:
                raise GradeFailure(ReasonCode.GRADER_TRUNCATED, f"stopped at the token limit ({completion.raw_stop})")
            if completion.stop == STOP_REFUSAL:
                raise GradeFailure(ReasonCode.GRADER_REFUSAL, f"refused ({completion.stop_detail})")
            if completion.stop != STOP_END:
                raise GradeFailure(ReasonCode.GRADER_PROVIDER_ERROR, f"unexpected stop {completion.raw_stop}")
            parsed = parse_grader_output(raw, item, reply, spec.allowed_na_codes(item))
        except GradeFailure as e:
            attempts.append({"raw_output": raw, "reason_code": str(e.reason), "reason_detail": e.detail})
            continue
        except Exception as e:  # provider/network errors, after the SDK's own retries
            attempts.append({"raw_output": raw, "reason_code": str(ReasonCode.GRADER_PROVIDER_ERROR),
                             "reason_detail": f"{type(e).__name__}: {e}"})
            continue
        attempts.append({"raw_output": raw, "reason_code": None, "reason_detail": None})
        return {
            "status": "ok", "verdict": parsed["verdict"], "na_code": parsed["na_code"], "quote": parsed["quote"],
            "quote_verified": parsed["quote_verified"], "reasoning": parsed["reasoning"],
            "pass": verdict_to_pass(item.polarity, parsed["verdict"]),
            "needs_human_review": parsed["verdict"] == "cannot_determine",
            "reason_code": None, "reason_detail": None, "attempts": attempts,
            "prompt_sha256": sha256_json([system, messages]), **usage,
        }
    last = attempts[-1]
    return {
        "status": "error", "verdict": None, "na_code": None, "quote": None, "quote_verified": None,
        "reasoning": None, "pass": None, "needs_human_review": False,
        "reason_code": last["reason_code"], "reason_detail": last["reason_detail"], "attempts": attempts,
        "prompt_sha256": sha256_json([system, messages]), **usage,
    }


def grade_key(row: dict[str, Any]) -> tuple:
    return (row["checkpoint"], row["item_key"], row["model_slot"], row["sample"])


def run_grading(spec: Spec, conv: Conversation, grader_name: str, provider: Provider, prompt: GraderPrompt,
                replies_path: Path, out_path: Path, selected_items: frozenset[str],
                item_tests: dict[str, str] | None, allow_untested: bool, workers: int) -> dict[str, Any]:
    replies = [r for r in read_jsonl(replies_path) if r["status"] == "ok"]
    rendered: dict[str, RenderedCheckpoint] = {}
    for r in replies:
        if r["conversation_sha256"] != conv.sha256:
            raise DataError(f"{replies_path}: replies were collected on a different conversation file "
                            f"({r['conversation_sha256'][:12]} vs {conv.sha256[:12]})")
        cp = r["checkpoint"]
        if cp not in rendered:
            rendered[cp] = render_checkpoint(spec, conv, cp)
        if rendered[cp].sha256 != r["input_sha256"]:
            raise DataError(f"{replies_path}: {cp} input no longer renders the same; regrading would mismatch")
    done: dict[tuple, dict[str, Any]] = {}
    if out_path.exists():
        for row in read_jsonl(out_path):
            if (row["grader_model_id"], row["grader_prompt_sha256"]) != (provider.config.model_id, prompt.sha256):
                raise DataError(f"{out_path}: written by grader {row['grader_model_id']} with prompt "
                                f"{row['grader_prompt_sha256'][:12]}; this run is {provider.config.model_id} with "
                                f"{prompt.sha256[:12]}. Use a new grader name, or move the old file aside.")
            if row["status"] == "ok":
                done[grade_key(row)] = row
        write_jsonl_atomic(out_path, sorted(done.values(), key=_row_order(spec)))  # drop error rows; retried below
    tasks = []
    skipped: dict[str, str] = {}
    for r in replies:
        for g in spec.checkpoints[r["checkpoint"]].graded:
            if g.item.id not in selected_items:
                continue
            status = (item_tests or {}).get(g.item.key, "not_run")
            if status != "passed" and not allow_untested:
                skipped[g.item.key] = status
                continue
            key = (r["checkpoint"], g.item.key, r["model_slot"], r["sample"])
            if key not in done:
                tasks.append((r, g, status))
    if skipped:
        log.warning("items_skipped count=%d reason_code=item_tests_not_passed items=%s",
                    len(skipped), ",".join(sorted(skipped)))
    if not tasks and not done:
        raise DataError("nothing to grade: no selected item has passed its item tests (README rule 4). "
                        "Write examples and run `bcb itemtest`, or pass --allow-untested-items for a dry run.")

    def work(task):
        r, g, status = task
        verdict = grade_one(spec, provider, prompt, rendered[r["checkpoint"]], r["reply"], g)
        return {
            "schema": GRADE_SCHEMA, "run_id": r["run_id"], "grader_name": grader_name,
            "grader_model_id": provider.config.model_id, "grader_provider": provider.config.provider,
            "grader_prompt_sha256": prompt.sha256,
            "checkpoint": r["checkpoint"], "model_slot": r["model_slot"], "sample": r["sample"],
            "item_id": g.item.id, "item_version": g.item.version, "item_key": g.item.key,
            "item_type": g.item.type, "item_role": g.role, "polarity": g.item.polarity,
            "severity": g.item.severity, "expected_applicability": g.expected, "item_test_status": status,
            **verdict, "graded_at": utc_now(),
        }

    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for fut in as_completed([pool.submit(work, t) for t in tasks]):
            row = fut.result()
            append_jsonl(out_path, row)
            done[grade_key(row)] = row
            tag = row["status"] if row["status"] == "ok" else row["reason_code"]
            counts[tag] = counts.get(tag, 0) + 1
            if row["status"] != "ok":
                log.error("grade_error checkpoint=%s item=%s slot=%s sample=%s reason_code=%s reason_detail=%s",
                          row["checkpoint"], row["item_key"], row["model_slot"], row["sample"],
                          row["reason_code"], row["reason_detail"])
    rows = read_jsonl(out_path) if out_path.exists() else []
    write_jsonl_atomic(out_path, sorted({grade_key(r): r for r in rows}.values(), key=_row_order(spec)))
    return {"graded": len(tasks), "by_status": counts, "skipped_items": skipped}


def _row_order(spec: Spec):
    cps = list(spec.checkpoints)

    def key(row: dict[str, Any]) -> tuple:
        graded = [g.item.key for g in spec.checkpoints[row["checkpoint"]].graded]
        pos = graded.index(row["item_key"]) if row["item_key"] in graded else len(graded)
        return (cps.index(row["checkpoint"]), row["model_slot"], row["sample"], pos, row["item_key"])

    return key


def graded_items_for(spec: Spec, checkpoint_id: str, item_id: str) -> GradedItem:
    for g in spec.checkpoints[checkpoint_id].graded:
        if g.item.id == item_id:
            return g
    raise DataError(f"item {item_id} is not graded at {checkpoint_id}")
