"""The run file: the scenarios, checkpoints, rubrics and models one run uses, and how it samples and grades.

    configs/<run>.yaml        (infra audit 4.2); its paths are relative to the repository's top folder

`problems` checks the run file's own shape. `run_problems` also reads every file it names, checks each one, then
checks them against each other: a checkpoint pattern that matches nothing, a check id naming two checks within one
scenario (bcb.rubric.clashes), a rubric naming a checkpoint its scenario does not have, an always_on entry naming no
check that applies always, an approach or trigger outside the rubrics' theory_consistency, a grader the models config
does not have.

Two settings hold scoring rules the team has not settled, so each defaults to what the Oct 8 numbers used:
- gates.cap: none (the default; a failed gate is reported and moves no rank) or rank_below, the cap adopted on
  2026-09-27 (docs/decisions.md) and never applied to a reported number; bcb.gates applies it only when asked.
- static_na: count (the default; rows a conversation's always_on map fixes as not_applicable are graded and
  counted, as on Oct 8) or exclude. Undecided (infra audit 5.13); excluding them would move Gemini from 0.6764 to
  0.6693 and Grok from 0.5184 to 0.5041.
bcb.results reads both when it adds up a run's results from saved verdicts; the runner (bcb.pilot) reads neither.
"""

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from . import models_config, rubric, scenario, schema
from .gates import CAPS  # none | rank_below
from .schema import REF, an_id, as_tuple, block, describe, duplicates, is_text, one_of, text, text_list, whole_number

ROOT = schema.ROOT
TOP =("run", "scenarios", "checkpoints", "rubrics", "models", "samples", "grade", "gates", "budget_usd", "static_na")
RUN = ("id", "out")
GRADE = ("with", "read")
READS = ("raw", "normalized")  # normalized: the typography labelers saw (decisions.md 2026-10-05)
GATES = ("rule", "k", "cap")
GATE_RULES = ("pass_k",)  # the gate holds only if it holds in all k samples
STATIC_NA = ("count", "exclude")


@dataclass(frozen=True)
class Gates:
    k: int
    rule: str = "pass_k"
    cap: str = "none"  # undecided: see the module docstring


@dataclass(frozen=True)
class Run:
    id: str
    out: Path
    samples: int
    scenarios: tuple[scenario.Scenario, ...]
    rubrics: tuple[rubric.Rubric, ...]
    models: models_config.ModelsConfig
    checkpoints: tuple[str, ...]  # "<scenario>/<checkpoint>", the patterns' matches (all if none), in file order
    gates: Gates
    grade_with: str | None = None  # None: draw replies only, grade later
    grade_read: str = "raw"
    budget_usd: float | None = None
    static_na: str = "count"  # undecided: see the module docstring
    path: Path | None = None


# ---------- checking ----------

def problems(raw):
    """Everything wrong with the run file's own shape, one line each; the files it names are not opened."""
    if not isinstance(raw, dict):
        return [f"the file must be a block of keys ({', '.join(TOP)}), not {describe(raw)}"]
    out = block("the file", raw, TOP, ("run", "scenarios", "rubrics", "models", "samples"))
    if raw.get("run") is not None:
        out += block("run", raw["run"], RUN, ("id",))
        if isinstance(raw["run"], dict):
            out += an_id("run.id", raw["run"]["id"]) if raw["run"].get("id") is not None else []
            out += text("run.out", raw["run"]["out"]) if raw["run"].get("out") is not None else []
    out += [p for k in ("scenarios", "rubrics") if raw.get(k) is not None for p in text_list(k, raw[k])]
    out += text("models", raw["models"]) if raw.get("models") is not None else []
    out += _patterns(raw["checkpoints"]) if raw.get("checkpoints") is not None else []
    samples = raw.get("samples")
    out += whole_number("samples", samples) if samples is not None else []
    out += _grade(raw["grade"]) if raw.get("grade") is not None else []
    out += _gates(raw["gates"], samples) if raw.get("gates") is not None else []
    budget = raw.get("budget_usd")
    if budget is not None and not (isinstance(budget, (int, float)) and not isinstance(budget, bool) and budget > 0):
        out.append(f"budget_usd must be a number of US dollars above 0, not {describe(budget)}")
    out += one_of("static_na", raw["static_na"], STATIC_NA) if "static_na" in raw else []
    return out


def _patterns(value):
    bad = text_list("checkpoints", value)
    if bad:
        return bad
    return [f"checkpoints item {n} {p!r} must name the scenario too, as <scenario>/<checkpoint> "
            "(a * matches any characters, as in vaping-v0/KM*)"
            for n, p in enumerate(as_tuple(value), 1) if not REF.fullmatch(p)]


def _grade(value):
    out = block("grade", value, GRADE, ("with",))
    if isinstance(value, dict):
        out += an_id("grade.with", value["with"]) if value.get("with") is not None else []
        out += one_of("grade.read", value["read"], READS) if "read" in value else []
    return out


def _gates(value, samples):
    out = block("gates", value, GATES)
    if not isinstance(value, dict):
        return out
    out += one_of("gates.rule", value["rule"], GATE_RULES) if "rule" in value else []
    out += one_of("gates.cap", value["cap"], CAPS) if "cap" in value else []
    k = value.get("k")
    out += whole_number("gates.k", k) if k is not None else []
    if not out and not whole_number("samples", samples) and k is not None and k > samples:
        out.append(f"gates.k is {k} but samples is {samples}: pass^k needs at least k replies per checkpoint and model")
    return out


def run_problems(raw, root=ROOT):
    """Everything wrong with a run file and the files it names, alone and against each other."""
    return _resolve(raw, Path(root))[0]


def _resolve(raw, root):
    """(problems, scenarios, rubrics, models config); the loaded files are None or partial when anything is wrong."""
    out = problems(raw)
    if not isinstance(raw, dict):
        return out, (), (), None
    # the run's paths start from the repository's top folder, so that is where a private scenario must stay out of git
    scenarios = _load_each(raw.get("scenarios"), root, scenario, out, folder_file="conversation.yaml", repo=root)
    rubrics = _load_each(raw.get("rubrics"), root, rubric, out)
    models = _load_each(raw.get("models"), root, models_config, out)
    models = models[0] if models else None
    if out:
        return out, scenarios, rubrics, models
    return _cross(raw, scenarios, rubrics, models), scenarios, rubrics, models


def _load_each(paths, root, module, out, folder_file=None, **load_args):
    """Each named file loaded with `module.load` (given `load_args`); its problems go to `out`, each prefixed with the
    path as written."""
    if not (is_text(paths) or (isinstance(paths, list) and all(is_text(p) for p in paths))):
        return ()
    loaded = []
    for written in as_tuple(paths):
        path = root / written
        if folder_file and path.is_dir():
            path, written = path / folder_file, f"{written.rstrip('/')}/{folder_file}"
        try:
            loaded.append(module.load(path, **load_args))
        except schema.FormatError as e:
            out += [f"{written}: {p}" for p in e.problems]
    return tuple(loaded)


def _cross(raw, scenarios, rubrics, models):
    out = [f"scenarios: two files have scenario id {i}" for i in duplicates(s.id for s in scenarios)]
    known = {f"{s.id}/{c.id}": c for s in scenarios for c in s.checkpoints}
    for p in as_tuple(raw.get("checkpoints")):
        if not _matching([s.id for s in scenarios], p.split("/", 1)[0]):
            out.append(f"checkpoints: {p!r} names a scenario that is not under scenarios "
                       f"({', '.join(s.id for s in scenarios)})")
        elif not _matching(known, p):
            out.append(f"checkpoints: {p!r} matches no checkpoint (the scenarios have {', '.join(known)})")
    checks = [(r, c) for r in rubrics for c in r.checks]
    out += rubric.clashes(rubrics, [s.id for s in scenarios])
    in_run = {s.id for s in scenarios}
    for r, c in checks:
        for ref in c.applies.at + c.applies.false_positive_at:
            scen, cp = ref.split("/", 1)
            if scen in in_run and ref not in known:
                out.append(f"rubric {r.id}: check {c.id} applies at {ref}, but scenario {scen} has no checkpoint {cp}")
    always = {c.id for _, c in checks if c.applies.always}
    out += [f"scenario {s.id}: checkpoint {c.id} always_on names {i}, but no rubric in this run has a check {i} that "
            "applies always" for s in scenarios for c in s.checkpoints for i in c.always_on if i not in always]
    out += _theory(scenarios, rubrics, checks)
    grade = raw.get("grade")
    if grade and grade["with"] not in models.graders:
        out.append(f"grade.with {grade['with']!r} is not a grader in {raw['models']} "
                   f"({', '.join(models.graders) or 'it has none'})")
    return out


def _matching(names, pattern):
    """The names a pattern matches; * matches any characters, and case counts on every system."""
    return [n for n in names if fnmatch.fnmatchcase(n, pattern)]


def _theory(scenarios, rubrics, checks):
    """Approach names and trigger codes against the rubrics' theory_consistency, when any rubric declares one."""
    declared = [r.theory_consistency for r in rubrics if r.theory_consistency]
    if not declared:
        return []
    names = set().union(*(t.approaches for t in declared))
    codes = set().union(*(t.licensed_triggers for t in declared))
    named, licensed = f"an approach the rubrics name ({', '.join(sorted(names))})", ", ".join(sorted(codes))
    out = []
    for s in scenarios:
        for c in (c for c in s.checkpoints if c.approach):
            out += [f"scenario {s.id}: checkpoint {c.id} approach {a!r} is not {named}"
                    for a in c.approach.expected + c.approach.in_prefix if a not in names]
            if codes and c.approach.trigger and c.approach.trigger.code not in codes:
                out.append(f"scenario {s.id}: checkpoint {c.id} trigger {c.approach.trigger.code!r} is not a licensed "
                           f"trigger the rubrics name ({licensed})")
    # a rubric with its own theory_consistency was checked against it when it was read
    out += [f"rubric {r.id}: check {c.id} approach {a!r} is not {named}"
            for r, c in checks if not r.theory_consistency for a in c.approach if a not in names]
    return out


# ---------- loading ----------

def load(path, root=ROOT):
    """The run file and every file it names, as a Run; FormatError listing every problem if anything is wrong."""
    raw, bad = schema.read(path)
    scenarios, rubrics, models = (), (), None
    if not bad:
        bad, scenarios, rubrics, models = _resolve(raw, Path(root))
    if bad:
        raise schema.FormatError(path, bad)
    patterns = as_tuple(raw.get("checkpoints")) or ("*",)
    chosen = tuple(g for g in (f"{s.id}/{c.id}" for s in scenarios for c in s.checkpoints)
                   if any(_matching([g], p) for p in patterns))
    gates, grade = raw.get("gates") or {}, raw.get("grade") or {}
    return Run(raw["run"]["id"], Path(root) / (raw["run"].get("out") or "runs"), raw["samples"], scenarios, rubrics,
               models, chosen, Gates(gates.get("k") or raw["samples"], gates.get("rule", "pass_k"),
                                     gates.get("cap", "none")),
               grade.get("with"), grade.get("read", "raw"), raw.get("budget_usd"), raw.get("static_na", "count"),
               Path(path))
