"""Load and check the known-pass / known-fail examples each rubric item must grade correctly (README rule 4)."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from .grader import NA_CODES, passes, quote_in_reply
from .spec import FACT_ID, Spec

KINDS = ("known_pass", "known_fail", "known_na")
EXPECTED = ("met", "not_met", "not_applicable")
# pending_rewording: the expected verdict holds only under a proposed rewording of the item.
# Kept so the hard case is not lost, validated like any other, never graded or counted.
STATUSES = ("active", "pending_rewording")


@dataclass(frozen=True)
class Example:
    id: str
    item: str
    checkpoint: str
    kind: str
    expected: str
    reply: str
    decisive_span: str = ""
    na_code: str | None = None
    why: str = ""
    status: str = "active"


def load(path) -> tuple[dict, list[Example]]:
    raw = yaml.safe_load(Path(path).read_text())
    fields = Example.__dataclass_fields__
    return raw, [Example(**{k: v for k, v in e.items() if k in fields}) for e in raw["examples"]]


def problems(spec: Spec, examples: list[Example]) -> list[str]:
    """Every way an example breaks the contract. An empty list means the file is well formed."""
    out, seen = [], set()
    persona = [spec.raw["persona"]["name"]] + spec.raw["persona"]["name"].split()
    for e in examples:
        where = f"{e.id}:"
        if e.id in seen:
            out.append(f"{where} duplicate id")
        seen.add(e.id)
        item = spec.items.get(e.item)
        if item is None:
            out.append(f"{where} unknown item {e.item}")
            continue
        if e.checkpoint not in spec.moments:
            out.append(f"{where} unknown checkpoint {e.checkpoint}")
            continue
        if e.item not in spec.items_at(e.checkpoint):
            out.append(f"{where} {e.item} is not graded at {e.checkpoint}")
        if e.status not in STATUSES:
            out.append(f"{where} status {e.status!r} not recognised")
        if e.kind not in KINDS or e.expected not in EXPECTED:
            out.append(f"{where} kind {e.kind!r} / expected {e.expected!r} not recognised")
            continue
        if e.kind == "known_na":
            if e.expected != "not_applicable" or e.na_code not in NA_CODES:
                out.append(f"{where} known_na needs expected not_applicable and a valid na_code")
            if item.not_applicable is None:
                out.append(f"{where} {e.item} has no not_applicable condition")
        elif passes(item, e.expected) is not (e.kind == "known_pass"):
            out.append(f"{where} {e.kind} with expected {e.expected} contradicts {e.item}'s polarity ({item.polarity})")
        if not e.reply.strip():
            out.append(f"{where} empty reply")
        if FACT_ID.search(e.reply):
            out.append(f"{where} reply contains a reference-fact ID")
        if any(name in e.reply for name in persona):
            out.append(f"{where} reply uses the persona's name, which the model never learns")
        if e.expected == "met" and not quote_in_reply(e.decisive_span, e.reply):
            out.append(f"{where} decisive_span is not a span of the reply")
    return out


def coverage_gaps(spec: Spec, examples: list[Example]) -> list[str]:
    """Items still missing an active known-pass or known-fail example."""
    have = {(e.item, e.kind) for e in examples if e.status == "active"}
    return [f"{item} has no {kind}" for item in spec.items for kind in ("known_pass", "known_fail")
            if (item, kind) not in have]
