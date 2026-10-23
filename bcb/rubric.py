"""A rubric file: checks that apply always (narrowed by approach) or at named checkpoints.

    rubrics/core-mi.yaml                                  one file per framework, reused across scenarios
    scenarios/<domain>/<scenario>/checks.yaml             one scenario's own checks, same shape   (infra audit 4.2)

A check's fields follow spec.yaml's items and HealthBench (criterion, points, tags), plus what the spec already
records for checks: `severity` (the S1-S3 ladder of docs/design/04) and the item's own `default_when_unsure`.
`group: core` replaces the "AO" prefix. `approach` narrows a check that applies always to checkpoints whose
approach is listed; a check that names its checkpoints under `at` is not narrowed. The optional
`theory_consistency` block is spec.yaml rubrics.theory_consistency: the approach names and licensed triggers that
conversation files may use.

Gates carry no points: they act through the gate rule, and whether a failed gate caps a model's rank is undecided
(the run file's gates.cap, off by default; bcb.gates applies it when asked).

A check id is local to the scenarios it is graded in: as graded in a scenario, its global id is
"<scenario>/<check id>" (bcb.loader). So two scenarios' own check files may each use an id, but within one scenario
an id names one check, which `clashes` checks across the rubrics of a run.

Like a conversation file, a rubric file carries the canary (bcb.schema.CANARY) at the top, on one line; a file
without it still loads, `warnings` names it, and the gate fails on a rubric file in the repository without it.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import grader, schema
from .schema import REF, an_id, as_tuple, block, describe, duplicates, is_text, one_of, text, text_list, version

TOP = ("canary", "rubric", "defaults", "theory_consistency", "checks")
RUBRIC = ("id", "version")
DEFAULTS = ("default_when_unsure", "na_codes")
UNSURE = ("desired", "undesired", "gate")
THEORY = ("approaches", "licensed_triggers", "unlicensed_patterns")
CHECK = ("id", "version", "type", "severity", "applies", "approach", "group", "polarity", "points", "criterion",
         "default_when_unsure", "source", "tags")
APPLIES = ("always", "at", "false_positive_at", "when", "na_code")
TYPES = ("fidelity", "gate")
POLARITIES = ("desired", "undesired")
SEVERITIES = ("S1", "S2", "S3")  # docs/design/04-severity-and-overrefusal.md, the strawman ladder
MAX_POINTS = 10  # HealthBench's range, -10 to 10 (spec.yaml frameworks.HealthBench)


@dataclass(frozen=True)
class Applies:
    always: bool  # every checkpoint whose approach the check lists (no list: every checkpoint)
    at: tuple[str, ...] = ()  # "<scenario>/<checkpoint>"
    false_positive_at: tuple[str, ...] = ()  # gates only: graded, expected not_met, reported apart from the gate rule
    when: str | None = None  # the condition; the grader may answer not_applicable when it does not hold
    na_code: str | None = None


@dataclass(frozen=True)
class Check:
    id: str
    version: str
    criterion: str
    polarity: str  # desired | undesired
    type: str  # fidelity | gate
    points: int | None  # None for gates
    applies: Applies
    default_when_unsure: str  # the check's own tie-break, else the rubric's default for its type and polarity
    approach: tuple[str, ...] = ()
    group: str | None = None
    severity: str | None = None
    source: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TheoryConsistency:
    approaches: tuple[str, ...]
    licensed_triggers: dict = field(default_factory=dict, hash=False)  # {code: what licenses the change}
    unlicensed_patterns: dict = field(default_factory=dict, hash=False)


@dataclass(frozen=True)
class Rubric:
    id: str
    version: str
    checks: tuple[Check, ...]
    na_codes: tuple[str, ...]
    theory_consistency: TheoryConsistency | None = None
    canary: str | None = None
    path: Path | None = None


# ---------- checking ----------

def problems(raw):
    """Everything wrong with a rubric file, one line each; empty means it can be loaded."""
    if not isinstance(raw, dict):
        return [f"the file must be a block of keys ({', '.join(TOP)}), not {describe(raw)}"]
    out = block("the file", raw, TOP, ("rubric", "checks"))
    out += text("canary", raw["canary"]) if raw.get("canary") is not None else []
    head = raw.get("rubric")
    if head is not None:
        out += block("rubric", head, RUBRIC, RUBRIC)
        if isinstance(head, dict):
            out += an_id("rubric.id", head["id"]) if head.get("id") is not None else []
            out += version("rubric.version", head["version"]) if head.get("version") is not None else []
    bad, unsure, na_codes = _defaults(raw.get("defaults"))
    out += bad
    bad, approaches = _theory(raw.get("theory_consistency"))
    out += bad
    checks = raw.get("checks")
    if checks is None:
        return out
    if not isinstance(checks, list) or not checks:
        return out + [f"checks must be a list with at least one check, not {describe(checks)}"]
    out += [f"check id {i} is used twice; a check id is never reused" for i in duplicates(
        c.get("id") for c in checks if isinstance(c, dict))]
    for n, c in enumerate(checks, 1):
        out += _check(n, c, unsure, na_codes, approaches)
    return out


def _defaults(value):
    """(problems, {desired|undesired|gate: tie-break}, the NA codes checks may use)."""
    if value is None:
        return [], {}, grader.NA_CODES
    out = block("defaults", value, DEFAULTS)
    if not isinstance(value, dict):
        return out, {}, grader.NA_CODES
    unsure = value.get("default_when_unsure")
    if unsure is not None:
        out += block("defaults.default_when_unsure", unsure, UNSURE)
    unsure = unsure if isinstance(unsure, dict) else {}
    for kind in (k for k in UNSURE if unsure.get(k) is not None):
        out += text(f"defaults.default_when_unsure.{kind}", unsure[kind])
    out += [f"defaults.default_when_unsure.{kind} is cannot_determine, which the grader may answer only on a gate; "
            f"on a {kind} check that answer is refused as an error" for kind in POLARITIES
            if _undetermined(unsure.get(kind))]
    codes = value.get("na_codes")
    if codes is None:
        return out, unsure, grader.NA_CODES
    bad = text_list("defaults.na_codes", codes)
    if bad:
        return out + bad, unsure, grader.NA_CODES
    # the grader's parser accepts only its own codes (bcb/grader.py NA_CODES), so another would fail every verdict
    out += [f"defaults.na_codes: {c} is not a code the grader accepts yet ({', '.join(grader.NA_CODES)})"
            for c in as_tuple(codes) if is_text(c) and c not in grader.NA_CODES]
    return out, unsure, as_tuple(codes)


def _theory(value):
    """(problems, the approach names checks may list, or None when the rubric declares none)."""
    if value is None:
        return [], None
    out = block("theory_consistency", value, THEORY, ("approaches",))
    if not isinstance(value, dict):
        return out, None
    names = value.get("approaches")
    bad = text_list("theory_consistency.approaches", names) if names is not None else []
    out += bad
    for key in ("licensed_triggers", "unlicensed_patterns"):
        codes = value.get(key)
        if codes is None:
            continue
        if not isinstance(codes, dict) or not codes:
            out.append(f"theory_consistency.{key} must be a block of codes, each with what it means, such as "
                       f"{{T1: readiness}}, not {describe(codes)}")
            continue
        for code, meaning in codes.items():
            out += an_id(f"theory_consistency.{key} code", code) + text(f"theory_consistency.{key}.{code}", meaning)
    return out, set(as_tuple(names)) if names is not None and not bad else None


def _check(n, c, unsure, na_codes, approaches):
    name = f"check {c['id']}" if isinstance(c, dict) and is_text(c.get("id")) else f"check {n}"
    out = block(name, c, CHECK, ("id", "version", "applies", "polarity", "criterion"))
    if not isinstance(c, dict):
        return out
    out += an_id(f"{name} id", c["id"]) if c.get("id") is not None else []
    out += version(f"{name} version", c["version"]) if c.get("version") is not None else []
    out += text(f"{name} criterion", c["criterion"]) if c.get("criterion") is not None else []
    out += one_of(f"{name} type", c["type"], TYPES) if "type" in c else []
    out += one_of(f"{name} polarity", c["polarity"], POLARITIES) if c.get("polarity") is not None else []
    out += text(f"{name} group", c["group"]) if c.get("group") is not None else []
    out += [p for k in ("source", "tags") if c.get(k) is not None for p in text_list(f"{name} {k}", c[k])]
    gate = c.get("type") == "gate"
    out += _points(name, c, gate)
    if c.get("severity") is not None:
        out += one_of(f"{name} severity", c["severity"], SEVERITIES)
    elif gate:
        out.append(f"{name}: a gate needs a severity, one of {', '.join(SEVERITIES)} (docs/design/04)")
    if c.get("default_when_unsure") is not None:
        out += text(f"{name} default_when_unsure", c["default_when_unsure"])
        if not gate and _undetermined(c["default_when_unsure"]):
            out.append(f"{name} default_when_unsure is cannot_determine, which the grader may answer only on a gate "
                       "(on any other check that answer is refused as an error); use not_met or met")
    elif c.get("polarity") in POLARITIES and not unsure.get("gate" if gate else c["polarity"]):
        out.append(f"{name}: no rule for when the grader is unsure; give the check a default_when_unsure, or the "
                   f"rubric a defaults.default_when_unsure.{'gate' if gate else c['polarity']}")
    applies = c.get("applies")
    out += _applies(name, applies, gate, na_codes) if applies is not None else []
    if c.get("approach") is not None:
        out += _approach(name, c["approach"], applies, approaches)
    return out


def _undetermined(rule):
    """A tie-break telling the grader to answer cannot_determine, which bcb/grader.py parse refuses on all but gates."""
    return is_text(rule) and rule.strip().startswith("cannot_determine")


def _points(name, c, gate):
    points = c.get("points")
    if gate:
        return [f"{name}: a gate has no points, since it acts through the gate rule and not the points score; "
                "write points: null or leave it out"] if points is not None else []
    if points is None:
        return [f"{name}: missing points (a whole number from -{MAX_POINTS} to {MAX_POINTS} other than 0)"]
    if not isinstance(points, int) or isinstance(points, bool) or points == 0 or abs(points) > MAX_POINTS:
        return [f"{name} points must be a whole number from -{MAX_POINTS} to {MAX_POINTS} other than 0 "
                f"(HealthBench's range), not {describe(points)}"]
    if c.get("polarity") in POLARITIES and (points > 0) != (c["polarity"] == "desired"):
        return [f"{name} points must be positive for a desired check and negative for an undesired one "
                f"(it is {c['polarity']} with {points})"]
    return []


def _applies(name, value, gate, na_codes):
    where = f"{name} applies"
    if value == "always":
        return []
    if not isinstance(value, dict):
        return [f"{where} must be always, or a block with always: true or at: [<scenario>/<checkpoint>, ...], "
                f"not {describe(value)}"]
    out = block(where, value, APPLIES)
    always, at = value.get("always"), value.get("at")
    if always is not None and always is not True:
        out.append(f"{where}.always must be true; to limit the check to some checkpoints, list them under at")
    elif always is True and at is not None:
        out.append(f"{where}: give always: true or at: [...], not both")
    elif always is None and at is None:
        out.append(f"{where}: say where the check applies, with always: true or at: [<scenario>/<checkpoint>, ...]")
    refs = {}  # the well-formed lists of checkpoint references, as sets
    for key in ("at", "false_positive_at"):
        if value.get(key) is not None:
            bad = _refs(f"{where}.{key}", value[key])
            out += bad
            refs[key] = set() if bad else set(as_tuple(value[key]))
    if "false_positive_at" in refs:
        if not gate:
            out.append(f"{where}.false_positive_at is for gates only: the checkpoints where a gate is graded to see "
                       "whether it fires wrongly")
        both = refs["false_positive_at"] & refs.get("at", set())
        out += [f"{where}: {r} is under both at and false_positive_at" for r in sorted(both)]
    out += text(f"{where}.when", value["when"]) if value.get("when") is not None else []
    if value.get("na_code") is not None:
        out += one_of(f"{where}.na_code", value["na_code"], na_codes)
        if value.get("when") is None:
            out.append(f"{where}: na_code needs a when, saying when the check applies, so the grader knows when "
                       "not_applicable is the right answer")
    return out


def _refs(where, value):
    """Checkpoint references, each "<scenario>/<checkpoint>"; one may be written without the brackets."""
    bad = text_list(where, value)
    if bad:
        return bad
    out = []
    for n, ref in enumerate(as_tuple(value), 1):
        m = REF.fullmatch(ref)
        if not m:
            out.append(f"{where} item {n} {ref!r} must name the scenario and the checkpoint, as "
                       "<scenario>/<checkpoint> (for example walking-v0/C1)")
        else:
            out += an_id(f"{where} item {n} scenario", m[1]) + an_id(f"{where} item {n} checkpoint", m[2])
    return out


def _approach(name, value, applies, approaches):
    out = text_list(f"{name} approach", value)
    if isinstance(applies, dict) and applies.get("always") is not True:
        out.append(f"{name}: approach narrows only a check that applies always; this one names its checkpoints, "
                   "so leave approach out")
    if not out and approaches is not None:
        out += [f"{name} approach {a!r} is not one of this rubric's approaches ({', '.join(sorted(approaches))})"
                for a in as_tuple(value) if a not in approaches]
    return out


# ---------- across the rubrics of a run ----------

def reaches(check, scenario_id):
    """Whether a check can be graded in a scenario: it applies always, or names one of the scenario's checkpoints."""
    named = check.applies.at + check.applies.false_positive_at
    return check.applies.always or any(ref.split("/", 1)[0] == scenario_id for ref in named)


def clashes(rubrics, scenario_ids):
    """Check ids that name two checks within one scenario, one line each. Two scenarios may each have a check with
    one id, since each is graded under its own scenario's name; one scenario cannot."""
    twice = duplicates(r.id for r in rubrics)
    if twice:  # the same file listed twice, most likely; every one of its checks would clash with itself
        return [f"rubrics: two files have rubric id {i}" for i in twice]
    out = []
    for s in scenario_ids:
        where = {}  # {check id: the rubrics with a check of that id that can be graded in this scenario}
        for r in rubrics:
            for c in (c for c in r.checks if reaches(c, s)):
                where.setdefault(c.id, []).append(r.id)
        out += [f"scenario {s}: check id {i} is in rubric {found[0]} and in rubric {found[1]}, and both can be graded "
                "in this scenario; give one of them another id" for i, found in where.items() if len(found) > 1]
    return out


# ---------- publishing ----------

def warnings(r):
    """What a loadable rubric still lacks before it is published: the canary (bcb.schema.CANARY)."""
    return schema.canary_warnings(r.canary)


# ---------- loading ----------

def load(path):
    """The rubric file as a Rubric; FormatError listing every problem if it has any."""
    return schema.load(path, problems, build)


def build(raw, path=None):
    """A Rubric from a file that `problems` passed."""
    defaults = raw.get("defaults") or {}
    unsure = defaults.get("default_when_unsure") or {}
    theory = raw.get("theory_consistency")
    return Rubric(raw["rubric"]["id"], str(raw["rubric"]["version"]),
                  tuple(_check_of(c, unsure) for c in raw["checks"]),
                  as_tuple(defaults.get("na_codes")) or grader.NA_CODES,
                  TheoryConsistency(as_tuple(theory["approaches"]), dict(theory.get("licensed_triggers") or {}),
                                    dict(theory.get("unlicensed_patterns") or {})) if theory else None,
                  raw.get("canary"), path)


def _check_of(c, unsure):
    a = c["applies"]
    applies = Applies(True) if a == "always" else Applies(
        a.get("always") is True, as_tuple(a.get("at")), as_tuple(a.get("false_positive_at")), a.get("when"),
        a.get("na_code"))
    kind = c.get("type", "fidelity")
    return Check(c["id"], str(c["version"]), c["criterion"], c["polarity"], kind, c.get("points"), applies,
                 c.get("default_when_unsure") or unsure["gate" if kind == "gate" else c["polarity"]],
                 as_tuple(c.get("approach")), c.get("group"), c.get("severity"), as_tuple(c.get("source")),
                 as_tuple(c.get("tags")))
