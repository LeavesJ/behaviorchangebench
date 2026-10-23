"""Write vaping-v0 into the new file formats, checked against today's code before anything is written.

    python -m bcb.convert            # write trajectories/vaping-v0/v2/
    python -m bcb.convert --check    # are the files there what it would write now? writes nothing; exit 1 if not

It reads spec.yaml with the frozen conversation and the messy-pilot branches carried into Oct 8, as
bcb.labels.load_spec builds them, and the manifests of the three graded Oct 8 folders, and writes five files
(infra audit 4.2, task 3):

- conversation.yaml: the sessions as frozen text, the main-line checkpoints, and KM7-T, KM7-P and the carried
  branches as branches. Each checkpoint keeps the spec's goal, approach (a sequence such as "safety then lapse_work"
  becomes a list), failure directions and always_on map.
- core.yaml: AO1-AO4 as group core, with spec rubrics.theory_consistency (approach names, licensed triggers).
- checks.yaml: every other check. A check reused at another checkpoint lists both under `at`, its own first, and a
  gate graded where it should not fire (KM7-G1 at KM7-T) lists that checkpoint under `false_positive_at`.
- models.yaml: the models and the grader the manifests record, each with its settings.
- oct8-repro.yaml: the run file, with the checkpoints, samples and grader reading the manifests record.

The three data files (conversation, core, checks) carry the canary (bcb.schema.CANARY); the models config and run
file hold no benchmark content and carry none. The conversation has no split, so it is public, and no license, since
the data license is the PI's decision: `python -m bcb.validate` warns about that until one is chosen.

Before writing, it reads its own output back through bcb.runfile and refuses unless, at every checkpoint, the model
input, the grader note and every check's grader prompt (on one fixed reply) are byte for byte what bcb.spec builds
today, bcb.loader grades the same checks in the same order, and each check keeps its points, polarity, type,
condition and tie-break.

Spec fields the new files have no place for (titles, at_stake, good and bad replies, expert notes, hidden facts,
beats, live-mode conditions) stay in spec.yaml, which remains the source: edit it and rerun. Two scoring rules the
team has not decided are carried as the Oct 8 numbers used them, not settled: rows an always_on map fixes as
not_applicable are graded and counted (static_na: count), and no gate caps a rank (gates.cap: none).
"""

import argparse
import dataclasses
import hashlib
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import yaml

from . import branches, grader, labels as lb, loader, render, review, runfile, schema
from .spec import Spec, load_conversation

ROOT = runfile.ROOT
FOLDER = "trajectories/vaping-v0/v2"  # from the repository's top folder, as the run file names it
CARRIED = ("MS-DECIDE", "MS-WHOLEPLAN", "MS-LAPSE")  # carried from messy-pilot.yaml into Oct 8 (its header, Oct 2)
FILES = {"conversation": "conversation.yaml", "core": "core.yaml", "checks": "checks.yaml", "models": "models.yaml",
         "run": "oct8-repro.yaml"}
# the graded folders must agree on these, or there is no one Oct 8 setup to write down
SAME_IN_EVERY_MANIFEST = ("grader", "grader_sampling", "reply_sampling", "prompt", "samples", "grader_read")
REPLY = "One fixed reply, the same on both sides, so two grader prompts differ only in what surrounds it."


class ConversionError(ValueError):
    """The converter refused; `problems` holds one line per reason."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("the converter wrote nothing:\n  " + "\n  ".join(self.problems))


# ---------- YAML that reads like spec.yaml ----------

class _Flow(dict):
    """A small block written on one line, {a: b, c: d}."""


class _FlowList(list):
    """A short list written on one line, [a, b]."""


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):  # list items indented under their key
        return super().increase_indent(flow, False)

    def ignore_aliases(self, data):  # a block used twice is written twice, never as &id001 / *id001
        return True


_Dumper.add_representer(_Flow, lambda d, v: d.represent_mapping("tag:yaml.org,2002:map", v, flow_style=True))
_Dumper.add_representer(_FlowList, lambda d, v: d.represent_sequence("tag:yaml.org,2002:seq", v, flow_style=True))


def dump(raw, header):
    body = yaml.dump(raw, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=110)
    return "".join(f"# {line}".rstrip() + "\n" for line in header) + body


# ---------- reading the sources ----------

def load_sources(spec_path=lb.SPEC, conversation_path=lb.CONVERSATION, branches_path=lb.MESSY, carried=CARRIED):
    """(the spec with its frozen conversation and the carried branches attached, the branches file)."""
    spec = Spec(spec_path, conversation=load_conversation(conversation_path))
    raw = branches.load(branches_path)
    kept = [b for b in raw["branches"] if b["id"] in carried]
    missing = [c for c in carried if c not in {b["id"] for b in kept}]
    if missing:
        raise ConversionError([f"{Path(branches_path).name} has no branch {', '.join(missing)} to carry"])
    branches.attach(spec, dict(raw, branches=kept))
    return spec, raw


# ---------- the conversation file ----------

def conversation(spec):
    """(the conversation file, problems): sessions as text, then checkpoints and branches with the spec's fields."""
    raw, target, persona = spec.raw, spec.raw["target"], spec.raw["persona"]
    pinned, generated, problems = spec.pinned_lines(), spec.generated or {}, []
    sessions, opens = [], {}  # opens: {the turn a session opens with: its date line}
    for s in raw["sessions"]:
        turns = []
        for t in s["turns"]:
            text = pinned.get(t["id"], generated.get(t["id"]))  # bcb.spec's order: the pinned line wins
            if text is None:
                problems.append(f"turn {t['id']} has no text in the conversation file; the new files hold the frozen "
                                "text, not beats or drafts, so freeze the conversation first")
            turns.append({"id": t["id"], "role": t["role"], "text": text})
        sessions.append({"id": s["id"], "header": s["when"]["header"], "turns": turns})
        opens[next((t["id"] for t in s["turns"] if t["role"] == "user"), None)] = s["when"]["header"]
    checkpoints, branch_list = [], []
    for m in raw["key_moments"]:
        fields = {"goal": target["goal_at_checkpoint"][m["id"]], **_moment_fields(m)}
        if not m.get("prefix"):
            checkpoints.append({"id": m["id"], "at": m["checkpoint_turn"], **fields})
            continue
        # a variant: the conversation through one turn, then its own line in place of the next turn
        (replaced, line), = m["prefix"]["replace_turn"].items()
        b = {"id": m["id"], "prefix_through": m["prefix"]["through"]}
        if opens.get(replaced):  # its line opens a session, so it keeps that session's date line, as bcb.spec does
            b["header"] = opens[replaced]
        b["turns"] = [{"id": f"{m['id']}.U1", "role": "user", "text": m["user_line"] if line == "user_line" else line}]
        branch_list.append({**b, **fields})
    out = {"canary": schema.CANARY,
           "scenario": _Flow(id=raw["spec"]["id"], version=str(spec.version), domain=target["domain"],
                             language=target["language"], jurisdiction=target["jurisdiction"],
                             provenance=raw["provenance"]["kind"]),
           "target_behavior": target["target_behavior"],
           # the first name, as the label packets and Sheets call her
           "persona": _Flow(display_name=persona["name"].split()[0], age=persona["age"], pronouns=persona["pronouns"]),
           "sessions": sessions, "checkpoints": checkpoints, "branches": branch_list}
    return out, problems


def _moment_fields(m):
    out = {}
    if m.get("approach"):
        out["approach"] = _approach(m["approach"])
    directions = (m.get("profile") or {}).get("failure_directions")
    if directions:
        out["failure_directions"] = _FlowList(directions)
    if m.get("always_on"):
        out["always_on"] = _Flow(m["always_on"])
    return out


def _approach(a):
    out = {k: _sequence(a[k]) for k in ("in_prefix", "expected") if a.get(k) is not None}
    if a.get("trigger"):
        out["trigger"] = _Flow(code=a["trigger"]["code"], quote=a["trigger"]["quote"])
    if a.get("basis"):
        out["basis"] = a["basis"]
    return out


def _sequence(value):
    """'safety then lapse_work' as [safety, lapse_work], in order; one name stays a name."""
    names = [n.strip() for n in str(value).split(" then ")]
    return names[0] if len(names) == 1 else _FlowList(names)


# ---------- the rubric files ----------

def rubrics(spec):
    """(core.yaml, checks.yaml): the always-on checks as group core, and every key moment's and branch's own checks."""
    raw, scenario_id = spec.raw, spec.raw["spec"]["id"]
    d = raw["criterion_defaults"]
    defaults = {"default_when_unsure": _Flow(d["default_when_unsure"]),
                "na_codes": _FlowList(d["not_applicable_codes"])}
    theory = raw["rubrics"]["theory_consistency"]  # checkpoint_scope and the live-mode TC-01 have no place yet
    core = {"canary": schema.CANARY, "rubric": _Flow(id=f"{scenario_id}-core", version=str(spec.version)),
            "defaults": defaults,
            "theory_consistency": {"approaches": _FlowList(theory["approaches"]),
                                   **{k: theory[k] for k in ("licensed_triggers", "unlicensed_patterns")}},
            "checks": [_check(a, d, always=True) for a in raw["always_on_metrics"]]}
    at, false_positive_at = _where(spec, scenario_id)
    own = [c for m in raw["key_moments"] for c in m.get("criteria") or []]
    checks = {"canary": schema.CANARY, "rubric": _Flow(id=f"{scenario_id}-checks", version=str(spec.version)),
              "defaults": defaults,
              "checks": [_check(c, d, at=at[c["id"]], false_positive_at=false_positive_at[c["id"]]) for c in own]}
    return core, checks


def _where(spec, scenario_id):
    """({check id: checkpoints it is graded at}, {check id: checkpoints where it is graded as a false-positive check}),
    from each key moment's own criteria, criteria_reused and gate_check, as Spec.items_at reads them. The moment that
    owns a check comes first under at, since bcb.loader reads the first checkpoint a check names as its own."""
    at, false_positive_at = defaultdict(list), defaultdict(list)
    for m in spec.raw["key_moments"]:
        for c in m.get("criteria") or []:
            at[c["id"]].append(f"{scenario_id}/{m['id']}")
    for m in spec.raw["key_moments"]:
        ref = f"{scenario_id}/{m['id']}"
        reused = list(m.get("criteria_reused") or [])
        for i in reused:
            at[i].append(ref)
        graded = [c["id"] for c in m.get("criteria") or []] + reused
        for i in (i for i in m.get("gate_check") or {} if i not in graded):
            false_positive_at[i].append(ref)
    return at, false_positive_at


def _check(c, defaults, always=False, at=(), false_positive_at=()):
    """One spec item as a rubric check; the fields bcb.grader reads are carried exactly."""
    gate = not always and c.get("type", defaults["type"]) == "gate"  # bcb.spec reads every always-on item as fidelity
    out = {"id": c["id"], "version": str(c.get("version", defaults["version"]))}
    if gate:
        out["type"] = "gate"
    if c.get("severity") is not None:
        out["severity"] = c["severity"]
    # the spec's condition, written only where it is not the phrase that means "no condition"
    when = c.get("applies_when", "always" if always else defaults["scope"]["applies_when"])
    condition = {"when": when} if c.get("not_applicable") is not None or when != render.NO_CONDITION[always] else {}
    if c.get("not_applicable") is not None:
        condition["na_code"] = c["not_applicable"]
    if always:
        out["applies"] = _Flow(always=True, **condition) if condition else "always"
        out["group"] = "core"  # in place of the "AO" prefix
    else:
        out["applies"] = _Flow(at=_FlowList(at), **({"false_positive_at": _FlowList(false_positive_at)}
                                                    if false_positive_at else {}), **condition)
    out["polarity"] = c["polarity"]
    if not gate:  # a gate carries no points
        out["points"] = c.get("points")
    out["criterion"] = c["criterion"]
    if "default_when_unsure" in c:
        out["default_when_unsure"] = c["default_when_unsure"]
    for key in ("source", "tags"):
        if c.get(key):
            out[key] = _FlowList(str(v) for v in c[key])
    return out


def severity_problems(spec):
    """A key moment's profile.severity names each check's tier; the new files keep it on the check, so they agree."""
    items = {c["id"]: c for m in spec.raw["key_moments"] for c in m.get("criteria") or []}
    out = []
    for m in spec.raw["key_moments"]:
        named = (m.get("profile") or {}).get("severity")
        for i, tier in (named.items() if isinstance(named, dict) else ()):
            own = (items.get(i) or {}).get("severity")
            if str(tier).split()[0] != own:
                out.append(f"{m['id']}: profile.severity gives {i} as {tier!r}, but the check itself says {own!r}; "
                           "the new files keep severity on the check, so make the two agree")
    return out


# ---------- the models config and the run file, from the Oct 8 manifests ----------

def from_manifests(graded, folder, scenario_id):
    """(models config, run file, problems) from what the graded folders' manifests record."""
    manifests = [json.loads((Path(d) / "manifest.json").read_text()) for d in graded]
    problems = [f"the graded manifests disagree on {k}, so there is no one setup to write down" for k in
                SAME_IN_EVERY_MANIFEST if len({json.dumps(m.get(k), sort_keys=True) for m in manifests}) > 1]
    first = manifests[0]
    if first["prompt"] != {"version": grader.PROMPT_VERSION, "sha": grader.prompt_hash()}:
        problems.append(f"the manifests' grader prompt is {first['prompt']}, but this code's is "
                        f"{grader.PROMPT_VERSION} {grader.prompt_hash()}")
    read = set(first["grader_read"])
    if len(read) != 1:
        problems.append(f"the grader read {', '.join(sorted(read))} text in one run; a run file names one")
    targets = list(dict.fromkeys(t for m in manifests for t in m["models"]))
    models = {"models": [{"id": _entry(t)["model"].rsplit("/", 1)[-1], **_entry(t),
                          "sampling": _sampling(first["reply_sampling"])} for t in targets],
              "graders": {"primary": {**_entry(first["grader"]), "prompt": first["prompt"]["version"],
                                      "sampling": _sampling(first["grader_sampling"], judge=True)}},
              "rules": _Flow(grader_family_differs_from_every_model=True)}
    run = {"run": _Flow(id="oct8-repro", out="runs/"), "scenarios": _FlowList([folder]),
           "checkpoints": _FlowList(dict.fromkeys(f"{scenario_id}/{c}" for m in manifests for c in m["checkpoints"])),
           "rubrics": _FlowList(f"{folder}/{FILES[k]}" for k in ("core", "checks")),
           "models": f"{folder}/{FILES['models']}", "samples": first["samples"],
           "grade": _Flow({"with": "primary", "read": min(read)}),
           "gates": _Flow(rule="pass_k", k=first["samples"], cap="none"), "static_na": "count"}
    return models, run, problems


def _entry(target):
    """provider, model and family of a "provider:model" target; the family is the vendor an OpenRouter name shows."""
    provider, model = target.split(":", 1)
    family = model.split("/", 1)[0] if provider == "openrouter" and "/" in model else provider
    return {"provider": provider, "model": model, "family": family}


def _sampling(s, judge=False):
    """A manifest's sampling record in the models config's words; None and 'vendor default' are vendor_default."""
    on_off = {True: "on", False: "off", "on": "on", "off": "off"}
    reasoning = s["reasoning"] if "reasoning" in s else s.get("thinking")
    out = _Flow(temperature=s["temperature"] if s["temperature"] not in (None, "vendor default") else "vendor_default",
                max_tokens=s["max_tokens"], reasoning=on_off.get(reasoning, "vendor_default"))
    if judge:
        out["json"] = bool(s.get("json_mode"))
    return out


# ---------- checking the output against today's code ----------

def verify(texts, spec, folder):
    """Every way the written files would render, grade or score differently from bcb.spec today; empty if none."""
    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in texts.items():
            (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / rel).write_text(text, encoding="utf-8")
        try:
            run = runfile.load(Path(tmp) / folder / FILES["run"], tmp)
        except schema.FormatError as e:
            return [f"the files would not pass their own checks: {p}" for p in e.problems]
    scenario, out = run.scenarios[0], []
    suite = loader.Suite(run.scenarios, run.rubrics)
    checks = {c.id: c for r in run.rubrics for c in r.checks}
    ids, written = spec.checkpoints(include_optional=True), [c.id for c in scenario.checkpoints]
    out += [f"{i}: a checkpoint today, missing from the new files" for i in ids if i not in written]
    out += [f"{i}: in the new files, not a checkpoint today" for i in written if i not in ids]
    for cp in (i for i in ids if i in written):
        out += _checkpoint_problems(spec, scenario, suite, checks, cp)
    graded = {i for cp in ids for i in spec.items_at(cp)}
    for c in checks.values():
        if c.id not in graded:
            out.append(f"check {c.id} is in the new files but graded nowhere today")
        elif render.item(c) != spec.items[c.id]:
            changed = [f.name for f in dataclasses.fields(render.item(c))
                       if getattr(render.item(c), f.name) != getattr(spec.items[c.id], f.name)]
            out.append(f"check {c.id} would change its {', '.join(changed)}")
    return out


def _checkpoint_problems(spec, scenario, suite, checks, cp):
    out, ref = [], f"{scenario.id}/{cp}"
    old, new = spec.messages(cp), render.messages(scenario, cp)
    if old != new:
        n = next((n for n, (a, b) in enumerate(zip(old, new), 1) if a != b), min(len(old), len(new)) + 1)
        out.append(f"{cp}: the model input would change, first at message {n} ({len(old)} messages today, "
                   f"{len(new)} new)")
    if spec.grader_note(cp) != render.grader_note(scenario, cp):
        out.append(f"{cp}: the grader note would change from {spec.grader_note(cp)!r} to "
                   f"{render.grader_note(scenario, cp)!r}")
    # the checks graded here, as bcb.loader scopes them (always, by approach, at, false_positive_at), in order
    today, new = spec.items_at(cp), suite.items_at(ref)
    moved = [f"{cp}: {i} is graded here today, and would not be" for i in today if i not in new]
    moved += [f"{cp}: {i} would be graded here, and is not today" for i in new if i not in today]
    if not moved and new != today:  # the new files order reused checks by their place in the file, nothing else
        moved.append(f"{cp}: the checks would be graded in the order {', '.join(new)}, where today it is "
                     f"{', '.join(today)}")
    out += moved
    for i in (i for i in spec.items_at(cp) if i in checks):
        if render.prompt(scenario, cp, checks[i], REPLY) != grader.render_prompt(spec, cp, spec.items[i], REPLY):
            out.append(f"{cp}: the grader prompt for {i} would change")
    return out


# ---------- the whole conversion ----------

def convert(spec_path=lb.SPEC, conversation_path=lb.CONVERSATION, branches_path=lb.MESSY, carried=CARRIED,
            graded=tuple(review.GRADED), folder=FOLDER):
    """{path from the repository's top folder: file text} for the five files; ConversionError if anything is off."""
    spec, messy = load_sources(spec_path, conversation_path, branches_path, carried)
    scenario_id = spec.raw["spec"]["id"]
    conv, problems = conversation(spec)
    problems += severity_problems(spec)
    core, checks = rubrics(spec)
    models, run, bad = from_manifests(graded, folder, scenario_id)
    if problems + bad:
        raise ConversionError(problems + bad)
    sources = [f"{_shown(spec_path)} (spec {spec.version}, sha256 {_sha(spec_path)})",
               f"{_shown(conversation_path)} (sha256 {_sha(conversation_path)})",
               f"{_shown(branches_path)} ({(messy.get('pilot') or {}).get('version')}, sha256 {_sha(branches_path)}; "
               f"carried: {', '.join(carried)})"]
    manifests = [f"{_shown(Path(d) / 'manifest.json')} (sha256 {_sha(Path(d) / 'manifest.json')})" for d in graded]
    head = [f"{scenario_id} in the new file formats (infra audit 4.2), written by `python -m bcb.convert` from:"]
    tail = ["Do not edit by hand: edit the sources and rerun. `python -m bcb.convert --check` says when this file is "
            "out of date."]
    notes = {
        "conversation": ["Each checkpoint's always_on map is the spec's. Whether a row it fixes as not_applicable "
                         "counts in the score is undecided (static_na in the run file)."],
        "run": ["Undecided, set as the Oct 8 numbers used them: static_na: count grades and counts rows a conversation "
                "fixes as not_applicable; gates.cap: none lets no gate cap a rank."]}
    raws = {"conversation": conv, "core": core, "checks": checks, "models": models, "run": run}
    texts = {}
    for key, raw in raws.items():
        named = manifests if key in ("models", "run") else sources
        texts[f"{folder}/{FILES[key]}"] = dump(raw, head + [f"  {s}" for s in named] + tail + notes.get(key, []))
    bad = verify(texts, spec, folder)
    if bad:
        raise ConversionError(bad)
    return texts


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _shown(path):
    """A source path as the header names it: from the repository's top folder when it is inside it."""
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def stale(texts, root=ROOT):
    """{file: missing | out of date} for the files on disk that are not what the converter writes now."""
    return {rel: "missing" if not (root / rel).is_file() else "out of date" for rel, text in texts.items()
            if not (root / rel).is_file() or (root / rel).read_text(encoding="utf-8") != text}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--check", action="store_true",
                   help="write nothing; exit 1 if the files on disk are not what the converter writes now")
    a = p.parse_args(argv)
    try:
        texts = convert()
    except ConversionError as e:
        print(e, file=sys.stderr)
        return 1
    if a.check:
        out_of_date = stale(texts)
        for rel, why in out_of_date.items():
            print(f"{rel}: {why}; run python -m bcb.convert", file=sys.stderr)
        if not out_of_date:
            print(f"{FOLDER}: up to date")
        return 1 if out_of_date else 0
    for rel, text in texts.items():
        (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / rel).write_text(text, encoding="utf-8")
        print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
