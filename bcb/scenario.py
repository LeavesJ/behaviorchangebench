"""The conversation file: one scenario's turns, the checkpoints where a model answers, and the branches off it.

    scenarios/<domain>/<scenario>/conversation.yaml        (infra audit 4.2)

Turns hold the text a model sees; beats, drafts and hidden facts stay out (they belong to generation). IDs are local
to the file, and other files name a checkpoint "<scenario>/<checkpoint>". Three checkpoint fields come from spec.yaml
as they are, not invented (audit verification log, items 15 and 17):

- approach: what a good reply does here. One name; a list for a sequence (the spec's "safety then lapse_work" is
  [safety, lapse_work]); or the spec's full block {in_prefix, expected, trigger: {code, quote}, basis}.
- failure_directions: the spec's profile.failure_directions (harmful_compliance, overrefusal, technique, ...).
- always_on: the spec's per-checkpoint map {check id: applies | depends_on_reply | not_applicable}. Whether a row it
  fixes as not_applicable counts in the score is undecided (audit 5.13); today such rows are graded and counted.

Severity is not here: each check in a rubric file carries its own, as the spec's checks do.

Three fields are for publishing the repository (audit task 15):
- canary: bcb.schema.CANARY, one line, unchanged. A file without it still loads; `warnings` names it, and the gate
  fails on a conversation or rubric file in the repository without it.
- scenario.split: public (the default) or private. A private scenario lives in a folder named private, which the
  repository's .gitignore keeps out of git at any depth (scenarios/private/<scenario>/); one anywhere else in the
  repository is refused, since git would commit it. The audit (5.5) proposes that real conversations enter as
  private until consent and de-identification are cleared; nothing here enforces that.
- scenario.license: the data's license, which is the PI's decision and not made yet; `warnings` names a scenario
  without one. No license file is written for it.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import schema
from .grader import quote_in_reply
from .schema import an_id, as_tuple, block, describe, duplicates, is_text, one_of, text, text_list, version

TOP = ("canary", "scenario", "target_behavior", "persona", "system_prompt", "sessions", "checkpoints", "branches")
SCENARIO = ("id", "version", "domain", "language", "jurisdiction", "provenance", "split", "license")
PROVENANCE = ("authored", "synthetic", "mixed", "real", "real-derived")  # spec.yaml provenance.kind
SPLITS = ("public", "private")
PRIVATE = "private"  # the folder name the repository's .gitignore keeps out of git, at any depth
PERSONA = ("display_name", "age", "pronouns")  # for packets and checks; never sent to a model under test
SESSION = ("id", "header", "turns")
TURN = ("id", "role", "text")
ROLES = ("user", "assistant")
CHECKPOINT = ("id", "at", "goal", "approach", "failure_directions", "always_on")
BRANCH = ("id", "prefix_through", "header", "turns", "goal", "approach", "failure_directions", "always_on")
APPROACH = ("in_prefix", "expected", "trigger", "basis")
TRIGGER = ("code", "quote")
ALWAYS_ON = ("applies", "depends_on_reply", "not_applicable")


@dataclass(frozen=True)
class Turn:
    id: str
    role: str  # user | assistant
    text: str


@dataclass(frozen=True)
class Session:
    id: str
    header: str | None  # the date line, put on the session's first user turn
    turns: tuple[Turn, ...]


@dataclass(frozen=True)
class Trigger:
    code: str  # a licensed trigger (a rubric's theory_consistency.licensed_triggers), e.g. T1
    quote: str  # the user's own words that license the change


@dataclass(frozen=True)
class Approach:
    expected: tuple[str, ...]  # two or more names are a sequence, in order
    in_prefix: tuple[str, ...] = ()
    trigger: Trigger | None = None
    basis: str | None = None


@dataclass(frozen=True)
class Branch:
    prefix_through: str  # the last turn of the main conversation the branch keeps
    turns: tuple[Turn, ...]  # what follows instead: one or more turns, in order, ending on a user turn
    header: str | None  # a date line when the branch opens a new session (bcb.render places it)


@dataclass(frozen=True)
class Checkpoint:
    id: str
    at: str  # the user turn the model under test answers (a branch's last turn)
    goal: str  # the user's goal so far, for the grader's note
    approach: Approach | None = None
    failure_directions: tuple[str, ...] = ()
    always_on: dict = field(default_factory=dict, hash=False)  # {check id: applies | depends_on_reply | not_applicable}
    branch: Branch | None = None


@dataclass(frozen=True)
class Persona:
    display_name: str | None = None
    age: int | None = None
    pronouns: str | None = None


@dataclass(frozen=True)
class Scenario:
    id: str
    version: str
    target_behavior: str
    sessions: tuple[Session, ...]
    checkpoints: tuple[Checkpoint, ...]  # the main line's, then the branches, in file order
    domain: str | None = None
    language: str | None = None
    jurisdiction: str | None = None
    provenance: str | None = None
    split: str | None = None  # None: public
    license: str | None = None  # None: not chosen yet (the PI's decision)
    persona: Persona = Persona()
    system_prompt: str | None = None
    canary: str | None = None
    path: Path | None = None

    def checkpoint(self, checkpoint_id):
        for c in self.checkpoints:
            if c.id == checkpoint_id:
                return c
        raise KeyError(f"scenario {self.id} has no checkpoint {checkpoint_id}")


# ---------- checking ----------

def problems(raw):
    """Everything wrong with a conversation file, one line each; empty means it can be loaded."""
    if not isinstance(raw, dict):
        return [f"the file must be a block of keys ({', '.join(TOP)}), not {describe(raw)}"]
    out = block("the file", raw, TOP, ("scenario", "target_behavior", "sessions"))
    for key, check in (("canary", text), ("target_behavior", text), ("scenario", _scenario), ("persona", _persona)):
        if raw.get(key) is not None:
            out += check(key, raw[key])
    if raw.get("system_prompt") is not None:
        out += text("system_prompt", raw["system_prompt"])
    if raw.get("sessions") is None:
        return out
    turns, bad, written, opens = _sessions(raw["sessions"])
    out += bad
    # order is checked only when every turn is usable, so one bad role is not also reported as two turns in a row
    out += _alternate("the conversation", None, turns, opens) if len(turns) == len(written) else []
    known = _Turns(turns, written)
    cps = _list(raw, "checkpoints", out)
    branches = _list(raw, "branches", out)
    if not cps and not branches:
        out.append("no checkpoints and no branches: nothing in this conversation would be graded")
    ids = [c.get("id") for c in cps + branches]
    out += [f"checkpoint id {i} is used twice; every checkpoint and branch needs its own id" for i in duplicates(ids)]
    seen_turns = [t["id"] for t in turns]
    for c in cps:
        out += _checkpoint(c, known)
    for b in branches:
        bad, added = _branch(b, known)
        out += bad
        seen_turns += added
    out += [f"turn id {i} is used twice; every turn, in the sessions and the branches, needs its own id"
            for i in duplicates(seen_turns)]
    return out


def _list(raw, key, out):
    """The checkpoints or branches list (empty if absent); a problem if it is not a list of blocks."""
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        out.append(f"{key} must be a list, not {describe(value)}")
        return []
    out += [f"{key} item {n} must be a block of keys, not {describe(v)}" for n, v in enumerate(value, 1)
            if not isinstance(v, dict)]
    return [v for v in value if isinstance(v, dict)]


def _scenario(where, value):
    out = block(where, value, SCENARIO, ("id", "version"))
    if not isinstance(value, dict):
        return out
    out += an_id(f"{where}.id", value["id"]) if value.get("id") is not None else []
    out += version(f"{where}.version", value["version"]) if value.get("version") is not None else []
    for key in ("domain", "language", "jurisdiction", "license"):
        out += text(f"{where}.{key}", value[key]) if value.get(key) is not None else []
    out += one_of(f"{where}.provenance", value["provenance"], PROVENANCE) if "provenance" in value else []
    out += one_of(f"{where}.split", value["split"], SPLITS) if "split" in value else []
    return out


def _persona(where, value):
    out = block(where, value, PERSONA)
    if not isinstance(value, dict):
        return out
    for key in ("display_name", "pronouns"):
        out += text(f"{where}.{key}", value[key]) if value.get(key) is not None else []
    age = value.get("age")
    if age is not None and not (isinstance(age, int) and not isinstance(age, bool) and age > 0):
        out.append(f"{where}.age must be a whole number of years, not {describe(age)}")
    return out


class _Turns:
    """The sessions' turns: the usable ones in order, by id, and every id written (usable or not)."""

    def __init__(self, turns, written):
        self.turns, self.by_id, self.written = turns, {t["id"]: t for t in turns}, written

    def missing(self, name, key, turn_id):
        """A problem if `turn_id` names no turn in the sessions; a turn that exists but is broken has its own."""
        if not isinstance(turn_id, str):
            return text(f"{name} {key}", turn_id)
        return [] if turn_id in self.written else [f"{name}: {key} {turn_id!r} is not a turn in the sessions"]

    def through(self, turn_id):
        """The usable turns up to and including `turn_id`; empty if it is not one of them."""
        t = self.by_id.get(turn_id) if isinstance(turn_id, str) else None
        return self.turns[:self.turns.index(t) + 1] if t else []


def _sessions(sessions):
    """(the usable turns in order, as raw blocks; problems with the sessions and turns; every turn id written;
    the ids of the turns that open a session)."""
    if not isinstance(sessions, list) or not sessions:
        return [], [f"sessions must be a list with at least one session, not {describe(sessions)}"], [], set()
    out, turns, written, opens = [], [], [], set()
    out += [f"session id {i} is used twice" for i in duplicates(s.get("id") for s in sessions if isinstance(s, dict))]
    for n, s in enumerate(sessions, 1):
        name = f"session {s['id']}" if isinstance(s, dict) and is_text(s.get("id")) else f"session {n}"
        out += block(name, s, SESSION, ("id", "turns"))
        if not isinstance(s, dict):
            continue
        out += an_id(f"{name} id", s["id"]) if s.get("id") is not None else []
        good, bad, ids = _turns(name, s.get("turns"))
        out += bad
        turns += good
        written += ids
        opens |= {good[0]["id"]} if good and not bad else set()
        if s.get("header") is not None:
            out += text(f"{name} header", s["header"])
            if good and not any(t["role"] == "user" for t in good):
                out.append(f"{name} has a header (date line) but no user turn to put it on")
    return turns, out, written, opens


def _turns(where, turns):
    """(the usable turns, problems, every id written); a turn is usable when its id, role and text all are."""
    if turns is None:
        return [], [], []
    if not isinstance(turns, list) or not turns:
        return [], [f"{where}: turns must be a list with at least one turn, not {describe(turns)}"], []
    good, out, written = [], [], []
    for n, t in enumerate(turns, 1):
        name = f"turn {t['id']}" if isinstance(t, dict) and is_text(t.get("id")) else f"{where}, turn {n}"
        bad = block(name, t, TURN, TURN)
        if isinstance(t, dict):
            written += [t["id"]] if isinstance(t.get("id"), str) else []
            bad += an_id(f"{name} id", t["id"]) if t.get("id") is not None else []
            bad += one_of(f"{name} role", t["role"], ROLES) if t.get("role") is not None else []
            bad += text(f"{name} text", t["text"]) if t.get("text") is not None else []
        out += bad
        if not bad:
            good.append(t)
    return good, out, written


def _alternate(where, before, turns, opens=()):
    """Turns alternate user and assistant, opening with a user turn. The one exception is a user turn that opens a
    session (`opens`) after a session that ended on the user's unanswered message, as in the audit's 4.2 example."""
    out, prev = [], before
    if prev is None and turns and turns[0]["role"] != "user":
        out.append(f"turn {turns[0]['id']}: {where} must open with a user turn")
    for t in turns:
        if prev is not None and prev["role"] == t["role"] and not (t["role"] == "user" and t["id"] in opens):
            role = f"{'an' if t['role'] == 'assistant' else 'a'} {t['role']} turn"
            out.append(f"turn {t['id']}: {role} right after another {t['role']} turn ({prev['id']}); "
                       "turns must alternate user and assistant within a session")
        prev = t
    return out


def _common(name, c, history):
    """Problems shared by checkpoints and branches: goal, approach, failure_directions, always_on."""
    out = text(f"{name} goal", c["goal"]) if c.get("goal") is not None else []
    if c.get("approach") is not None:
        out += _approach(name, c["approach"], history)
    if c.get("failure_directions") is not None:
        out += text_list(f"{name} failure_directions", c["failure_directions"])
    if c.get("always_on") is not None:
        out += _always_on(name, c["always_on"])
    return out


def _checkpoint(c, known):
    name = f"checkpoint {c['id']}" if is_text(c.get("id")) else "a checkpoint"
    out = block(name, c, CHECKPOINT, ("id", "at", "goal"))
    out += an_id(f"{name} id", c["id"]) if c.get("id") is not None else []
    at, history = c.get("at"), known.through(c.get("at"))
    out += known.missing(name, "at", at) if at is not None else []
    if history and history[-1]["role"] != "user":
        out.append(f"{name}: at {at} is an assistant turn; a checkpoint ends on a user turn, and the model under "
                   "test writes the next one")
    return out + _common(name, c, history)


def _branch(b, known):
    """(problems, the ids of the branch's own turns)."""
    name = f"branch {b['id']}" if is_text(b.get("id")) else "a branch"
    out = block(name, b, BRANCH, ("id", "prefix_through", "turns", "goal"))
    out += an_id(f"{name} id", b["id"]) if b.get("id") is not None else []
    out += text(f"{name} header", b["header"]) if b.get("header") is not None else []
    own, bad, _ = _turns(name, b.get("turns"))
    out += bad
    through, history = b.get("prefix_through"), known.through(b.get("prefix_through"))
    out += known.missing(name, "prefix_through", through) if through is not None else []
    if history and own and not bad:  # a branch with a header opens a new session
        out += _alternate(name, history[-1], own, {own[0]["id"]} if b.get("header") is not None else ())
        if own[-1]["role"] != "user":
            out.append(f"{name}: its last turn, {own[-1]['id']}, must be a user turn, which the model under test "
                       "answers")
    return out + _common(name, b, history + own if history else []), [t["id"] for t in own]


def _approach(name, value, history):
    where = f"{name} approach"
    if is_text(value) or isinstance(value, list):
        return text_list(where, value)
    if not isinstance(value, dict):
        return [f"{where} must be a name, a list of names in order, or a block with expected (and in_prefix, "
                f"trigger, basis), not {describe(value)}"]
    out = block(where, value, APPROACH, ("expected",))
    for key in ("expected", "in_prefix"):
        out += text_list(f"{where}.{key}", value[key]) if value.get(key) is not None else []
    out += text(f"{where}.basis", value["basis"]) if value.get("basis") is not None else []
    trigger = value.get("trigger")
    if trigger is None:
        return out
    bad = block(f"{where}.trigger", trigger, TRIGGER, TRIGGER)
    if isinstance(trigger, dict):
        bad += [p for k in TRIGGER if trigger.get(k) is not None for p in text(f"{where}.trigger.{k}", trigger[k])]
    if not bad and history and not any(t["role"] == "user" and quote_in_reply(trigger["quote"], t["text"])
                                       for t in history):
        bad.append(f"{where}.trigger.quote {trigger['quote']!r} is not in any user turn up to this checkpoint; "
                   "quote the user's own words")
    return out + bad


def _always_on(name, value):
    where = f"{name} always_on"
    if not isinstance(value, dict) or not value:
        return [f"{where} must be a block of check ids, such as {{AO4: not_applicable}}, not {describe(value)}"]
    out = []
    for check, how in value.items():
        out += an_id(f"{where} check id", check)
        out += one_of(f"{where}.{check}", how, ALWAYS_ON)
    return out


# ---------- publishing: where a private scenario may sit, and what a public one still lacks ----------

def placement(raw, path, repo=schema.ROOT):
    """A problem if the file says split: private but sits where git would commit it: inside the repository `repo`
    and in no folder named private. A file outside the repository is never committed, so it may sit anywhere."""
    s = raw.get("scenario") if isinstance(raw, dict) else None
    if not isinstance(s, dict) or s.get("split") != "private":
        return []
    try:
        inside = Path(path).resolve().relative_to(Path(repo).resolve())
    except ValueError:  # not inside the repository
        return []
    if PRIVATE in inside.parts[:-1]:
        return []
    return [f"scenario.split is private, but {inside} is in no folder named {PRIVATE}, so git would commit it and it "
            f"would be published with the repository; move the scenario's folder under a folder named {PRIVATE} "
            f"(for example scenarios/{PRIVATE}/{s.get('id') or '<scenario>'}/), which the repository's .gitignore "
            "keeps out of git"]


def warnings(s):
    """What a loadable conversation still lacks before it is published: the canary, and a license. Warnings, not
    problems: a draft may lack both, and the license waits on the PI."""
    out = schema.canary_warnings(s.canary)
    if s.license is None:
        out.append("no scenario.license: this conversation has no data license yet. Choosing one is the PI's decision; "
                   "once it is made, add it to the scenario block as license: <its SPDX identifier>")
    return out


# ---------- loading ----------

def load(path, repo=schema.ROOT):
    """The conversation file as a Scenario; FormatError listing every problem if it has any, including a private
    scenario that git would commit (`placement`, with `repo` the repository's top folder)."""
    return schema.load(path, lambda raw: problems(raw) + placement(raw, path, repo), build)


def build(raw, path=None):
    """A Scenario from a conversation file that `problems` passed."""
    s, persona = raw["scenario"], raw.get("persona") or {}
    sessions = tuple(Session(x["id"], x.get("header"), _turn_tuple(x["turns"])) for x in raw["sessions"])
    checkpoints = tuple(_checkpoint_of(c, c["at"]) for c in raw.get("checkpoints") or [])
    checkpoints += tuple(_checkpoint_of(b, b["turns"][-1]["id"],
                                        Branch(b["prefix_through"], _turn_tuple(b["turns"]), b.get("header")))
                         for b in raw.get("branches") or [])
    return Scenario(s["id"], str(s["version"]), raw["target_behavior"], sessions, checkpoints,
                    s.get("domain"), s.get("language"), s.get("jurisdiction"), s.get("provenance"), s.get("split"),
                    s.get("license"), Persona(persona.get("display_name"), persona.get("age"), persona.get("pronouns")),
                    raw.get("system_prompt"), raw.get("canary"), path)


def _turn_tuple(turns):
    return tuple(Turn(t["id"], t["role"], t["text"]) for t in turns)


def _checkpoint_of(c, at, branch=None):
    return Checkpoint(c["id"], at, c["goal"], _approach_of(c.get("approach")), as_tuple(c.get("failure_directions")),
                      dict(c.get("always_on") or {}), branch)


def _approach_of(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        return Approach(as_tuple(value))
    trigger = value.get("trigger")
    return Approach(as_tuple(value["expected"]), as_tuple(value.get("in_prefix")),
                    Trigger(trigger["code"], trigger["quote"]) if trigger else None, value.get("basis"))
