"""Preflight: check the spec itself before anything is generated from it.

Dates (each session's date line against its date, one quit day behind every day_offset, every
named weekday a real one), the cross-references the generator relies on, the user-text checks
of bcb/checks.py over the hand-written pinned lines and drafts, and the prefix heuristics over
the beats. Run it as `python -m bcb.checks --preflight`; findings use the same severities.
"""

import re
from datetime import date, datetime, timedelta

from . import checks
from .checks import ERROR, FLAG, HEALTH, Finding, _assistant, _hits, _user, spec_order
from .spec import _FACT_CITE, FACT_ID

_DOW_DATE = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,? (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{1,2})\b")


def _strings(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def _header(d: datetime):
    return f"[{d:%a}, {d:%b} {d.day}, {d.year}, {d.hour % 12 or 12}:{d:%M} {d:%p}]"


def dates(spec):
    """Header weekday/date against when.date; one quit day behind every day_offset; every 'Dow Mon D' a real weekday."""
    out, anchors, year = [], [], None
    for s in spec.raw["sessions"]:
        when, d = s["when"], date.fromisoformat(str(s["when"]["date"]))
        year = year or d.year
        try:
            h = datetime.strptime(when["header"], "[%a, %b %d, %Y, %I:%M %p]")
        except ValueError:
            out.append(Finding(ERROR, "dates", s["id"], f"header {when['header']!r} is not in the session header format"))
        else:
            if h.date() != d or _header(h) != when["header"]:
                out.append(Finding(ERROR, "dates", s["id"], f"header {when['header']!r} disagrees with when.date {d} ({d:%a})"))
        anchors.append((s["id"], d - timedelta(days=when["day_offset"])))
        for e in s.get("events") or []:
            anchors.append((f"{s['id']} event", date.fromisoformat(str(e["date"])) - timedelta(days=e["day_offset"])))
    days = {a for _, a in anchors}
    if len(days) != 1:
        out.append(Finding(ERROR, "dates", None, "day_offsets imply different quit days: "
                           + ", ".join(f"{w} {a:%a %b %d}" for w, a in anchors)))
    quit_day = anchors[0][1]
    named = [(p, m) for p, s in _strings(spec.raw["sessions"]) for m in re.finditer(r"\bquit (?:on )?" + _DOW_DATE.pattern, s)]
    if not named:
        out.append(Finding(ERROR, "dates", None, "no session names the quit day ('quit <Dow> <Mon> <D>')"))
    for p, m in named:
        d = datetime.strptime(f"{m.group(2)} {m.group(3)} {year}", "%b %d %Y").date()
        if d != quit_day:
            out.append(Finding(ERROR, "dates", p, f"names quit day {d}, but day 0 by day_offset is {quit_day}"))
    for p, s in _strings(spec.raw):
        for m in _DOW_DATE.finditer(s):
            d = datetime.strptime(f"{m.group(2)} {m.group(3)} {year}", "%b %d %Y").date()
            if f"{d:%a}" != m.group(1):
                out.append(Finding(ERROR, "dates", p, f"{m.group(0)!r}: {d} is a {d:%A}"))
    return out


def structure(spec):
    """Cross-references the generator relies on: pins, key moments, reveals, fact citations, rule names, alternation."""
    out, plan = [], spec_order(spec)
    ids = [t["id"] for _, t in plan]
    pinned = spec.pinned_lines()
    facts = {f["id"] for f in spec.raw["reference_facts"]}
    for k in spec.raw["key_moments"]:
        if k["checkpoint_turn"] not in ids:
            out.append(Finding(ERROR, "structure", k["id"], f"checkpoint_turn {k['checkpoint_turn']} is not a turn"))
    for _, t in plan:
        if bool(t.get("pinned")) != (t["id"] in pinned):
            out.append(Finding(ERROR, "structure", t["id"], "pinned: true needs a key-moment user_line, and the reverse"))
        if t.get("key_moment") and spec.moments.get(t["key_moment"], {}).get("checkpoint_turn") != t["id"]:
            out.append(Finding(ERROR, "structure", t["id"], f"key_moment {t['key_moment']} does not point here"))
        if t["role"] == "user" and not t.get("pinned") and not t.get("draft"):
            out.append(Finding(ERROR, "structure", t["id"], "unpinned user turn has no draft"))
        if t["role"] == "assistant" and not t.get("beat"):
            out.append(Finding(ERROR, "structure", t["id"], "assistant turn has no beat"))
        unknown = sorted(set(FACT_ID.findall(t.get("beat", ""))) - facts)
        if unknown:
            out.append(Finding(ERROR, "structure", t["id"], f"beat cites unknown facts {unknown}"))
    roles = [t["role"] for _, t in plan]
    if roles != ["user", "assistant"] * (len(roles) // 2) + ["user"] * (len(roles) % 2):
        out.append(Finding(ERROR, "structure", None, "roles do not alternate user/assistant"))
    turns = {t["id"]: t for _, t in plan}
    for h in spec.raw["persona"]["hidden_facts"]:
        where = h.get("revealed_in")
        if where and h["id"] not in (turns.get(where, {}).get("must_convey") or []):
            out.append(Finding(ERROR, "structure", h["id"], f"revealed_in {where}, but that turn's must_convey does not list it"))
    norm = {m.replace("-", "") for m in spec.moments}
    for key in spec.raw["generation"]["prefix_style"]["must_not_preanswer"]:
        if key.removeprefix("before_").replace("-", "") not in norm:
            out.append(Finding(ERROR, "structure", key, "must_not_preanswer key names no key moment"))
    return out


def spec_as_turns(spec):
    """The hand-written material as conversation turns: pinned lines and user drafts; beats (fact IDs stripped) as assistant text."""
    pinned = spec.pinned_lines()
    return [{"id": t["id"], "role": t["role"], "source": "pinned" if t["id"] in pinned else "draft", "facts": [],
             "text": pinned.get(t["id"]) or t.get("draft") or _FACT_CITE.sub("", t.get("beat", "")).strip()}
            for _, t in spec_order(spec)]


def preflight(spec):
    """Dates and structure; the user-text checks over pinned lines and drafts; the prefix heuristics over the beats."""
    turns = spec_as_turns(spec)
    users = _user(turns)
    beats = _assistant(turns)
    out = dates(spec) + structure(spec)
    for check in (checks.persona_name, checks.fact_ids, checks.crisis, checks.user_words, checks.quoting,
                  checks.leftovers, checks.user_register, checks.eval_words, checks.day_counts):
        out += check(spec, users)
    # a beat describes a turn rather than being one, so what it trips is for reading, never an error
    for check in (checks.persona_name, checks.crisis, checks.preanswer, checks.eval_words):
        out += [Finding(FLAG, f"{f.check} (beat)", f.turn, f.message) for f in check(spec, beats)]
    plan = {t["id"]: t for _, t in spec_order(spec)}
    out += [Finding(FLAG, "uncited_health (beat)", t["id"], f"cites no fact but reads as a health claim: {_hits(HEALTH, t['text'])}")
            for t in beats if not FACT_ID.search(plan[t["id"]].get("beat", "")) and HEALTH.search(t["text"])]
    return out
