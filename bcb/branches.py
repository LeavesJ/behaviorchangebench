"""Hard-question branches: the same conversation, then a new message from her whose good answer needs a sourced fact.

The branches live in their own file (trajectories/vaping-v0/hard-pilot.yaml) until the team
picks which ones go into the spec. `attach` adds them to a loaded Spec as variant checkpoints,
the way KM7-T and KM7-P are written, so rendering, grading and scoring treat them the same.
Each branch has at least one check citing a pilot fact (P01, P02, ...), which carries the exact
source passage; other checks may cite the spec's facts (F01, ...) or a framework.
"""

import re
from pathlib import Path

import yaml

from .spec import FACT_ID

PILOT_FACT = re.compile(r"\bP\d\d\b")
_REQUIRED = ("id", "title", "prefix", "user_line", "goal", "criteria", "reference_reply", "near_miss_reply")
_FACT_KEYS = ("claim", "quote", "source", "url", "read")


def load(path):
    return yaml.safe_load(Path(path).read_text())


def problems(spec, raw):
    """Everything wrong with a branches file, as one list; empty means it can be attached."""
    out = []
    facts = {f.get("id"): f for f in raw.get("facts") or []}
    for fid, f in facts.items():
        out += [f"{fid}: no {k}" for k in _FACT_KEYS if not f.get(k)]
    turns = [t for s in spec.raw["sessions"] for t in s["turns"]]
    order = [t["id"] for t in turns]
    roles = {t["id"]: t["role"] for t in turns}
    spec_facts = {f["id"] for f in spec.raw["reference_facts"]}
    seen_items, seen_ids = set(spec.items), set()
    for b in raw.get("branches") or []:
        bid = b.get("id", "?")
        missing = [k for k in _REQUIRED if not b.get(k)]
        if missing:
            out.append(f"{bid}: missing {', '.join(missing)}")
            continue
        if bid in spec.moments or bid in seen_ids:
            out.append(f"{bid}: the id is already used")
        seen_ids.add(bid)
        through, replace = b["prefix"].get("through"), b["prefix"].get("replace_turn") or {}
        if through not in order:
            out.append(f"{bid}: prefix.through {through!r} is not a turn")
        elif len(replace) != 1 or list(replace.values()) != ["user_line"]:
            out.append(f"{bid}: prefix.replace_turn must be {{<turn id>: user_line}}")
        else:
            replaced, i = next(iter(replace)), order.index(through)
            if i + 1 >= len(order) or order[i + 1] != replaced:
                out.append(f"{bid}: replace_turn must name the turn right after {through}")
            elif roles[replaced] != "user":
                out.append(f"{bid}: replace_turn {replaced} is not a user turn")
        if PILOT_FACT.search(b["user_line"]) or "Dani" in b["user_line"]:
            out.append(f"{bid}: the user line carries a fact ID or the persona's name")
        for c in b["criteria"]:
            cid = c.get("id", "?")
            if cid in seen_items:
                out.append(f"{bid}: criterion id {cid} is already used")
            seen_items.add(cid)
            if c.get("polarity") not in ("desired", "undesired"):
                out.append(f"{cid}: polarity must be desired or undesired")
            elif not isinstance(c.get("points"), int) or (c["points"] > 0) != (c["polarity"] == "desired"):
                out.append(f"{cid}: points must be a positive int for desired items, negative for undesired")
            if not c.get("criterion"):
                out.append(f"{cid}: no criterion text")
            sources = [str(s) for s in c.get("source") or []]
            if not sources:
                out.append(f"{cid}: no source")
            out += [f"{cid}: cites unknown fact {s}" for s in sources
                    if (PILOT_FACT.fullmatch(s) and s not in facts) or (FACT_ID.fullmatch(s) and s not in spec_facts)]
        if not any(PILOT_FACT.fullmatch(str(s)) for c in b["criteria"] for s in c.get("source") or []):
            out.append(f"{bid}: no check cites a pilot fact (P01, ...)")
        out += [f"{bid}: reuses unknown item {r}" for r in b.get("criteria_reused") or [] if r not in spec.items]
    return out


def attach(spec, raw):
    """Add the branches to `spec` as variant checkpoints; returns their IDs. Call `problems` first."""
    bad = problems(spec, raw)
    if bad:
        raise ValueError("hard-pilot branches are malformed:\n  " + "\n  ".join(bad))
    ids = []
    for b in raw["branches"]:
        moment = {"id": b["id"], "title": b["title"], "status": "pilot", "variant_of": b.get("variant_of"),
                  "prefix": b["prefix"], "user_line": b["user_line"], "criteria": b["criteria"],
                  "criteria_reused": b.get("criteria_reused") or []}
        spec.raw["key_moments"].append(moment)
        spec.moments[b["id"]] = moment
        spec.raw["target"]["goal_at_checkpoint"][b["id"]] = b["goal"]
        ids.append(b["id"])
    spec.items = spec._items()
    return ids
