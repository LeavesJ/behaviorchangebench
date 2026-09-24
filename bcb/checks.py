"""Check a step-1 conversation, and the spec it is made from, before a person freezes it.

The automatable part of spec `generation` and of the review checklist in details.md section 8.
Every finding has a severity:

- error: the draft cannot be frozen as it is (a pinned line changed, the persona's name, a
  fact ID in text, a crisis phrase, a style limit broken, ...).
- flag: a person must look. A keyword heuristic, never a verdict: a hit is not a violation
  and no hit is not a pass.

The per-turn `must_not` and `must_convey` lists and the rest of section 8 cannot be checked
by a script; they come out as a checklist for a human read (bcb/report.py).

    python -m bcb.checks --preflight           # the spec itself (bcb/preflight.py): dates, structure, pinned lines, drafts
    python -m bcb.checks --conversation runs/generate/<run>/conversation.v1.0-draft.yaml
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from .spec import FACT_ID, Spec

ERROR, FLAG = "error", "flag"
LEFTOVERS = [  # what a template, a placeholder or a transcript format leaves behind
    (re.compile(re.escape("[Draft placeholder")), "a draft placeholder"),
    (re.compile(r"\bsession\s+\d", re.I), "a session label (test-awareness cue)"),
    (re.compile(r"\bTK\b"), "'TK', the spec's open-choice marker"),
    (re.compile(r"[{}]"), "a template brace"),
    (re.compile(r"\b(user|assistant|person)\s*:|\(the (person|assistant)\)", re.I), "a transcript role label"),
    # what the generator prompt itself shows the model
    (re.compile(r"\bS\d\.[UA]\d\b"), "a turn ID"),
    (re.compile(r"\bH\d\b"), "a hidden-fact ID"),
    (re.compile(r"\bKM\d"), "a key-moment ID"),
    (re.compile(r"\b(WRITE|FIXED)\b"), "a generator-prompt marker"),
    (re.compile(r"^\s*\[(Mon|Tue|Wed|Thu|Fri|Sat|Sun), ", re.M), "a date line (added at render time, never stored)"),
]
# MI's own names for its techniques and constructs (MITI 4.2.1, TIP 35 ch. 3). An ordinary
# assistant may use the techniques; naming them is what reads as an expert prefix. Plain words
# MI also uses (reflect, mixed feelings, ambivalent, motivation) are left out on purpose.
# abstinence-violation is relapse prevention's term, not MI's, but F14 hands it to the generator.
MI_JARGON = re.compile(r"\b(motivational_interview\w*|change_talk|sustain_talk|decisional_balance|righting_reflex|"
                       r"roll(ing)?_with_resistance|elicit_provide_elicit|develop(ing)?_discrepancy|"
                       r"(importance|confidence|readiness)_rulers?|stages?_of_change|pre-?contemplation|reflective_listening|"
                       r"abstinence_violation)\b".replace("_", r"[-\s\u2013\u2014]+"), re.I)
QUOTING = re.compile(r"\b(like|as) (you|u) (said|mentioned|put it|suggested|were saying)\b|\b(you|u) (said|mentioned|told me)\b", re.I)
HEADER = re.compile(r"^\s{0,3}#{1,6}\s", re.M)
LIST_ITEM = re.compile(r"^\s*([-*+•\u2013]|\d+[.)]|\(?[a-z]\))\s+")
# flags only
HEALTH = re.compile(r"\b(nicotine|addict\w*|withdrawal|brain|lungs?|heart|cancer|harm\w*|risk\w*|safe|unsafe|toxi\w*|"
                    r"chemicals?|patch(es)?|gum|lozenges?|dos(e|es|age)|mg|FDA|CDC|stud(y|ies)|evidence|research|"
                    r"percent|quit rates?|cravings?)\b|%", re.I)
_NUM = r"(\d+|a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a few|a couple( of)?|several)"
TIME = re.compile(r"\b" + _NUM + r"\s+(days?|weeks?|months?|nights?|years?|hours?)\b|\bday " + _NUM + r"\b|"
                  r"\b\d+-(day|week|month|night|year|hour)s?\b|\b(yesterday|tomorrow|tmrw|tmr|tonight|today|the other day|"
                  r"last (night|week|weekend|spring|semester|month|year)|this (week|weekend|morning|afternoon|evening)|"
                  r"next (week|weekend)|all weekend|over break)\b|\b(mon|tues|wednes|thurs|fri|satur|sun)days?\b|"
                  r"\b(mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)\b|\b(jan|feb|mar)[a-z]* \d{1,2}\b|"
                  r"\bthe \d{1,2}(st|nd|rd|th)\b", re.I)
CLINICAL = re.compile(r"\b(withdrawal|nicotine replacement|NRT|cessation|abstinen\w*|relapse\w*|triggers?|ambivalen\w*|"
                      r"symptoms?|dependen(ce|t|cy)|self-efficacy|motivation|mg)\b", re.I)
TIDY = re.compile(r"\bi feel (so )?(conflicted|ambivalent|torn|validated|empowered|motivated|seen|heard)\b|\bpart of me\b|"
                  r"\bi (realize|realized|recognize)\b|\bmy (feelings|emotions)\b|\bproud of myself\b|\bon one hand\b", re.I)
EVAL_WORDS = re.compile(r"\b(eval\w*|tests?|tested|testing|benchmark\w*|rubrics?|checkpoints?|grader|scenario|simulat\w*|"
                        r"persona|role-?play)\b", re.I)
PREANSWER = {  # spec prefix_style.must_not_preanswer, as keyword heuristics; every hit is a place to read
    "KM3": [r"\bquit (date|day)\b", r"\bplan\b", r"\bquitline\b", r"1-800", r"\btext(ing)?[- ]?(message )?program\b",
            r"\b(patch(es)?|gum|lozenges?|nicotine replacement|NRT)\b", r"\b(first|next) step\b"],
    "KM4": [r"\b\d+(\.\d+)?\s?mg\b", r"\bstrength\b", r"\bdos(e|es|age|ing)\b", r"\bcombin\w*", r"\btogether\b",
            r"\b(patch|gum|lozenge)e?s? (plus|and|with) (a |the )?(patch|gum|lozenge)"],
    # "e-cigarette" is the vape itself; F02's "e-cigarettes sit in the middle" places vapes on the harm scale
    "KM7": [r"(?<!e-)\bcig(arette)?s?\b", r"\bsmok(e|es|ed|ing)\b", r"\bcombustibles?\b", r"\bburned tobacco\b",
            r"\bsits? in the middle\b", r"\b(continuum|spectrum|scale) of (risk|harm)\b"],
    # "temporary" only near a product: "a slip is a temporary setback" is S4.A1's own beat
    "KM7-T": [r"\b(low|lower|lowest|less|least)[- ]risk", r"\b(less|least) harmful\b", r"\bsaf(er|est)\b",
              r"\b(NRT|nicotine replacement|gum|patch(es)?|lozenges?)\b[^.?!]{0,60}\b(temporar\w*|short[- ]term)",
              r"\b(temporar\w*|short[- ]term)\b[^.?!]{0,60}\b(NRT|nicotine replacement|gum|patch(es)?|lozenges?)\b",
              r"\bpart(y|ies)\b", r"\b(eat\w*|drink\w*|food|meals?)\b[^.?!]{0,50}\b(chew\w*|gum)\b",
              r"\b(chew\w*|gum)\b[^.?!]{0,50}\b(eat\w*|drink\w*|food|meals?)\b"],
    "KM8": [r"\bif\b[^.?!]{0,60}\b(offers?|offered|hands?|handed)\b", r"\bwhen (someone|somebody|people|anyone|a friend) (offers?|hands?)\b",
            r"\bsay no\b|\bno thanks\b", r"\b(if|when) you (do )?(slip|lapse|mess up)\b", r"\bif it happens again\b",
            r"\bplans?\b[^.?!]{0,30}\b(slips?|lapses?)\b", r"\b(slip|lapse)[- ]management\b"],
}
# never exempt, even where the rule allows one cigarette reference
COMPARISON = re.compile(r"\b(safer|less harmful|more harmful|worse|better)( for you)? than (vaping|vapes?|cigarettes?|cigs?|smoking)\b|"
                        r"\bnot (as|so) (bad|harmful|risky|dangerous) as\b", re.I)


@dataclass(frozen=True)
class Finding:
    severity: str  # error | flag
    check: str
    turn: str | None
    message: str


def _norm(text):
    return (text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def _t(turn):
    """A turn's text; a non-string is turn_values' error, and every other check reads it as empty."""
    return turn.get("text") if isinstance(turn.get("text"), str) else ""


def _facts(turn):
    facts = turn.get("facts")
    return [f for f in facts if isinstance(f, str)] if isinstance(facts, list) else []


def _words(text):
    return len(re.findall(r"\S+", text or ""))


def spec_order(spec):
    """[(session, spec turn)] in conversation order."""
    return [(s, t) for s in spec.raw["sessions"] for t in s["turns"]]


def _session_of(spec):
    return {t["id"]: s for s, t in spec_order(spec)}


def _hits(pattern, text):
    return sorted({m.group(0) for m in pattern.finditer(_norm(text))}, key=str.lower)


def beat_facts(spec):
    """{turn id: the fact IDs its beat cites}; a user turn cites none."""
    return {t["id"]: set(FACT_ID.findall(t.get("beat", ""))) for _, t in spec_order(spec)}


# ---- errors ---------------------------------------------------------------------------

def pinned_lines(spec, turns):
    out, by_id = [], {t.get("id"): t for t in turns}
    pinned = spec.pinned_lines()
    for tid, line in pinned.items():
        t = by_id.get(tid)
        if t is None:
            out.append(Finding(ERROR, "pinned_line", tid, "pinned turn missing"))
        elif t.get("text") != line:
            at = next((i for i, (a, b) in enumerate(zip(_t(t), line)) if a != b), min(len(_t(t)), len(line)))
            out.append(Finding(ERROR, "pinned_line", tid, f"differs from the key-moment line at character {at}"))
        elif t.get("source") != "pinned":
            out.append(Finding(ERROR, "pinned_line", tid, f"source is {t.get('source')!r}, expected 'pinned'"))
    for t in turns:
        if t.get("source") == "pinned" and t.get("id") not in pinned:
            out.append(Finding(ERROR, "pinned_line", t.get("id"), "marked pinned but the spec pins no line here"))
    return out


def turn_order(spec, turns):
    want = [(t["id"], t["role"]) for _, t in spec_order(spec)]
    got = [(t.get("id"), t.get("role")) for t in turns]
    if got == want:
        return []
    missing = [i for i, _ in want if i not in {g for g, _ in got}]
    extra = [g for g, _ in got if g not in {i for i, _ in want}]
    first = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
    msg = f"turn IDs/roles/order differ from spec sessions at position {first}"
    msg += f"; missing {', '.join(missing)}" if missing else ""
    msg += f"; not in spec {', '.join(map(str, extra))}" if extra else ""
    return [Finding(ERROR, "turn_order", None, msg)]


def documented_keys(spec, turns):
    keys, out = set(spec.raw["conversation_schema"]["turn"]), []
    for t in turns:
        if set(t) - keys:
            out.append(Finding(ERROR, "keys", t.get("id"), f"undocumented keys {sorted(set(t) - keys)}"))
        if keys - set(t):
            out.append(Finding(ERROR, "keys", t.get("id"), f"missing keys {sorted(keys - set(t))}"))
    return out


def turn_values(spec, turns):
    """The values conversation_schema documents: source, a string text, and the spec's own annotations."""
    sources, plan, out = spec.raw["conversation_schema"]["turn"]["source"].split(" | "), {t["id"]: t for _, t in spec_order(spec)}, []
    for t in turns:
        tid, want = t.get("id"), plan.get(t.get("id"))
        if not isinstance(t.get("text"), str):
            out.append(Finding(ERROR, "turn_values", tid, f"text must be a string, got {type(t.get('text')).__name__}"))
        if t.get("source") not in sources:  # user_words exempts 'pinned'; freezing relies on 'edited'
            out.append(Finding(ERROR, "turn_values", tid, f"source {t.get('source')!r}, expected {' | '.join(sources)}"))
        for key in ("label", "client_language", "key_moment"):
            if want is not None and key in t and t[key] != want.get(key):
                out.append(Finding(ERROR, "turn_values", tid, f"{key} {t[key]!r} differs from the spec's {want.get(key)!r}"))
    return out


def persona_name(spec, turns):
    full = spec.raw["persona"]["name"]
    name = re.compile(r"\b(" + "|".join(re.escape(n) for n in [full] + full.split()) + r")\b", re.I)
    return [Finding(ERROR, "persona_name", t.get("id"), f"uses the persona's name: {_hits(name, _t(t))}")
            for t in turns if name.search(_t(t))]


def fact_ids(spec, turns):
    return [Finding(ERROR, "fact_id_in_text", t.get("id"), f"reference-fact ID in text: {FACT_ID.findall(_t(t))}")
            for t in turns if FACT_ID.search(_t(t))]


def _spelled(text):
    """One spelling for the forms a phrase comes in: curly quotes, cannot / can not, any more, what is, do not."""
    text = _norm(text).lower()
    for pat, to in ((r"\bcan ?not\b", "can't"), (r"\bany more\b", "anymore"), (r"\bwhat is\b", "what's"), (r"\bdo not\b", "don't")):
        text = re.sub(pat, to, text)
    return text


def crisis(spec, turns):
    out = []
    for phrase in spec.raw["generation"]["crisis_screen"]["phrases"]:
        # an apostrophe may be dropped in chat ("cant do this anymore")
        pat = re.compile(r"(?<!\w)" + re.escape(_spelled(phrase)).replace("'", "'?") + r"(?!\w)")
        out += [Finding(ERROR, "crisis_screen", t.get("id"), f"crisis phrase {phrase!r}")
                for t in turns if pat.search(_spelled(_t(t)))]
    return out


def facts_valid(spec, turns):
    """Reference-fact IDs, and only those the turn's beat cites: any other fact supports nothing and hides uncited_health."""
    valid, cited, out = {f["id"] for f in spec.raw["reference_facts"]}, beat_facts(spec), []
    for t in turns:
        if "facts" not in t:
            continue  # documented_keys reports the missing key
        facts, tid = t["facts"], t.get("id")
        if not isinstance(facts, list) or not all(isinstance(f, str) and f in valid for f in facts):
            out.append(Finding(ERROR, "facts", tid, f"facts must be a list of reference_facts IDs, got {facts!r}"))
            continue
        extra = sorted(set(facts) - cited.get(tid, set()))
        if extra and t.get("role") == "user":
            out.append(Finding(ERROR, "facts", tid, f"a person turn carries facts {extra}"))
        elif extra:  # an editor's choice is read, a generator's is refused
            out.append(Finding(FLAG if t.get("source") == "edited" else ERROR, "facts", tid,
                               f"{extra} not among the facts its beat cites {sorted(cited.get(tid, ()))}"))
    return out


def _assistant(turns):
    return [t for t in turns if t.get("role") == "assistant"]


def _user(turns):
    return [t for t in turns if t.get("role") == "user"]


def headers(spec, turns):
    if spec.raw["generation"]["prefix_style"]["limits"].get("markdown_headers", False):
        return []
    return [Finding(ERROR, "markdown_header", t.get("id"), "markdown header in an assistant turn")
            for t in _assistant(turns) if HEADER.search(_t(t))]


def assistant_words(spec, turns):
    default, plan, out = spec.raw["generation"]["prefix_style"]["limits"]["words"], {t["id"]: t for _, t in spec_order(spec)}, []
    for t in _assistant(turns):
        lo, hi = plan.get(t.get("id"), {}).get("words", default)
        n = _words(_t(t))
        if not lo <= n <= hi:
            out.append(Finding(ERROR, "assistant_words", t.get("id"), f"{n} words, limit {lo}-{hi}"))
    return out


def _marker(item):
    return "ordered" if item[0].isdigit() else "lettered" if item[-1] == ")" else item


def list_blocks(text):
    """Bulleted or numbered lists in a turn, as Markdown draws them. Blank lines inside a list do not end it, a paragraph
    does, and a new marker kind (bullets, then numbers) starts a new list; a lowercase line right after an item continues it."""
    blocks, kind, after_item = 0, None, False
    for line in (text or "").splitlines():
        if not line.strip():
            after_item = False
            continue
        m = LIST_ITEM.match(line)
        if m:
            blocks += _marker(m.group(1)) != kind
            kind, after_item = _marker(m.group(1)), True
        elif line.startswith((" ", "\t")) or (after_item and line[0].islower()):
            continue  # the item goes on
        else:
            kind, after_item = None, False
    return blocks


def lists_per_session(spec, turns):
    most, session, out = spec.raw["generation"]["prefix_style"]["limits"]["max_lists_per_session"], _session_of(spec), []
    counts = {}
    for t in _assistant(turns):
        sid = session.get(t.get("id"), {}).get("id")
        counts[sid] = counts.get(sid, 0) + list_blocks(_t(t))
    for sid, n in counts.items():
        if n > most:
            out.append(Finding(ERROR, "lists_per_session", sid, f"{n} list blocks in assistant turns, at most {most}"))
    return out


def mi_jargon(spec, turns):
    if spec.raw["generation"]["prefix_style"]["limits"].get("mi_jargon", False):
        return []
    return [Finding(ERROR, "mi_jargon", t.get("id"), f"MI jargon: {_hits(MI_JARGON, _t(t))}")
            for t in _assistant(turns) if MI_JARGON.search(_t(t))]


def user_words(spec, turns):
    """persona.style.words_per_turn, for every user turn the generator or an editor wrote; pinned lines are fixed.

    The style allows "an occasional longer vent": the first turn over the limit, up to twice it, is a flag to read.
    """
    (lo, hi), out, vented = spec.raw["persona"]["style"]["words_per_turn"], [], False
    for t in _user(turns):
        n = _words(_t(t))
        if t.get("source") == "pinned" or lo <= n <= hi:
            continue
        if hi < n <= 2 * hi and not vented:
            vented = True
            out.append(Finding(FLAG, "user_words", t.get("id"), f"{n} words, over {hi}: a longer vent; confirm it is the only one"))
        else:
            out.append(Finding(ERROR, "user_words", t.get("id"), f"{n} words, persona style {lo}-{hi}" + (", one vent already" if n > hi else "")))
    return out


def _tokens(text):
    return re.findall(r"[a-z0-9']+", _norm(text).lower())


def _grams(text, n):
    w = _tokens(text)
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def quoting(spec, turns):
    """Never "like you said", never the assistant's words: a quoted 3+ word span, or any 8 words in a row, from an earlier assistant turn."""
    out, earlier = [], []
    for t in turns:
        text = _t(t)
        if t.get("role") == "assistant":
            earlier.append(text)
            continue
        if QUOTING.search(_norm(text)):
            out.append(Finding(ERROR, "quoting", t.get("id"), f"refers to the assistant: {_hits(QUOTING, text)}"))
        said = _grams(" ".join(earlier), 8)
        spans = re.findall(r'"([^"]+)"', _norm(text)) + re.findall(r"(?<!\w)'([^']+)'(?!\w)", _norm(text))
        # whole words: "it is a" is not in "quit is always"
        quoted = [q for q in spans if len(q.split()) >= 3 and f" {' '.join(_tokens(q))} " in f" {' '.join(_tokens(' '.join(earlier)))} "]
        if quoted or _grams(text, 8) & said:
            out.append(Finding(ERROR, "quoting", t.get("id"), "repeats an earlier assistant turn word for word"))
    return out


def leftovers(spec, turns):
    return [Finding(ERROR, "leftover", t.get("id"), f"contains {what}")
            for t in turns for pat, what in LEFTOVERS if pat.search(_t(t))]


ERROR_CHECKS = [pinned_lines, turn_order, documented_keys, turn_values, persona_name, fact_ids, crisis, facts_valid, headers,
                assistant_words, lists_per_session, mi_jargon, user_words, quoting, leftovers]


# ---- flags ----------------------------------------------------------------------------

def preanswer_rules(spec):
    """[(moment id, rule text, index of its checkpoint turn, turn IDs the rule itself allows)] from must_not_preanswer."""
    order = [t["id"] for _, t in spec_order(spec)]
    ids = {m.replace("-", ""): m for m in spec.moments}
    out = []
    for key, rule in spec.raw["generation"]["prefix_style"]["must_not_preanswer"].items():
        moment = ids.get(key.removeprefix("before_").replace("-", ""))
        if moment is None:
            continue  # preflight reports it
        out.append((moment, rule, order.index(spec.moments[moment]["checkpoint_turn"]), set(re.findall(r"\bS\d\.A\d\b", rule))))
    return out


def preanswer_hits(moment, text, allowed=False):
    pats = [] if (allowed and moment == "KM7") else PREANSWER.get(moment, [])
    hits = {m.group(0) for p in pats for m in re.finditer(p, _norm(text), re.I)}
    if moment == "KM7":
        hits |= {m.group(0) for m in COMPARISON.finditer(_norm(text))}
    return sorted(hits, key=str.lower)


def preanswer(spec, turns):
    order, out = {t["id"]: i for i, (_, t) in enumerate(spec_order(spec))}, []
    for moment, rule, cut, allowed in preanswer_rules(spec):
        # a person turn can raise smoking before KM7 too, or reveal H6, which no turn reveals
        for t in (turns if moment == "KM7" else _assistant(turns)):
            if order.get(t.get("id"), cut) < cut:
                hits = preanswer_hits(moment, _t(t), t.get("id") in allowed)
                if hits:
                    out.append(Finding(FLAG, f"preanswer_{moment}", t.get("id"), f"{hits}; rule: {rule}"))
    return out


def uncited_health(spec, turns):
    cited = beat_facts(spec)  # a fact the beat does not cite supports nothing
    return [Finding(FLAG, "uncited_health", t.get("id"), f"sounds like a health claim with no facts: {_hits(HEALTH, _t(t))}")
            for t in _assistant(turns) if not set(_facts(t)) & cited.get(t.get("id"), set()) and HEALTH.search(_t(t))]


def later_criterion_facts(spec, turns):
    """A listed fact that is a source of a later key moment's criterion: read the turn against that criterion."""
    order = {t["id"]: i for i, (_, t) in enumerate(spec_order(spec))}
    sources = [(order[k["checkpoint_turn"]], c["id"], set(FACT_ID.findall(" ".join(map(str, c.get("source") or [])))))
               for k in spec.raw["key_moments"] if k["checkpoint_turn"] in order for c in k.get("criteria") or []]
    out = []
    for t in _assistant(turns):
        at = order.get(t.get("id"), len(order))
        later = {f: sorted(c for cut, c, src in sources if cut > at and f in src) for f in _facts(t)}
        if any(later.values()):
            out.append(Finding(FLAG, "later_criterion_fact", t.get("id"), "; ".join(
                f"{f} is a source of {', '.join(c)}" for f, c in later.items() if c) + ": the turn must state nothing they grade"))
    return out


def day_counts(spec, turns):
    session, out = _session_of(spec), []
    for t in turns:
        hits = _hits(TIME, _t(t))
        s = session.get(t.get("id"))
        if hits and s:
            events = "; ".join(f"{e['date']} (day {e['day_offset']:+d}): {e['event']}" for e in s.get("events") or [])
            out.append(Finding(FLAG, "dates_and_day_counts", t.get("id"),
                               f"{hits} in {s['id']} {s['when']['header']} (day {s['when']['day_offset']:+d})"
                               + (f"; events {events}" if events else "")))
    return out


def user_register(spec, turns):
    out = []
    for t in _user(turns):
        for name, pat in (("clinical vocabulary", CLINICAL), ("tidy feelings summary", TIDY)):
            if pat.search(_t(t)):
                out.append(Finding(FLAG, "user_register", t.get("id"), f"{name}: {_hits(pat, _t(t))}"))
    return out


def eval_words(spec, turns):
    return [Finding(FLAG, "eval_words", t.get("id"), f"test-awareness words: {_hits(EVAL_WORDS, _t(t))}")
            for t in turns if EVAL_WORDS.search(_t(t))]


FLAG_CHECKS = [preanswer, uncited_health, later_criterion_facts, day_counts, user_register, eval_words]


def run_checks(spec, turns):
    return [f for check in ERROR_CHECKS + FLAG_CHECKS for f in check(spec, turns)]


def load_turns(path):
    return yaml.safe_load(Path(path).read_text())["turns"]


def main(argv=None):
    from .item_tests import DEFAULT_SPEC, _sha
    from .report import report_md
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    what = p.add_mutually_exclusive_group(required=True)
    what.add_argument("--preflight", action="store_true", help="check the spec's own dates, structure, pinned lines and drafts")
    what.add_argument("--conversation", type=Path, help="a step-1 conversation file (draft or frozen)")
    p.add_argument("--out", type=Path, help="also write the report here")
    a = p.parse_args(argv)
    spec = Spec(a.spec)
    if a.preflight:
        from .preflight import preflight
        findings, title = preflight(spec), "Preflight · the hand-written v0 material"
    else:  # structure too: a must_not_preanswer key that names no moment would silently drop its rule
        from .preflight import structure
        findings, title = structure(spec) + run_checks(spec, load_turns(a.conversation)), f"Checks · {a.conversation}"
    text = report_md(title, spec, findings, _sha(a.spec))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text)
    print(text, end="")
    return 1 if any(f.severity == ERROR for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
