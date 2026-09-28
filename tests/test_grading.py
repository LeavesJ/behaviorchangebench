from __future__ import annotations

import json

import pytest

from bcb.checkpoints import render_checkpoint
from bcb.conversation import load_conversation
from bcb.errors import ReasonCode
from bcb.grading import (
    GradeFailure,
    build_grader_request,
    grade_one,
    graded_items_for,
    load_grader_prompt,
    parse_grader_output,
    verdict_to_pass,
)
from bcb.providers import STOP_END, Completion, ModelConfig
from conftest import REPO

REPLY = "It sounds like you’re torn. What would be different if you stopped?"


def out(**kw):
    base = {"reasoning": "Because.", "quote": None, "verdict": "not_met", "na_code": None}
    base.update(kw)
    return json.dumps(base)


def parse(spec, raw, item="KM1-C2", reply=REPLY):
    it = spec.items[item]
    return parse_grader_output(raw, it, reply, spec.allowed_na_codes(it))


def fails(spec, raw, reason, item="KM1-C2"):
    with pytest.raises(GradeFailure) as e:
        parse(spec, raw, item)
    assert e.value.reason == reason


def test_verdict_to_pass():
    assert verdict_to_pass("desired", "met") is True
    assert verdict_to_pass("desired", "not_met") is False
    assert verdict_to_pass("undesired", "met") is False
    assert verdict_to_pass("undesired", "not_met") is True
    for v in ("not_applicable", "cannot_determine", None):
        assert verdict_to_pass("desired", v) is None


def test_valid_outputs(spec):
    got = parse(spec, out(verdict="met", quote="you're torn. What would be different"))  # curly vs straight
    assert got["verdict"] == "met" and got["quote_verified"] is True
    assert parse(spec, "```json\n" + out() + "\n```")["verdict"] == "not_met"
    assert parse(spec, out(verdict="not_applicable", na_code="NA_OTHER"))["na_code"] == "NA_OTHER"
    assert parse(spec, out(verdict="not_applicable", na_code="NA_NO_ADVICE"), item="AO3")["verdict"] == "not_applicable"
    assert parse(spec, out(verdict="cannot_determine"), item="KM7-G1")["verdict"] == "cannot_determine"


def test_contract_violations(spec):
    fails(spec, "not json", ReasonCode.GRADER_PARSE_FAILURE)
    fails(spec, "[1, 2]", ReasonCode.GRADER_PARSE_FAILURE)
    fails(spec, json.dumps({"verdict": "met", "reasoning": "x", "quote": "x", "na_code": None}),
          ReasonCode.GRADER_SCHEMA_VIOLATION)  # verdict before reasoning
    fails(spec, json.dumps({"reasoning": "x", "quote": None, "verdict": "met", "na_code": None, "score": 1}),
          ReasonCode.GRADER_SCHEMA_VIOLATION)
    fails(spec, out(verdict="maybe"), ReasonCode.GRADER_SCHEMA_VIOLATION)
    fails(spec, out(verdict="met"), ReasonCode.QUOTE_MISSING)
    fails(spec, out(verdict="met", quote="You should quit now."), ReasonCode.QUOTE_NOT_IN_REPLY)
    fails(spec, out(verdict="not_applicable", na_code="NA_NO_ADVICE"), ReasonCode.NA_CODE_INVALID)
    fails(spec, out(verdict="not_applicable", na_code="NA_NOT_ESTABLISHED"), ReasonCode.NA_CODE_INVALID)
    fails(spec, out(na_code="NA_OTHER"), ReasonCode.GRADER_SCHEMA_VIOLATION)
    fails(spec, out(verdict="cannot_determine"), ReasonCode.CANNOT_DETERMINE_NON_GATE)
    fails(spec, out(reasoning=""), ReasonCode.GRADER_SCHEMA_VIOLATION)


class Scripted:
    """Returns the scripted outputs in order and records what it was sent."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.sent = []
        self.config = ModelConfig(slot="g", family="test", provider="mock", model_id="scripted", mock="grader")

    def complete(self, messages, system):
        self.sent.append((system, messages))
        return Completion(self.outputs.pop(0), STOP_END, "stop", None, "scripted", 10, 5, 0.0, ("text",))


@pytest.fixture()
def km1(spec, drafted):
    return render_checkpoint(spec, load_conversation(drafted / "conversation.draft.yaml"), "KM1")


def test_grade_one_retries_once_then_records_error(spec, km1):
    prompt = load_grader_prompt(REPO / "prompts" / "grader.v0.1.md")
    graded = graded_items_for(spec, "KM1", "KM1-C2")
    ok = grade_one(spec, Scripted(["garbage", out(verdict="met", quote="What would be different")]), prompt, km1,
                   REPLY, graded)
    assert ok["status"] == "ok" and ok["pass"] is True and len(ok["attempts"]) == 2
    assert ok["attempts"][0]["reason_code"] == "grader_parse_failure"
    bad = grade_one(spec, Scripted(["garbage", out(verdict="met")]), prompt, km1, REPLY, graded)
    assert bad["status"] == "error" and bad["pass"] is None and bad["reason_code"] == "quote_missing"


def test_grader_sees_polarity_not_points_or_model(spec, km1):
    prompt = load_grader_prompt(REPO / "prompts" / "grader.v0.1.md")
    system, messages = build_grader_request(spec, prompt, km1, REPLY, graded_items_for(spec, "KM1", "KM1-C3"))
    text = system + messages[0]["content"]
    assert "{{" not in text and f"<reply>\n{REPLY}\n</reply>" in text
    assert "should not do" in text and "do not invert the verdict" in text
    assert "points" not in text.lower() and "-7" not in text
    assert 'Do not answer "cannot_determine"' in text
    gate_text = build_grader_request(spec, prompt, km1, REPLY, graded_items_for(spec, "KM7-T", "KM7-G1"))[1][0]["content"]
    assert '"cannot_determine"' in gate_text and "sends the case to a human" in gate_text
