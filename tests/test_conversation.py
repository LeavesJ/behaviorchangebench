from __future__ import annotations

import yaml

from bcb.checkpoints import render_checkpoint, transcript_text
from bcb.conversation import _count_lists, check_conversation, freeze_conversation, load_conversation


def _codes(spec, path):
    return {f.code for f in check_conversation(spec, load_conversation(path)) if f.severity == "error"}


def test_mock_draft_is_clean_and_pins_lines(spec, drafted):
    conv = load_conversation(drafted / "conversation.draft.yaml")
    assert _codes(spec, conv.path) == set()
    assert conv.order == spec.turn_order
    for tid in spec.turn_order:
        t = spec.turns[tid]
        if t.pinned:
            assert conv.text(tid) == spec.checkpoints[t.key_moment].user_line
            assert conv.turns[tid].source == "pinned"
    record = (drafted / "generation-record.json").read_text()
    assert '"model_id": "mock-generator-v0"' in record and len(yaml.safe_load(record)["sessions"]) == 5


def test_check_catches_each_violation(spec, drafted):
    path = drafted / "conversation.draft.yaml"
    doc = yaml.safe_load(path.read_text())
    turns = {t["id"]: t for t in doc["turns"]}
    turns["S1.A1"]["text"] += " Dani, this is from F10."
    turns["S1.U3"]["text"] = "honestly what's the point lol"
    turns["S1.U2"]["text"] = turns["S1.U2"]["text"] + " "
    turns["S2.A1"]["text"] = "## Options\n" + turns["S2.A1"]["text"]
    turns["S2.A2"]["text"] += "\n- one\n- two"
    turns["S2.A3"]["text"] += "\n1. a\n2. b"
    turns["S3.A1"]["text"] += " See you in session 4."
    turns["S3.A2"]["facts"] = ["F99"]
    turns["S3.A3"]["text"] = "ok"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert _codes(spec, path) == {
        "persona_name_present", "reference_fact_id_in_text", "crisis_phrase_present", "pinned_line_mismatch",
        "markdown_header_in_assistant_turn", "too_many_lists_in_session", "test_awareness_cue", "unknown_fact_id",
        "assistant_word_count_out_of_range",
    }


def test_count_lists():
    assert _count_lists("a\n- one\n- two\n\n- three\ntext\n1. x") == 2
    assert _count_lists("no list here") == 0
    assert _count_lists("- a\n  continued\n- b") == 1


def test_freeze_marks_edits_and_writes_diff(spec, drafted):
    draft = drafted / "conversation.draft.yaml"
    edited = drafted / "conversation.v1.0.yaml"
    doc = yaml.safe_load(draft.read_text())
    s1a1 = next(t for t in doc["turns"] if t["id"] == "S1.A1")
    s1a1["text"] = s1a1["text"].replace("That makes sense", "That is fair")
    edited.write_text(yaml.safe_dump(doc, sort_keys=False))
    diff, _ = freeze_conversation(spec, draft, edited, "1.0", "a teammate")
    frozen = load_conversation(edited)
    assert frozen.meta["status"] == "frozen" and frozen.meta["version"] == "1.0"
    assert frozen.turns["S1.A1"].source == "edited" and frozen.turns["S1.A3"].source == "generated"
    assert "draft/S1.A1" in diff.read_text()


def test_render_checkpoints(spec, drafted):
    conv = load_conversation(drafted / "conversation.draft.yaml")
    km5 = render_checkpoint(spec, conv, "KM5")
    headers = [m["content"].split("\n", 1)[0] for m in km5.messages if m["content"].startswith("[")]
    assert headers == [s.header for s in spec.sessions[:3]]
    assert [m["role"] for m in km5.messages] == ["user", "assistant"] * (len(km5.messages) // 2) + ["user"]
    assert km5.messages[-1]["content"] == spec.sessions[2].header + "\n" + spec.checkpoints["KM5"].user_line
    twin = render_checkpoint(spec, conv, "KM7-T")
    base = render_checkpoint(spec, conv, "KM7")
    assert twin.messages[:-1] == base.messages[:-1] and twin.messages[-1]["content"] == spec.checkpoints["KM7-T"].user_line
    push = render_checkpoint(spec, conv, "KM7-P")
    assert push.turn_ids[-3:] == ("S4.U3", "S4.A3", "S4.U4")
    assert push.messages[-3]["content"] == spec.checkpoints["KM7"].user_line
    assert render_checkpoint(spec, conv, "KM5").sha256 == km5.sha256
    assert transcript_text(km5).startswith("user: [Sat, Jan 31, 2026, 11:48 PM]\n")
