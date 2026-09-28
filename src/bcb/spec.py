"""Load a conversation spec (trajectories/<id>/spec.yaml) into typed objects and validate it.

Validation collects every problem and raises them together as one SpecError, so an author
sees the whole list at once. Nothing here calls a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .errors import SpecError
from .util import load_yaml, sha256_file

POLARITIES = ("desired", "undesired")
ITEM_TYPES = ("gate", "fidelity")
APPLICABILITY = ("applies", "not_applicable", "depends_on_reply")
VERDICTS = ("met", "not_met", "not_applicable", "cannot_determine")
LABEL_VERDICTS = ("met", "not_met", "not_applicable")

# The role an item plays at one checkpoint. A gate is scored pass^k where its role is ROLE_GATE;
# where it is graded only to measure false positives (KM7-G1 at KM7-T) it never enters pass^k.
ROLE_ITEM = "item"
ROLE_GATE = "gate"
ROLE_GATE_FALSE_POSITIVE = "gate_false_positive_check"

LEVEL_CORE = "core"
LEVEL_SCENARIO = "scenario"

NA_OTHER = "NA_OTHER"
NA_LIVE_ONLY = "NA_NOT_ESTABLISHED"  # live mode only; never valid at a checkpoint

_TURN_ID = re.compile(r"^(S\d+)\.([UA])(\d+)$")
_FACT_ID = re.compile(r"^F\d\d$")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_HEADER = re.compile(
    r"^\[(?P<dow>[A-Z][a-z]{2}), (?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2}), (?P<year>\d{4}), "
    r"(?P<h>\d{1,2}):(?P<mm>\d{2}) (?P<ampm>AM|PM)\]$"
)


@dataclass(frozen=True)
class Item:
    id: str
    version: str
    type: str
    polarity: str
    points: int | None
    criterion: str
    applies_when: str
    na_code: str | None
    default_when_unsure: str
    severity: str | None
    level: str
    rubric: str
    direction: str | None  # harmful_compliance | overrefusal, for the safety rubric only
    tags: tuple[str, ...]
    source: tuple[str, ...]
    home: str | None  # the key moment that defines it; None for always-on items

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def is_gate(self) -> bool:
        return self.type == "gate"


@dataclass(frozen=True)
class Turn:
    id: str
    session: str
    role: str
    pinned: bool
    key_moment: str | None
    draft: str | None
    beat: str | None
    words: tuple[int, int] | None
    must_not: tuple[str, ...]
    must_convey: tuple[str, ...]
    label: str | None
    client_language: tuple[str, ...]


@dataclass(frozen=True)
class Session:
    id: str
    date: date
    header: str
    day_offset: int
    purpose: str
    turn_ids: tuple[str, ...]


@dataclass(frozen=True)
class GradedItem:
    item: Item
    role: str
    expected: str  # applies | not_applicable | depends_on_reply


@dataclass(frozen=True)
class Checkpoint:
    id: str
    turn_id: str
    user_line: str
    variant_of: str | None
    through: str | None
    optional: bool
    goal: str
    graded: tuple[GradedItem, ...]


@dataclass(frozen=True)
class Spec:
    path: Path
    sha256: str
    id: str
    version: str
    raw: dict[str, Any]
    persona_name: str
    sessions: tuple[Session, ...]
    turns: dict[str, Turn]
    turn_order: tuple[str, ...]
    items: dict[str, Item]
    checkpoints: dict[str, Checkpoint]
    default_checkpoints: tuple[str, ...]
    optional_checkpoints: tuple[str, ...]
    na_codes: tuple[str, ...]
    reference_facts: dict[str, str]
    crisis_phrases: tuple[str, ...]
    prefix_style: dict[str, Any]

    def session_of(self, turn_id: str) -> Session:
        sid = self.turns[turn_id].session
        return next(s for s in self.sessions if s.id == sid)

    def allowed_na_codes(self, item: Item) -> tuple[str, ...]:
        """Codes a grader or labeler may give at a checkpoint: the item's own code, plus NA_OTHER
        so that an unexpected N/A is recorded as data (an N/A mismatch), not as an error."""
        codes = [item.na_code] if item.na_code else []
        return tuple(dict.fromkeys([*codes, NA_OTHER]))

    def grader_note(self, checkpoint_id: str) -> str:
        template = self.raw["grading"]["grader_note_template"]
        target = self.raw["target"]
        return template.format(
            target_behavior=target["target_behavior"], goal_at_checkpoint=self.checkpoints[checkpoint_id].goal
        )


def load_spec(path: str | Path) -> Spec:
    path = Path(path)
    raw = load_yaml(path)
    if not isinstance(raw, dict):
        raise SpecError(str(path), ["top level is not a mapping"])
    return _Builder(path, raw).build()


class _Builder:
    def __init__(self, path: Path, raw: dict[str, Any]):
        self.path = path
        self.raw = raw
        self.problems: list[str] = []

    def bad(self, msg: str) -> None:
        self.problems.append(msg)

    def need(self, obj: Any, key: str, where: str, kind: type | tuple[type, ...] = str) -> Any:
        if not isinstance(obj, dict) or key not in obj or obj[key] is None:
            self.bad(f"{where}: missing `{key}`")
            return None
        value = obj[key]
        if not isinstance(value, kind):
            self.bad(f"{where}: `{key}` must be {kind}, got {type(value).__name__}")
            return None
        return value

    def build(self) -> Spec:
        raw = self.raw
        meta = self.need(raw, "spec", "spec", dict) or {}
        spec_id = self.need(meta, "id", "spec") or ""
        version = str(meta.get("version") or "")
        if not version:
            self.bad("spec: missing `version`")
        persona = self.need(raw, "persona", "spec", dict) or {}
        persona_name = self.need(persona, "name", "persona") or ""

        defaults = self.need(raw, "criterion_defaults", "spec", dict) or {}
        na_codes = tuple(defaults.get("not_applicable_codes") or ())
        if not na_codes:
            self.bad("criterion_defaults: missing `not_applicable_codes`")

        facts = self._reference_facts()
        sessions, turns, order = self._sessions()
        items, homes = self._items(defaults, na_codes)
        self._assign_rubrics(items)
        items = {i: self._with_rubric(it) for i, it in items.items()}
        checkpoints = self._checkpoints(turns, order, items, homes)
        default_cps, optional_cps = self._run_checkpoints(checkpoints)
        checkpoints = {
            k: replace(cp, optional=k in optional_cps) for k, cp in checkpoints.items()
        }
        gen = raw.get("generation") or {}
        crisis = tuple((gen.get("crisis_screen") or {}).get("phrases") or ())
        if not crisis:
            self.bad("generation.crisis_screen: missing `phrases`")
        prefix_style = gen.get("prefix_style") or {}
        if not prefix_style.get("limits"):
            self.bad("generation.prefix_style: missing `limits`")
        grading = raw.get("grading") or {}
        if "grader_note_template" not in grading:
            self.bad("grading: missing `grader_note_template`")

        if self.problems:
            raise SpecError(str(self.path), self.problems)
        return Spec(
            path=self.path,
            sha256=sha256_file(self.path),
            id=spec_id,
            version=version,
            raw=raw,
            persona_name=persona_name,
            sessions=sessions,
            turns=turns,
            turn_order=order,
            items=items,
            checkpoints=checkpoints,
            default_checkpoints=default_cps,
            optional_checkpoints=optional_cps,
            na_codes=na_codes,
            reference_facts=facts,
            crisis_phrases=crisis,
            prefix_style=prefix_style,
        )

    # -- reference facts ---------------------------------------------------------------------

    def _reference_facts(self) -> dict[str, str]:
        facts: dict[str, str] = {}
        for n, f in enumerate(self.raw.get("reference_facts") or []):
            fid = self.need(f, "id", f"reference_facts[{n}]")
            claim = self.need(f, "claim", f"reference_facts[{n}]")
            if fid is None or claim is None:
                continue
            if not _FACT_ID.match(fid):
                self.bad(f"reference_facts[{n}]: id {fid!r} does not match F<nn>")
            if fid in facts:
                self.bad(f"reference_facts: duplicate id {fid}")
            facts[fid] = claim
        if not facts:
            self.bad("spec: no `reference_facts`")
        return facts

    # -- sessions and turns ------------------------------------------------------------------

    def _sessions(self) -> tuple[tuple[Session, ...], dict[str, Turn], tuple[str, ...]]:
        sessions: list[Session] = []
        turns: dict[str, Turn] = {}
        order: list[str] = []
        day0s: set[date] = set()
        for n, s in enumerate(self.raw.get("sessions") or []):
            where = f"sessions[{n}]"
            sid = self.need(s, "id", where) or f"?{n}"
            when = self.need(s, "when", sid, dict) or {}
            d = self.need(when, "date", f"{sid}.when", date)
            header = self.need(when, "header", f"{sid}.when")
            offset = self.need(when, "day_offset", f"{sid}.when", int)
            if d is not None and header is not None:
                self._check_header(sid, d, header)
            if d is not None and offset is not None:
                day0s.add(d - timedelta(days=offset))
            ids: list[str] = []
            for m, t in enumerate(s.get("turns") or []):
                turn = self._turn(sid, t, f"{sid}.turns[{m}]")
                if turn is None:
                    continue
                if turn.id in turns:
                    self.bad(f"{sid}: duplicate turn id {turn.id}")
                    continue
                turns[turn.id] = turn
                ids.append(turn.id)
            if not ids:
                self.bad(f"{sid}: no turns")
            roles = [turns[i].role for i in ids]
            expected = ["user" if k % 2 == 0 else "assistant" for k in range(len(roles))]
            if roles != expected:
                self.bad(f"{sid}: turns must alternate user/assistant starting with user")
            sessions.append(
                Session(sid, d or date.min, header or "", offset or 0, s.get("purpose", ""), tuple(ids))
            )
            order.extend(ids)
        if not sessions:
            self.bad("spec: no `sessions`")
        if len(day0s) > 1:
            self.bad(f"sessions: date minus day_offset disagrees across sessions: {sorted(map(str, day0s))}")
        return tuple(sessions), turns, tuple(order)

    def _check_header(self, sid: str, d: date, header: str) -> None:
        m = _HEADER.match(header)
        if not m:
            self.bad(f"{sid}: header {header!r} does not match `[Dow, Mon D, YYYY, h:mm AM/PM]`")
            return
        want = (_DAYS[d.weekday()], _MONTHS[d.month - 1], d.day, d.year)
        got = (m["dow"], m["mon"], int(m["day"]), int(m["year"]))
        if want != got:
            self.bad(f"{sid}: header {header!r} disagrees with date {d.isoformat()} (expected {want})")
        if not (1 <= int(m["h"]) <= 12 and 0 <= int(m["mm"]) <= 59):
            self.bad(f"{sid}: header {header!r} has an invalid time")

    def _turn(self, sid: str, t: Any, where: str) -> Turn | None:
        tid = self.need(t, "id", where)
        role = self.need(t, "role", where)
        if tid is None or role is None:
            return None
        m = _TURN_ID.match(tid)
        if not m or m.group(1) != sid:
            self.bad(f"{where}: turn id {tid!r} must look like {sid}.U<n> or {sid}.A<n>")
            return None
        if role != {"U": "user", "A": "assistant"}[m.group(2)]:
            self.bad(f"{tid}: role {role!r} disagrees with its id")
        pinned = bool(t.get("pinned", False))
        km = t.get("key_moment")
        if role == "user":
            if pinned and not km:
                self.bad(f"{tid}: pinned user turn without `key_moment`")
            if not pinned and not t.get("draft"):
                self.bad(f"{tid}: unpinned user turn without `draft`")
        else:
            if pinned:
                self.bad(f"{tid}: assistant turns are never pinned")
            if not t.get("beat"):
                self.bad(f"{tid}: assistant turn without `beat`")
        words = t.get("words")
        if words is not None:
            if not (isinstance(words, list) and len(words) == 2 and all(isinstance(w, int) for w in words)):
                self.bad(f"{tid}: `words` must be [min, max]")
                words = None
            else:
                words = (words[0], words[1])
        return Turn(
            id=tid,
            session=sid,
            role=role,
            pinned=pinned,
            key_moment=km,
            draft=t.get("draft"),
            beat=t.get("beat"),
            words=words,
            must_not=tuple(map(str, t.get("must_not") or ())),
            must_convey=tuple(map(str, t.get("must_convey") or ())),
            label=t.get("label"),
            client_language=tuple(map(str, t.get("client_language") or ())),
        )

    # -- items --------------------------------------------------------------------------------

    def _items(self, defaults: dict[str, Any], na_codes: tuple[str, ...]) -> tuple[dict[str, Item], dict[str, str | None]]:
        items: dict[str, Item] = {}
        homes: dict[str, str | None] = {}
        for n, raw_item in enumerate(self.raw.get("always_on_metrics") or []):
            self._add_item(items, homes, raw_item, f"always_on_metrics[{n}]", defaults, na_codes, None)
        for km in self.raw.get("key_moments") or []:
            for n, raw_item in enumerate(km.get("criteria") or []):
                self._add_item(items, homes, raw_item, f"{km.get('id')}.criteria[{n}]", defaults, na_codes, km.get("id"))
        if not any(it.level == LEVEL_CORE for it in items.values()):
            self.bad("spec: no `always_on_metrics`")
        return items, homes

    def _add_item(self, items, homes, r, where, defaults, na_codes, home) -> None:
        iid = self.need(r, "id", where)
        if iid is None:
            return
        if iid in items:
            self.bad(f"{where}: duplicate item id {iid}")
            return
        where = f"item {iid}"
        itype = r.get("type", defaults.get("type", "fidelity"))
        polarity = self.need(r, "polarity", where)
        criterion = self.need(r, "criterion", where)
        points = r.get("points")
        severity = r.get("severity")
        if itype not in ITEM_TYPES:
            self.bad(f"{where}: type {itype!r} not in {ITEM_TYPES}")
        if polarity is not None and polarity not in POLARITIES:
            self.bad(f"{where}: polarity {polarity!r} not in {POLARITIES}")
        if itype == "gate":
            if points is not None:
                self.bad(f"{where}: a gate carries no points (it acts through the cap)")
            if not severity:
                self.bad(f"{where}: a gate needs a `severity`")
        else:
            if not isinstance(points, int) or isinstance(points, bool) or not (-10 <= points <= 10) or points == 0:
                self.bad(f"{where}: points must be a nonzero integer in [-10, 10]")
            elif polarity == "desired" and points < 0 or polarity == "undesired" and points > 0:
                self.bad(f"{where}: sign of points ({points}) disagrees with polarity {polarity}")
        na_code = r.get("not_applicable")
        if na_code is not None and na_code not in na_codes:
            self.bad(f"{where}: not_applicable code {na_code!r} not in {na_codes}")
        scope = defaults.get("scope") or {}
        unsure = r.get("default_when_unsure")
        if unsure is None:
            table = defaults.get("default_when_unsure") or {}
            unsure = table.get("gate" if itype == "gate" else polarity)
            if unsure is None:
                self.bad(f"{where}: no `default_when_unsure` and no default for it")
        version = r.get("version", defaults.get("version"))
        if version is None:
            self.bad(f"{where}: no `version` and no default version")
        tags = tuple(map(str, r.get("tags") or ()))
        items[iid] = Item(
            id=iid,
            version=str(version),
            type=itype,
            polarity=polarity or "",
            points=points,
            criterion=criterion or "",
            applies_when=str(r.get("applies_when", scope.get("applies_when", "always"))),
            na_code=na_code,
            default_when_unsure=str(unsure),
            severity=severity,
            level=LEVEL_CORE if home is None else LEVEL_SCENARIO,
            rubric="",
            direction=None,
            tags=tags,
            source=tuple(map(str, r.get("source") or ())),
            home=home,
        )
        homes[iid] = home

    def _assign_rubrics(self, items: dict[str, Item]) -> None:
        self._rubric_of: dict[str, tuple[str, str | None]] = {}
        rubrics = self.raw.get("rubrics") or {}
        for name, body in rubrics.items():
            if not isinstance(body, dict) or "items" not in body:
                continue  # scoring, theory_consistency and the like are not item lists
            groups = body["items"]
            pairs = groups.items() if isinstance(groups, dict) else [(None, groups)]
            for direction, patterns in pairs:
                for pattern in patterns:
                    matched = _expand(pattern, items)
                    if not matched:
                        self.bad(f"rubrics.{name}: {pattern!r} matches no item")
                    for iid in matched:
                        if iid in self._rubric_of:
                            self.bad(f"item {iid}: in rubric {self._rubric_of[iid][0]} and {name}")
                        self._rubric_of[iid] = (name, direction)
        for iid in items:
            if iid not in self._rubric_of:
                self.bad(f"item {iid}: in no rubric")

    def _with_rubric(self, item: Item) -> Item:
        name, direction = self._rubric_of.get(item.id, ("", None))
        return replace(item, rubric=name, direction=direction)

    # -- checkpoints ---------------------------------------------------------------------------

    def _checkpoints(self, turns, order, items, homes) -> dict[str, Checkpoint]:
        goals = (self.raw.get("target") or {}).get("goal_at_checkpoint") or {}
        core_ids = [i for i, it in items.items() if it.level == LEVEL_CORE]
        out: dict[str, Checkpoint] = {}
        for n, km in enumerate(self.raw.get("key_moments") or []):
            kid = self.need(km, "id", f"key_moments[{n}]")
            if kid is None:
                continue
            turn_id = self.need(km, "checkpoint_turn", kid)
            line = self.need(km, "user_line", kid)
            variant_of = km.get("variant_of")
            through = None
            if turn_id is not None and turn_id not in turns:
                self.bad(f"{kid}: checkpoint_turn {turn_id} is not a turn")
                turn_id = None
            if turn_id is not None:
                if variant_of is None:
                    t = turns[turn_id]
                    if not (t.pinned and t.role == "user" and t.key_moment == kid):
                        self.bad(f"{kid}: {turn_id} must be a pinned user turn with key_moment {kid}")
                else:
                    through = self._variant_prefix(kid, km, turn_id, order)
            goal = goals.get(kid)
            if goal is None:
                self.bad(f"{kid}: no target.goal_at_checkpoint entry")
            graded = self._graded(kid, km, items, core_ids)
            out[kid] = Checkpoint(kid, turn_id or "", line or "", variant_of, through, False, str(goal), graded)
        for kid, cp in out.items():
            if cp.variant_of is not None and cp.variant_of not in out:
                self.bad(f"{kid}: variant_of {cp.variant_of} is not a key moment")
        for tid, t in turns.items():
            if t.pinned and t.key_moment not in out:
                self.bad(f"{tid}: key_moment {t.key_moment} is not defined in key_moments")
        return out

    def _variant_prefix(self, kid: str, km: dict, turn_id: str, order: tuple[str, ...]) -> str | None:
        prefix = km.get("prefix") or {}
        through = prefix.get("through")
        replace = prefix.get("replace_turn")
        if through not in order:
            self.bad(f"{kid}: prefix.through {through!r} is not a turn")
            return None
        nxt = order.index(through) + 1
        if nxt >= len(order) or order[nxt] != turn_id:
            self.bad(f"{kid}: the turn after prefix.through {through} must be checkpoint_turn {turn_id}")
        if replace != {turn_id: "user_line"}:
            self.bad(f"{kid}: prefix.replace_turn must be {{{turn_id}: user_line}}")
        return through

    def _graded(self, kid: str, km: dict, items: dict[str, Item], core_ids: list[str]) -> tuple[GradedItem, ...]:
        always_on = km.get("always_on") or {}
        if set(always_on) != set(core_ids):
            self.bad(f"{kid}: always_on must list exactly {core_ids}")
        graded: list[GradedItem] = []
        for iid in core_ids:
            expected = always_on.get(iid)
            if expected not in APPLICABILITY:
                self.bad(f"{kid}: always_on.{iid} {expected!r} not in {APPLICABILITY}")
                continue
            graded.append(GradedItem(items[iid], ROLE_ITEM, expected))
        own = [c.get("id") for c in km.get("criteria") or []]
        reused = list(km.get("criteria_reused") or [])
        for iid in own + reused:
            if iid not in items:
                self.bad(f"{kid}: criterion {iid} is not an item")
                continue
            it = items[iid]
            graded.append(GradedItem(it, ROLE_GATE if it.is_gate else ROLE_ITEM, "applies"))
        for iid in km.get("gate_check") or {}:
            if iid not in items or not items[iid].is_gate:
                self.bad(f"{kid}: gate_check {iid} is not a gate item")
                continue
            graded.append(GradedItem(items[iid], ROLE_GATE_FALSE_POSITIVE, "applies"))
        if len(graded) == len(core_ids):
            self.bad(f"{kid}: no criteria graded beyond the always-on items")
        ids = [g.item.id for g in graded]
        if len(ids) != len(set(ids)):
            self.bad(f"{kid}: an item is graded twice")
        return tuple(graded)

    def _run_checkpoints(self, checkpoints: dict[str, Checkpoint]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        run = self.raw.get("run") or {}
        default = tuple(run.get("checkpoints") or ())
        optional = tuple(run.get("optional_checkpoints") or ())
        if not default:
            self.bad("run: missing `checkpoints`")
        for k in default + optional:
            if k not in checkpoints:
                self.bad(f"run: checkpoint {k} is not a key moment")
        if set(default) & set(optional):
            self.bad("run: a checkpoint is both default and optional")
        for k in checkpoints:
            if k not in default and k not in optional:
                self.bad(f"run: key moment {k} is in neither `checkpoints` nor `optional_checkpoints`")
        return default, optional


def _expand(pattern: str, items: dict[str, Item]) -> list[str]:
    if pattern.endswith("*"):
        stem = pattern[:-1]
        return [i for i in items if i.startswith(stem)]
    return [pattern] if pattern in items else []
