"""Step 1: the conversation file. Draft it with a generator model, check it, freeze it.

    conversation.draft.yaml   written by `draft`, never edited by hand
    conversation.v1.0.yaml    a copy people edit; `freeze` stamps it and writes edits.diff

`check` runs every hard constraint in details.md section 8 that a script can decide, and prints
the ones only a person can. Pinned key-moment lines always come from the spec, byte for byte.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .errors import DataError
from .providers import STOP_END, Provider
from .spec import Spec, Turn
from .util import (
    dump_yaml,
    load_yaml,
    sha256_bytes,
    sha256_file,
    sha256_json,
    utc_now,
    word_count,
    write_text_atomic,
)

SCHEMA = "bcb.conversation/1"
SOURCES = ("pinned", "generated", "edited")
_TOP_KEYS = {"conversation", "turns"}
_META_KEYS = {
    "schema", "spec_id", "spec_version", "spec_sha256", "version", "status", "generator_model_id",
    "generation_record", "draft_sha256", "realism_reviewer", "frozen_at", "content_sha256",
}
_TURN_KEYS = {"id", "role", "source", "label", "client_language", "key_moment", "facts", "text"}

# Checks that only a person can make (details.md section 8). Printed by `check`, never skipped.
MANUAL_CHECKS = (
    "Realism read by a teammate who wrote none of it: length, register, a question left unanswered, "
    "pushback present, nothing that reads like a vignette",
    "Response-robustness read for every user turn (reads naturally after a long list, one question, a refusal)",
    "Every prefix beat checked against every later key moment's criteria (no-giveaway rules)",
    "Every health claim in an assistant turn maps to a listed reference fact, or is cut",
    "Hidden facts appear only where the reveal table says; each user turn keeps its must_convey",
    "Elapsed time in her words agrees with the date lines (\"3 days in\", about 11 days, \"almost 3 weeks\")",
    "Crisis-phrase screen read by a human as well as the script",
)

MI_JARGON = (
    "change talk", "sustain talk", "motivational interviewing", "decisional balance", "righting reflex",
    "rolling with resistance", "elicit-provide-elicit", "self-efficacy", "precontemplation",
    "stages of change", "implementation intention", "abstinence violation",
)
_TEST_CUES = (
    re.compile(r"\bsession\s*#?\s*[1-9]\b", re.I),
    re.compile(r"\b(benchmark|evaluation|this is a test|test scenario|simulated user|role-?play)\b", re.I),
    re.compile(r"\bTK\b"),
    re.compile(r"\[(name|user|user name|placeholder)\]", re.I),
    re.compile(r"\{[a-z_]+\}"),
)
_FACT_REF = re.compile(r"\bF\d\d\b")
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s", re.M)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+•]|\d{1,2}[.)])\s+\S")


@dataclass(frozen=True)
class ConvTurn:
    id: str
    role: str
    source: str
    label: str | None
    client_language: tuple[str, ...]
    key_moment: str | None
    facts: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class Conversation:
    path: Path
    sha256: str
    meta: dict[str, Any]
    turns: dict[str, ConvTurn]
    order: tuple[str, ...]

    def text(self, turn_id: str) -> str:
        return self.turns[turn_id].text


@dataclass(frozen=True)
class Finding:
    severity: str  # error | warning
    code: str
    where: str
    detail: str

    def __str__(self) -> str:
        return f"{self.severity:7} {self.code:38} {self.where:8} {self.detail}"


def load_conversation(path: str | Path) -> Conversation:
    path = Path(path)
    raw = load_yaml(path)
    where = str(path)
    if not isinstance(raw, dict) or set(raw) != _TOP_KEYS:
        raise DataError(f"{where}: top level must have exactly {sorted(_TOP_KEYS)}")
    meta = raw["conversation"]
    if not isinstance(meta, dict) or set(meta) - _META_KEYS:
        raise DataError(f"{where}: `conversation` has unknown keys {sorted(set(meta or {}) - _META_KEYS)}")
    if meta.get("schema") != SCHEMA:
        raise DataError(f"{where}: conversation.schema must be {SCHEMA}")
    turns: dict[str, ConvTurn] = {}
    order: list[str] = []
    for n, t in enumerate(raw["turns"] or []):
        if not isinstance(t, dict):
            raise DataError(f"{where}: turns[{n}] is not a mapping")
        extra = set(t) - _TURN_KEYS
        missing = {"id", "role", "source", "text"} - set(t)
        if extra or missing:
            raise DataError(f"{where}: turns[{n}]: unknown keys {sorted(extra)}, missing keys {sorted(missing)}")
        if not isinstance(t["text"], str):
            raise DataError(f"{where}: {t['id']}: text must be a string")
        if t["source"] not in SOURCES:
            raise DataError(f"{where}: {t['id']}: source {t['source']!r} not in {SOURCES}")
        if t["id"] in turns:
            raise DataError(f"{where}: duplicate turn {t['id']}")
        turns[t["id"]] = ConvTurn(
            id=t["id"], role=t["role"], source=t["source"], label=t.get("label"),
            client_language=tuple(t.get("client_language") or ()), key_moment=t.get("key_moment"),
            facts=tuple(t.get("facts") or ()), text=t["text"],
        )
        order.append(t["id"])
    return Conversation(path, sha256_file(path), meta, turns, tuple(order))


# -- check ---------------------------------------------------------------------------------------


def check_conversation(spec: Spec, conv: Conversation) -> list[Finding]:
    out: list[Finding] = []

    def add(severity: str, code: str, where: str, detail: str) -> None:
        out.append(Finding(severity, code, where, detail))

    meta = conv.meta
    if meta.get("spec_id") != spec.id:
        add("error", "spec_id_mismatch", "meta", f"conversation is for {meta.get('spec_id')!r}, spec is {spec.id!r}")
    if str(meta.get("spec_version")) != spec.version:
        add("warning", "spec_version_mismatch", "meta",
            f"written against spec {meta.get('spec_version')}, spec is now {spec.version}; re-read the changed beats")
    if conv.order != spec.turn_order:
        add("error", "turn_ids_mismatch", "meta", f"turn IDs must be exactly the spec's, in order: {list(spec.turn_order)}")

    names = [spec.persona_name, *spec.persona_name.split()]
    name_re = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b", re.I)
    lists_per_session: dict[str, int] = {}
    limits = spec.prefix_style["limits"]
    user_words = (spec.raw["persona"].get("style") or {}).get("words_per_turn")

    for tid in conv.order:
        t = conv.turns[tid]
        st = spec.turns.get(tid)
        if st is None:
            continue
        if t.role != st.role:
            add("error", "role_mismatch", tid, f"role {t.role!r}, spec says {st.role!r}")
        if not t.text.strip():
            add("error", "empty_text", tid, "turn has no text")
            continue
        if st.pinned:
            line = spec.checkpoints[st.key_moment].user_line
            if t.text != line:
                add("error", "pinned_line_mismatch", tid, f"must equal {st.key_moment}.user_line byte for byte")
            if t.source != "pinned":
                add("error", "source_mismatch", tid, "a pinned turn has source `pinned`")
        elif t.source == "pinned":
            add("error", "source_mismatch", tid, "only key-moment turns have source `pinned`")
        if t.label != st.label or list(t.client_language) != list(st.client_language) or t.key_moment != st.key_moment:
            add("error", "tags_mismatch", tid, "label, client_language and key_moment must equal the spec's")
        unknown = [f for f in t.facts if f not in spec.reference_facts]
        if unknown:
            add("error", "unknown_fact_id", tid, f"facts {unknown} are not reference facts")
        if t.role == "user" and t.facts:
            add("error", "facts_on_user_turn", tid, "user turns cite no facts")
        _screen_text(spec, tid, t.text, name_re, add)
        n = word_count(t.text)
        if t.role == "assistant":
            lo, hi = st.words or tuple(limits["words"])
            if not lo <= n <= hi:
                add("error", "assistant_word_count_out_of_range", tid, f"{n} words, allowed {lo}-{hi}")
            if not limits.get("markdown_headers", False) and _MD_HEADER.search(t.text):
                add("error", "markdown_header_in_assistant_turn", tid, "no markdown headers")
            if not limits.get("mi_jargon", False):
                hits = [j for j in MI_JARGON if j in t.text.lower()]
                if hits:
                    add("error", "mi_jargon_in_assistant_turn", tid, f"jargon {hits}")
            lists_per_session[st.session] = lists_per_session.get(st.session, 0) + _count_lists(t.text)
        elif not st.pinned and user_words and not user_words[0] <= n <= user_words[1]:
            add("warning", "user_word_count_out_of_range", tid,
                f"{n} words, persona style says {user_words[0]}-{user_words[1]} (an occasional vent is allowed)")

    max_lists = limits.get("max_lists_per_session")
    for sid, count in lists_per_session.items():
        if max_lists is not None and count > max_lists:
            add("error", "too_many_lists_in_session", sid, f"{count} lists, allowed {max_lists}")

    for cp in spec.checkpoints.values():  # variant lines are model-visible too
        if cp.variant_of is not None:
            _screen_text(spec, cp.id, cp.user_line, name_re, add)
    for kid, line in ((spec.raw["run"].get("sensitivity_variants") or {}).get("paraphrase") or {}).items():
        _screen_text(spec, f"{kid}~para", line, name_re, add)
    return out


def _screen_text(spec: Spec, where: str, text: str, name_re: re.Pattern, add) -> None:
    if name_re.search(text):
        add("error", "persona_name_present", where, f"contains {name_re.search(text).group(0)!r}")
    if _FACT_REF.search(text):
        add("error", "reference_fact_id_in_text", where, f"contains {_FACT_REF.search(text).group(0)!r}")
    low = text.lower()
    for phrase in spec.crisis_phrases:
        if phrase.lower() in low:
            add("error", "crisis_phrase_present", where, f"contains {phrase!r}")
    for cue in _TEST_CUES:
        m = cue.search(text)
        if m:
            add("error", "test_awareness_cue", where, f"contains {m.group(0)!r}")


def _count_lists(text: str) -> int:
    """Counts list blocks. Blank lines and indented continuation lines stay inside a block."""
    blocks, in_list = 0, False
    for line in text.splitlines():
        if not line.strip():
            continue
        if _LIST_ITEM.match(line):
            blocks += 0 if in_list else 1
            in_list = True
        else:
            in_list = in_list and line[:1] in (" ", "\t")
    return blocks


# -- draft ----------------------------------------------------------------------------------------


def generator_payload(spec: Spec, session_index: int, earlier: dict[str, str]) -> dict[str, Any]:
    """The one JSON object the generator sees for one session. Earlier sessions are frozen text."""
    session = spec.sessions[session_index]
    persona = spec.raw["persona"]
    limits = spec.prefix_style["limits"]
    user_words = (persona.get("style") or {}).get("words_per_turn")
    turns: list[dict[str, Any]] = []
    for tid in session.turn_ids:
        t = spec.turns[tid]
        if t.pinned:
            turns.append({"id": tid, "role": "user", "pinned": True, "text": spec.checkpoints[t.key_moment].user_line})
        elif t.role == "user":
            turns.append({"id": tid, "role": "user", "write": True, "draft": t.draft,
                          "must_convey": list(t.must_convey), "words": user_words})
        else:
            turns.append({"id": tid, "role": "assistant", "write": True, "beat": t.beat,
                          "must_not": list(t.must_not), "words": list(t.words or limits["words"])})
    return {
        "persona": {"age": persona.get("age"), "pronouns": persona.get("pronouns"),
                    "surface": persona.get("surface"), "style": persona.get("style"),
                    "answer_changing_facts": persona.get("answer_changing_facts"),
                    "non_goals": persona.get("non_goals")},
        "prefix_style": spec.prefix_style,
        "no_giveaway_rules": _giveaway_rules(spec, session),
        "reference_facts": spec.reference_facts,
        "earlier_sessions": [
            {"date_line": s.header,
             "turns": [{"role": spec.turns[i].role, "text": earlier[i]} for i in s.turn_ids]}
            for s in spec.sessions[:session_index]
        ],
        "this_session": {"date_line": session.header, "purpose": session.purpose, "turns": turns},
    }


def _giveaway_rules(spec: Spec, session) -> dict[str, str]:
    """must_not_preanswer rules that bind at least one assistant turn in this session."""
    rules = spec.prefix_style.get("must_not_preanswer") or {}
    by_compact = {k.replace("-", ""): k for k in spec.checkpoints}
    out: dict[str, str] = {}
    for key, rule in rules.items():
        kid = by_compact.get(key.removeprefix("before_"))
        if kid is None:
            raise DataError(f"prefix_style.must_not_preanswer.{key}: no key moment {key.removeprefix('before_')}")
        limit = spec.turn_order.index(spec.checkpoints[kid].turn_id)
        if any(spec.turns[t].role == "assistant" and spec.turn_order.index(t) < limit for t in session.turn_ids):
            out[f"before {kid}"] = rule
    return out


def draft_conversation(spec: Spec, provider: Provider, prompt_path: Path, out_path: Path,
                       record_path: Path, generator_family_under_test: bool) -> list[Finding]:
    prompt = prompt_path.read_text(encoding="utf-8")
    texts: dict[str, str] = {}
    facts: dict[str, list[str]] = {}
    sources: dict[str, str] = {}
    record: dict[str, Any] = {
        "schema": "bcb.generation_record/1",
        "spec": {"id": spec.id, "version": spec.version, "sha256": spec.sha256},
        "generator": provider.config.describe(),
        "generator_family_under_test": generator_family_under_test,
        "prompt": {"path": str(prompt_path), "sha256": sha256_bytes(prompt.encode("utf-8"))},
        "started_at": utc_now(),
        "sessions": [],
    }
    try:
        for k, session in enumerate(spec.sessions):
            payload = generator_payload(spec, k, texts)
            messages = [{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)}]
            completion = provider.complete(messages, prompt)
            entry = {"session": session.id, "request_sha256": sha256_json(messages), "raw_output": completion.text,
                     "stop": completion.stop, "raw_stop": completion.raw_stop, "response_model": completion.response_model,
                     "input_tokens": completion.input_tokens, "output_tokens": completion.output_tokens}
            record["sessions"].append(entry)
            if completion.stop != STOP_END:
                raise DataError(f"generator stopped with {completion.stop} ({completion.raw_stop}) on {session.id}")
            written = _parse_generated(spec, session.turn_ids, completion.text)
            for tid in session.turn_ids:
                t = spec.turns[tid]
                if t.pinned:
                    texts[tid], facts[tid], sources[tid] = spec.checkpoints[t.key_moment].user_line, [], "pinned"
                else:
                    texts[tid], facts[tid], sources[tid] = written[tid]["text"], written[tid]["facts"], "generated"
    except Exception as e:
        record["error"] = f"{type(e).__name__}: {e}"
        write_text_atomic(record_path, json.dumps(record, ensure_ascii=False, indent=1) + "\n")
        raise
    meta = {
        "schema": SCHEMA, "spec_id": spec.id, "spec_version": spec.version, "spec_sha256": spec.sha256,
        "version": "draft", "status": "draft", "generator_model_id": provider.config.model_id,
        "generation_record": record_path.name,
    }
    doc = {"conversation": meta, "turns": [_turn_doc(spec.turns[t], sources[t], facts[t], texts[t]) for t in spec.turn_order]}
    write_text_atomic(out_path, dump_yaml(doc))
    findings = check_conversation(spec, load_conversation(out_path))
    record["finished_at"] = utc_now()
    record["output"] = {"path": str(out_path), "sha256": sha256_file(out_path)}
    record["check_findings"] = [f.__dict__ for f in findings]
    write_text_atomic(record_path, json.dumps(record, ensure_ascii=False, indent=1) + "\n")
    return findings


def _turn_doc(t: Turn, source: str, facts: Sequence[str], text: str) -> dict[str, Any]:
    return {"id": t.id, "role": t.role, "source": source, "label": t.label,
            "client_language": list(t.client_language), "key_moment": t.key_moment,
            "facts": list(facts), "text": text}


def _parse_generated(spec: Spec, turn_ids: Sequence[str], raw: str) -> dict[str, dict[str, Any]]:
    try:
        obj = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as e:
        raise DataError(f"generator output is not JSON: {e}") from e
    if not isinstance(obj, dict) or set(obj) != {"turns"} or not isinstance(obj["turns"], list):
        raise DataError('generator output must be {"turns": [...]}')
    want = [t for t in turn_ids if not spec.turns[t].pinned]
    got = [t.get("id") if isinstance(t, dict) else None for t in obj["turns"]]
    if got != want:
        raise DataError(f"generator wrote turns {got}, expected exactly {want}")
    out: dict[str, dict[str, Any]] = {}
    for t in obj["turns"]:
        if set(t) != {"id", "text", "facts"} or not isinstance(t["text"], str) or not isinstance(t["facts"], list):
            raise DataError(f"generator turn {t.get('id')}: needs exactly id, text (string), facts (list)")
        out[t["id"]] = {"text": t["text"].strip(), "facts": [str(f) for f in t["facts"]]}
    return out


def _strip_fence(raw: str) -> str:
    """Tolerates exactly one enclosing markdown code fence; anything else must be bare JSON."""
    text = raw.strip()
    m = re.fullmatch(r"```(?:json)?\n(.*)\n```", text, re.S)
    return m.group(1) if m else text


# -- freeze ---------------------------------------------------------------------------------------


def freeze_conversation(spec: Spec, draft_path: Path, edited_path: Path, version: str,
                        realism_reviewer: str) -> tuple[Path, list[Finding]]:
    if not realism_reviewer.strip():
        raise DataError("freeze needs --realism-reviewer (someone who wrote none of the turns)")
    draft = load_conversation(draft_path)
    edited = load_conversation(edited_path)
    findings = check_conversation(spec, edited)
    errors = [f for f in findings if f.severity == "error"]
    if errors:
        raise DataError("cannot freeze with check errors:\n" + "\n".join(map(str, errors)))
    diff: list[str] = []
    turns = []
    for tid in edited.order:
        t = edited.turns[tid]
        before = draft.turns[tid].text if tid in draft.turns else ""
        source = t.source
        if source != "pinned" and t.text != before:
            source = "edited"
            diff.extend(difflib.unified_diff(before.splitlines(), t.text.splitlines(),
                                             f"draft/{tid}", f"{version}/{tid}", lineterm=""))
        turns.append(_turn_doc(spec.turns[tid], source, t.facts, t.text))
    meta = dict(edited.meta)
    meta.update({
        "spec_version": spec.version, "spec_sha256": spec.sha256, "version": version, "status": "frozen",
        "draft_sha256": draft.sha256, "realism_reviewer": realism_reviewer.strip(), "frozen_at": utc_now(),
        "content_sha256": sha256_json([[t["id"], t["text"]] for t in turns]),
    })
    write_text_atomic(edited_path, dump_yaml({"conversation": meta, "turns": turns}))
    diff_path = edited_path.with_name(f"edits.{version}.diff")
    write_text_atomic(diff_path, "\n".join(diff) + ("\n" if diff else ""))
    return diff_path, findings
