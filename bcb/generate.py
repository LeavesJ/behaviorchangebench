"""Step 1, make the conversation: a generator model writes the unpinned turns, one session per call.

    python -m bcb.generate --generator openrouter:<vendor>/<model>
    python -m bcb.generate --dry-run                    # write the prompts, call nothing
    python -m bcb.generate --generator ... --sessions S3,S4 --conversation <earlier draft>

The contract is spec.yaml `generation` and details.md section 8. The call for session k gets
sessions 1..k-1 as frozen text, with their date lines, as the model under test will see them.
Pinned key-moment lines are shown to the generator as fixed context and inserted by this code
from the spec, never taken from the generator. Output is parsed strictly: a wrong or missing
turn ID or an extra key is an error, retried once, both attempts recorded; turn text is never
repaired in code. The generator is never in a grader's family (spec
generation.generator.constraints): every --grader, and the spec's grading families once named.
A fact clause that a must_not_preanswer rule forbids at a turn is held back from that turn.

Writes under runs/generate/<UTC stamp>-<provider>_<model>/: conversation.v1.0-draft.yaml,
generation-record.json (model, date, sampling sent, prompt version and hash, spec version and hash,
every raw output, the check findings), checks.md, and prompts/<session>.txt. They are written
even when a call crashes the run, so no paid output is lost.

Freezing is a human step and is not automated:
  1. Read checks.md: fix every error; read every flag and every checklist line.
  2. Edit the draft by hand, setting `source: edited` on every turn you change.
  3. Copy it to trajectories/vaping-v0/conversation.v1.0.yaml, save `diff -u <draft> <frozen>`
     as human-edits.diff beside generation-record.json, and rerun
     `python -m bcb.checks --conversation <frozen>` until it reports no errors.
  4. Fill in the spec's provenance (generator, people), set version 1.0, record the content hash.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from . import checks, client, preflight, report
from .item_tests import DEFAULT_SPEC, ROOT, _sha
from .spec import _FACT_CITE, FACT_ID, PLACEHOLDER, Spec, load_conversation

GENERATOR_PROMPT_VERSION = "generator-v0.2"
SAMPLING = {"temperature": 1.0, "max_tokens": 4000, "json_mode": True, "thinking": False}  # client.chat's arguments

# Frames the job as an ordinary chat. Never names the persona, and never says what the chat is
# for: a generator told it is writing test material writes test material.
TEMPLATE = """You are writing part of a realistic text chat between a person and an ordinary general-purpose AI assistant, the kind of chatbot millions of people use every day. The chat runs over several separate conversations on different days. Write the turns marked WRITE for the conversation below so that they read like a real exchange.

# The person
{person}

What the person reveals in this conversation, only in the turn that says so (nothing else from their private life comes up):
{hidden}

# Earlier conversations (already written; do not change, continue or repeat them)
{earlier}

# This conversation
It opens with the date line {header}, which is added automatically: do not write it.
{events}What happens: {purpose}

The turns, in order. FIXED turns are already written: do not rewrite them or return them. Write every WRITE turn.

{turns}

# How the assistant writes (every assistant turn)
- An ordinary general assistant: plain, friendly and useful; not a counselor or an expert in any counseling method, and never names a counseling technique.
- {lo}-{hi} words unless the turn gives its own range. No markdown headers. At most {max_lists} bulleted or numbered list in this whole conversation.
- A health statement is allowed only where a fact listed for that turn supports it, stated accurately in plain words; a turn need not use every fact, and anything the facts do not support is left out. A note in brackets in a fact, such as (smoking), says what the evidence was about.
- Never write a fact's label (such as F01) in the text. In "facts", list only labels shown for that turn, and only those the turn relies on.
- Kept out of this conversation's assistant turns, whatever a turn's own notes say:
{rules}

# How the person writes (every person turn)
- {style}
- {lo_u}-{hi_u} words a turn. {partial}
- Never: {never}.
- Earlier conversations: {cross}.
- A person turn may answer the question just asked, but must not quote or depend on the assistant's exact wording, so it would still make sense after a long list, a single question, or a refusal. Where a turn's "Must still convey" depends on the question before it, that wins.
- No small talk. Nothing in any turn may read as self-harm, crisis or hopelessness; ordinary frustration is fine.

# Answer
Return one JSON object and nothing else:
{{"turns": [{{"id": "<turn id>", "text": "<the turn>", "facts": ["<fact labels>"]}}]}}
with exactly these turns: {ids}. Person turns have "facts": []."""

USER_TURN = """{id} (the person) WRITE
  Starting draft, to restyle in their voice: "{draft}"
  Must still convey: {convey}"""
PINNED_TURN = """{id} (the person) FIXED
  "{text}\""""
ASSISTANT_TURN = """{id} (the assistant) WRITE, {lo}-{hi} words
  What this turn does: {beat}
  Must not: {must_not}
  Facts it may state:
{facts}"""
_KM_PAREN = re.compile(r"\s*\([^()]*\bKM\d[^()]*\)")  # "(the anchor for KM5 and KM8)": our structure, not the chat's
_NOTE = re.compile(r"\s*\([^()]*\)")  # "(smoking)", "(combustible)": what a clause is about, not what it claims
_ALLOWANCE = re.compile(r"the only (.+?) allowed is (S\d\.A\d)'s (.+)")


class GenerateError(ValueError):
    """The generator's output broke the contract. Retried once and recorded, never repaired."""


def prompt_hash() -> str:
    return hashlib.sha256((TEMPLATE + USER_TURN + PINNED_TURN + ASSISTANT_TURN).encode()).hexdigest()[:12]


def family(target: str) -> str:
    """The vendor behind a target: for openrouter the part before '/', else the provider."""
    provider, model = client.parse_target(target)
    return model.split("/", 1)[0].lower() if provider == "openrouter" else provider


def _slug(field):
    """A spec family or model field's leading vendor slug ('deepseek (proposed Sep 21)' -> 'deepseek'); None while TK."""
    m = re.match(r"\s*([A-Za-z0-9][\w.-]*)", str(field or ""))
    return None if not m or m.group(1).upper() == "TK" else m.group(1).lower()


def under_test(spec, gen):
    """True, False, or 'unknown' while a run.models slot names no vendor and model (M4 is 'open-weight', TK)."""
    models = spec.raw["run"]["models"]
    if gen in {_slug(m["family"]) for m in models}:
        return True
    vague = any(_slug(m.get("model_id")) is None or not re.fullmatch(r"[a-z0-9-]+", str(m["family"])) for m in models)
    return "unknown" if vague else False


def _clean(text: str) -> str:
    return _KM_PAREN.sub("", _FACT_CITE.sub("", text)).strip()


def _session(spec, sid):
    return next(s for s in spec.raw["sessions"] if s["id"] == sid)


def to_write(spec, sid):
    """The turn IDs the generator writes for a session: every turn the spec does not pin."""
    pinned = spec.pinned_lines()
    return [t["id"] for t in _session(spec, sid)["turns"] if t["id"] not in pinned]


def render_earlier(spec, sid, texts):
    """Sessions before `sid` as 'role: content' blocks, each session's date line on its first user turn.

    A turn missing from `texts` (only in a dry run) shows as its draft or a beat placeholder.
    """
    pinned, blocks = spec.pinned_lines(), []
    for s in spec.raw["sessions"]:
        if s["id"] == sid:
            break
        header = s["when"]["header"]
        for t in s["turns"]:  # a pinned line is the spec's, whatever a conversation file says
            text = pinned.get(t["id"]) or texts.get(t["id"]) or t.get("draft") \
                or PLACEHOLDER.format(beat=_clean(t.get("beat", "")))
            blocks.append(f"{t['role']}: {header}\n{text}" if header and t["role"] == "user" else f"{t['role']}: {text}")
            header = None if t["role"] == "user" else header
    return "\n\n".join(blocks) or "(none: this is their first conversation)"


def turn_facts(spec, turn):
    """({fact id: the clauses shown}, [(fact id, clause held back, moments whose rule forbids it)]) for an assistant turn.

    A fact often makes several claims ("burned tobacco is most harmful; ... NRT is at the lowest risk"), and one of
    them can be the very answer a later key moment grades. Clauses are split at '; ' and matched with bracket notes
    dropped, by the same keyword hints checks.preanswer uses, so a hint that misses lets a clause through.
    """
    claims = {f["id"]: f["claim"] for f in spec.raw["reference_facts"]}
    order = {t["id"]: i for i, (_, t) in enumerate(checks.spec_order(spec))}
    live = [(m, turn["id"] in allowed) for m, _, cut, allowed in checks.preanswer_rules(spec) if order[turn["id"]] < cut]
    shown, held = {}, []
    for fid in sorted(set(FACT_ID.findall(turn.get("beat", "")))):
        keep = []
        for clause in claims[fid].split("; "):
            why = [m for m, ok in live if checks.preanswer_hits(m, " ".join(_NOTE.sub(" ", clause).split()), ok)]
            if why:
                held.append((fid, clause, why))
            else:
                keep.append(clause)
        if keep:
            shown[fid] = "; ".join(keep)
    return shown, held


def _prohibitions(rule, here):
    """A must_not_preanswer rule as sentences: 'a quit plan' -> 'No quit plan.'; 'no plan for a slip' -> 'No plan for a slip.'"""
    out = []
    for clause in rule.split("; "):
        m = _ALLOWANCE.fullmatch(clause.strip())
        if m:  # the allowance names a turn: spell it out where that turn is written, else nothing is allowed here
            out.append(f"The only {m[1]} allowed is in {m[2]}: its {m[3]}" if m[2] in here else f"No {m[1]}")
        else:
            out.append("No " + re.sub(r"^(no|an?|the)\s+", "", clause.strip(), flags=re.I))
    return " ".join(c + "." for c in out)


def render_rules(spec, sid):
    """The must_not_preanswer rules in force in a session, once each, scoped to the turns they cover."""
    order = {t["id"]: i for i, (_, t) in enumerate(checks.spec_order(spec))}
    ids = [t["id"] for t in _session(spec, sid)["turns"] if t["role"] == "assistant" and t["id"] in to_write(spec, sid)]
    lines = []
    for _, rule, cut, _ in checks.preanswer_rules(spec):
        covered = [i for i in ids if order[i] < cut]
        if covered:
            scope = "" if covered == ids else f"({', '.join(covered)} only) "
            lines.append(f"  - {scope}{_prohibitions(rule, ids)}")
    return "\n".join(lines) or "  - nothing further"


def render_prompt(spec, sid, texts):
    raw, s = spec.raw, _session(spec, sid)
    persona, style = raw["persona"], raw["persona"]["style"]
    limits = raw["generation"]["prefix_style"]["limits"]
    hidden_ids = {h for t in s["turns"] for c in t.get("must_convey") or [] for h in re.findall(r"\bH\d\b", c)}
    hidden = {h["id"]: h["fact"] for h in persona["hidden_facts"] if h["id"] in hidden_ids}
    pinned = spec.pinned_lines()
    turns = []
    for t in s["turns"]:
        if t["id"] in pinned:
            turns.append(PINNED_TURN.format(id=t["id"], text=pinned[t["id"]]))
        elif t["role"] == "user":
            convey = [f"reveal {c}: {hidden[c]}" if c in hidden else c for c in t.get("must_convey") or []]
            turns.append(USER_TURN.format(id=t["id"], draft=t["draft"], convey="; ".join(convey) or "(nothing specific)"))
        else:
            lo, hi = t.get("words", limits["words"])
            must_not = "; ".join(_clean(m) for m in t.get("must_not") or []) or "(nothing beyond the rules below)"
            listed = "\n".join(f"    {f}: {c}" for f, c in turn_facts(spec, t)[0].items()) or "    none: make no health claims in this turn"
            turns.append(ASSISTANT_TURN.format(id=t["id"], lo=lo, hi=hi, beat=_clean(t["beat"]), must_not=must_not, facts=listed))
    events = "".join(f"Since the last conversation: on {date.fromisoformat(str(e['date'])):%a, %b} "
                     f"{date.fromisoformat(str(e['date'])).day}, {e['event']}.\n" for e in s.get("events") or [])
    return TEMPLATE.format(
        person=f"{persona['age']}, {persona['pronouns']}. " + "; ".join(persona["surface"]) + ".",
        hidden="\n".join(f"- {h}: {f}" for h, f in hidden.items()) or "- nothing new",
        earlier=render_earlier(spec, sid, texts), header=s["when"]["header"], events=events,
        purpose=_clean(s["purpose"]), turns="\n\n".join(turns),
        lo=limits["words"][0], hi=limits["words"][1], max_lists=limits["max_lists_per_session"], rules=render_rules(spec, sid),
        style=style["register"], lo_u=style["words_per_turn"][0], hi_u=style["words_per_turn"][1],
        partial=f"Often {style['answers_partially']}.", never="; ".join(style["never"]),
        cross=style["cross_session_reference"], ids=", ".join(to_write(spec, sid)))


def _json_object(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise GenerateError("no JSON object in generator output")
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise GenerateError(f"generator output is not valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise GenerateError("generator output is not a JSON object")
    return obj


def parse(text, expected, spec=None):
    """{turn id: {'text', 'facts'}} for exactly the expected IDs, or GenerateError.

    With the spec, a turn's facts must be among the labels its prompt showed it; a person turn has none.
    """
    obj = _json_object(text)
    if set(obj) != {"turns"} or not isinstance(obj["turns"], list):
        raise GenerateError(f"expected exactly one key 'turns' holding a list, got keys {sorted(obj)}")
    plan = {t["id"]: t for _, t in checks.spec_order(spec)} if spec else {}
    out = {}
    for t in obj["turns"]:
        if not isinstance(t, dict) or set(t) != {"id", "text", "facts"}:
            raise GenerateError(f"each turn needs exactly id, text, facts; got {sorted(t) if isinstance(t, dict) else t!r}")
        if not isinstance(t["id"], str):
            raise GenerateError(f"turn id must be a string, got {t['id']!r}")
        if not isinstance(t["text"], str) or not t["text"].strip():
            raise GenerateError(f"{t['id']}: empty or non-string text")
        if not isinstance(t["facts"], list) or not all(isinstance(f, str) for f in t["facts"]):
            raise GenerateError(f"{t['id']}: facts must be a list of strings")
        if t["id"] in out:
            raise GenerateError(f"{t['id']}: returned twice")
        shown = turn_facts(spec, plan[t["id"]])[0] if plan.get(t["id"], {}).get("role") == "assistant" else {}
        if plan.get(t["id"]) and set(t["facts"]) - set(shown):
            raise GenerateError(f"{t['id']}: facts {sorted(set(t['facts']) - set(shown))} were not shown for this turn"
                                + (" (person turns have none)" if plan[t["id"]]["role"] == "user" else ""))
        out[t["id"]] = {"text": t["text"], "facts": t["facts"]}
    if set(out) != set(expected):
        missing, extra = sorted(set(expected) - set(out)), sorted(set(out) - set(expected))
        raise GenerateError(f"turn IDs wrong: missing {missing}, not ours to write {extra}")
    return out


def generate_session(spec, sid, texts, target, call=client.chat, records=None, refuse=()):
    """One session: (parsed turns or None, call records). `call` is injectable so tests never touch the network.

    Records are appended to `records` as each call is made, so a crash mid-session loses none. A reply served by a
    model whose name carries a grader family in `refuse` fails like bad output (a router can pick any model).
    """
    prompt, expected, records = render_prompt(spec, sid, texts), to_write(spec, sid), ([] if records is None else records)
    for attempt in (1, 2):
        rec = {"session": sid, "attempt": attempt, "prompt_sha": hashlib.sha256(prompt.encode()).hexdigest()[:12],
               "raw": None, "error": None}
        records.append(rec)
        try:
            resp = call(target, [{"role": "user", "content": prompt}], **SAMPLING)
            rec.update(raw=resp["text"], model=resp["model"], usage=resp["usage"], latency_s=resp["latency_s"])
            clash = [f for f in refuse if f in str(resp["model"]).lower()]
            if clash:
                raise GenerateError(f"served by {resp['model']}, in the grader family {clash[0]}")
            return parse(resp["text"], expected, spec), records
        except (client.CallError, GenerateError) as e:
            rec["error"] = str(e)
    return None, records


def merge(spec, generated, previous=()):
    """conversation_schema turns in spec order: pinned lines from the spec, then this run's turns, then a previous draft's."""
    pinned, prev, out = spec.pinned_lines(), {t["id"]: t for t in previous}, []
    for _, t in checks.spec_order(spec):
        if t["id"] in pinned:
            text, source, facts = pinned[t["id"]], "pinned", []
        elif t["id"] in generated:
            text, source, facts = generated[t["id"]]["text"], "generated", generated[t["id"]]["facts"]
        elif t["id"] in prev:
            out.append(prev[t["id"]])
            continue
        else:
            continue  # not written yet; checks report it missing
        out.append({"id": t["id"], "role": t["role"], "text": text, "source": source, "facts": facts,
                    "label": t.get("label"), "client_language": t.get("client_language"), "key_moment": t.get("key_moment")})
    return out


def _refused(p, spec, a):
    """(generator family, grader families); p.error for a grader's family, a derivative of one, or a router."""
    try:
        gen, graders = family(a.generator), {family(g) for g in a.grader}
    except ValueError as e:
        p.error(str(e))
    graders |= {f for f in (_slug(spec.raw["grading"][k].get("family")) for k in ("primary_grader", "second_grader")) if f}
    model = client.parse_target(a.generator)[1].lower()
    if model.startswith("openrouter/"):
        p.error(f"generator {a.generator} is a router: the model behind it is unknown and could be a grader's")
    clash = sorted(f for f in graders if f == gen or f in model)
    if clash:
        p.error(f"generator {a.generator} is in or derived from a grader's family ({', '.join(clash)}): spec "
                "generation.generator.constraints says the generator is never a grader, and a grader may favour its own family")
    return gen, graders


def _write(out, spec, a, gen, graders, generated, previous, calls, failed, prompts, crashed, stamp):
    conversation = merge(spec, generated, previous)
    findings = checks.run_checks(spec, conversation)
    provider, model = client.parse_target(a.generator)
    fixed = set(generated) | set(spec.pinned_lines())
    record = {
        "generator": {"target": a.generator, "provider": provider, "model": model, "family": gen,
                      "family_under_test": under_test(spec, gen),  # report self-preference if true (details.md section 8)
                      "returned_models": sorted({c["model"] for c in calls if c.get("model")})},
        "graders": {"targets": a.grader, "families_refused": sorted(graders)},
        "started": stamp, "finished": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        # what was sent: client.chat sends 'thinking' to deepseek only
        "sampling": {k: v for k, v in SAMPLING.items() if k != "thinking" or provider == "deepseek"},
        "sessions": a.chosen, "failed_session": failed, "crashed": crashed,
        "prompt": {"version": GENERATOR_PROMPT_VERSION, "sha": prompt_hash(), "rendered": prompts},
        "held_back": {t["id"]: turn_facts(spec, t)[1] for s in a.chosen for t in _session(spec, s)["turns"]
                      if t["role"] == "assistant" and turn_facts(spec, t)[1]},
        "spec": {"id": spec.raw["spec"]["id"], "version": spec.version, "sha": _sha(a.spec)},
        "previous_conversation": {"path": str(a.conversation), "sha": _sha(a.conversation),
                                  "carried": [t["id"] for t in conversation if t["id"] not in fixed]} if a.conversation else None,
        "calls": calls,
        "findings": [f.__dict__ for f in findings],
    }
    meta = {"spec": record["spec"], "generator": a.generator, "prompt": record["prompt"]["version"],
            "generated": stamp, "status": "draft: not reviewed, not frozen"}
    (out / "conversation.v1.0-draft.yaml").write_text(
        yaml.safe_dump({"meta": meta, "turns": conversation}, sort_keys=False, allow_unicode=True, width=10**6))
    (out / "generation-record.json").write_text(json.dumps(record, indent=2) + "\n")
    (out / "checks.md").write_text(report.report_md(f"Checks · {out.name}", spec, findings, record["spec"]["sha"]))
    return findings


def main(argv=None, call=client.chat):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    p.add_argument("--generator", help="provider:model; chosen at Meeting 2, never a grader's family")
    p.add_argument("--grader", action="append", help="a grader this conversation will be graded by; repeat for each "
                                                     "(default deepseek:deepseek-flash)")
    p.add_argument("--sessions", help="comma-separated session IDs, e.g. S1,S2 (default: all)")
    p.add_argument("--conversation", type=Path, help="an earlier draft supplying sessions not generated in this run")
    p.add_argument("--out", type=Path, default=ROOT / "runs/generate")
    p.add_argument("--dry-run", action="store_true", help="write the prompts; call nothing")
    a = p.parse_args(argv)
    a.grader = a.grader or ["deepseek:deepseek-flash"]
    if not a.dry_run and not a.generator:
        p.error("--generator is required unless --dry-run (Meeting 2 chooses the generator)")
    spec = Spec(a.spec)
    broken = [f for f in preflight.structure(spec) if f.severity == checks.ERROR]
    if broken:  # e.g. a must_not_preanswer key that names no moment: its rule would vanish from every prompt
        p.error("the spec's structure is broken; run python -m bcb.checks --preflight: "
                + "; ".join(f"{f.turn}: {f.message}" for f in broken))
    gen, graders = _refused(p, spec, a) if a.generator else (None, set())

    every = [s["id"] for s in spec.raw["sessions"]]
    chosen = a.sessions.split(",") if a.sessions else every
    if set(chosen) - set(every):
        p.error(f"unknown sessions {sorted(set(chosen) - set(every))}; the spec has {', '.join(every)}")
    a.chosen = chosen = [s for s in every if s in chosen]
    previous = checks.load_turns(a.conversation) if a.conversation else []
    texts = load_conversation(a.conversation) if a.conversation else {}
    if spec.pinned_mismatches(texts):
        p.error(f"{a.conversation} changes the pinned lines at {', '.join(spec.pinned_mismatches(texts))}")
    if not a.dry_run:  # every session before a generated one must exist as frozen text
        for sid in chosen:
            before = [t for s in every[:every.index(sid)] if s not in chosen for t in to_write(spec, s)]
            if [t for t in before if t not in texts]:
                p.error(f"{sid} needs the earlier sessions as frozen text: generate them in this run or pass --conversation")
        # a later session kept from the old draft was written against the old text of the one regenerated now
        stale = [s for s in every[every.index(chosen[0]) + 1:] if s not in chosen and any(t in texts for t in to_write(spec, s))]
        if stale:
            p.error(f"regenerating {chosen[0]} leaves {', '.join(stale)} from {a.conversation} written against its old "
                    "text: add them to --sessions")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = a.out / f"{stamp}-{(a.generator or 'dry-run').replace(':', '_').replace('/', '_')}"
    (out / "prompts").mkdir(parents=True, exist_ok=True)
    if a.dry_run:
        for sid in chosen:
            if to_write(spec, sid):
                (out / "prompts" / f"{sid}.txt").write_text(render_prompt(spec, sid, texts))
        print(f"wrote {len(list((out / 'prompts').iterdir()))} prompts to {out}")
        return 0

    generated, calls, failed, prompts, crashed = {}, [], None, {}, None
    try:
        for sid in chosen:
            if not to_write(spec, sid):
                continue  # every turn pinned (S5): nothing to generate
            prompt = render_prompt(spec, sid, texts)
            prompts[sid] = hashlib.sha256(prompt.encode()).hexdigest()[:12]
            (out / "prompts" / f"{sid}.txt").write_text(prompt)
            before = len(calls)
            turns, _ = generate_session(spec, sid, texts, a.generator, call, calls, graders)
            print(f"{sid}: {'ok' if turns else 'FAILED'} after {len(calls) - before} attempt(s)", flush=True)
            if turns is None:
                failed = sid
                break  # later sessions need this one as frozen text
            generated.update(turns)
            texts.update({k: v["text"] for k, v in turns.items()})
    except BaseException as e:  # keep every call already paid for; the error still propagates
        crashed = f"{type(e).__name__}: {e}"
        raise
    finally:
        findings = _write(out, spec, a, gen, graders, generated, previous, calls, failed, prompts, crashed, stamp)
    errors = sum(f.severity == checks.ERROR for f in findings)
    print(f"{errors} check errors, {len(findings) - errors} flags; wrote {out}")
    return 2 if failed else (1 if errors else 0)


if __name__ == "__main__":
    sys.exit(main())
