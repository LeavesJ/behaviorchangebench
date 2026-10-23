"""The scenarios and rubrics of a run as one suite: every ID named by its scenario, and which checks are graded where.

    suite = Suite(run.scenarios, run.rubrics)       # run = bcb.runfile.load("configs/<run>.yaml")
    suite.items_at("vaping-v0/KM7")                 # the check ids graded there, in order
    suite.applied("vaping-v0/KM7")                  # the same checks, each with its scope, severity and version

IDs in the files are local (infra audit 4.2). Here a checkpoint is "<scenario>/<checkpoint>" and a check, as graded
in a scenario, is "<scenario>/<check id>", so two scenarios that each have a C1, or each have their own check with
one id, never overwrite each other. Within one scenario a check id names one check (bcb.rubric.clashes).

Which checks are graded at a checkpoint (infra audit 4.3, task 4):
- a check that names the checkpoint under `at`: the checkpoint's own check if it is the first checkpoint named
  there, a reused one otherwise; or under `false_positive_at` (a gate graded to see whether it fires wrongly);
- a check that applies always, the explicit scope that replaces the "AO" prefix, if its `approach` list includes
  the checkpoint's expected approach. A check with no list applies under every approach. A sequence such as
  [safety, lapse_work] includes each of its steps. A checkpoint that gives no approach narrows nothing.
They come in the order bcb.spec.Spec.items_at gives them: the checks that apply always (rubric by rubric, in file
order), then the checkpoint's own, the reused ones, and the false-positive gates. The core and scenario subscores
split on `group: core`, not on the prefix either.

Two scoring rules are undecided and not applied here. A check the checkpoint's always_on map fixes as not_applicable
is still graded, as on Oct 8, and `fixed_na` marks the row; whether it counts in the score is the run file's
static_na (count by default, as on Oct 8; infra audit 5.13), which bcb.results reads. No gate caps a rank unless the
run file's gates.cap says rank_below (bcb.gates; none by default).
"""

from dataclasses import dataclass

from . import rubric
from .schema import duplicates

ROLES = ("always", "own", "reused", "false_positive")  # also the order they are graded in at a checkpoint


class LoadError(ValueError):
    """The scenarios and rubrics cannot make one suite; `problems` holds one line per reason."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("the scenarios and rubrics clash:\n  " + "\n  ".join(self.problems))


@dataclass(frozen=True)
class Applied:
    """One check as graded at one checkpoint."""
    scenario: str
    checkpoint: str  # local to the scenario
    check: rubric.Check
    rubric: str  # the id of the rubric file the check is in
    role: str  # always | own | reused | false_positive
    fixed_na: bool = False  # the checkpoint's always_on map fixes it as not_applicable; still graded (see above)

    @property
    def id(self):
        """The check's global id, "<scenario>/<check id>"."""
        return f"{self.scenario}/{self.check.id}"

    @property
    def at(self):
        """The checkpoint's global id, "<scenario>/<checkpoint>"."""
        return f"{self.scenario}/{self.checkpoint}"

    @property
    def version(self):
        return self.check.version

    @property
    def versioned(self):
        """The id a verdict is joined on, "<scenario>/<check id>@<version>" (spec.yaml run.join_key)."""
        return f"{self.id}@{self.version}"

    @property
    def severity(self):
        return self.check.severity  # S1-S3, or None

    @property
    def core(self):
        """In the core subscore: the check's group is core (the "AO" prefix's old meaning)."""
        return self.check.group == "core"


def problems(scenarios, rubrics):
    """Why these scenarios and rubrics cannot make one suite, one line each; empty if they can."""
    out = [f"scenarios: two files have scenario id {i}" for i in duplicates(s.id for s in scenarios)]
    return out + rubric.clashes(rubrics, [s.id for s in scenarios])


def narrows_out(check, approach):
    """True when a check that applies always is left out at a checkpoint because of its approach (an Approach or
    None): the check lists approaches and none of them is the checkpoint's expected approach, or a step of it."""
    return bool(check.approach) and approach is not None and not set(check.approach) & set(approach.expected)


def role(check, ref, approach):
    """How a check is graded at the checkpoint `ref` ("<scenario>/<checkpoint>"): one of ROLES, or None if it is not."""
    applies = check.applies
    if ref in applies.false_positive_at:
        return "false_positive"
    if ref in applies.at:
        return "own" if applies.at[0] == ref else "reused"
    if applies.always and not narrows_out(check, approach):
        return "always"
    return None


class Suite:
    """The scenarios and rubrics of one run, resolved: each checkpoint by its global id, and the checks graded there."""

    def __init__(self, scenarios, rubrics):
        bad = problems(scenarios, rubrics)
        if bad:
            raise LoadError(bad)
        self.scenarios = {s.id: s for s in scenarios}
        self.rubrics = tuple(rubrics)
        self._applied = {f"{s.id}/{c.id}": self._scope(s, c) for s in scenarios for c in s.checkpoints}
        # every check each scenario can grade, by its global id; `clashes` makes these unique
        self._checks = {f"{s.id}/{c.id}": c for s in scenarios for r in self.rubrics for c in r.checks
                        if rubric.reaches(c, s.id)}

    def _scope(self, scenario, checkpoint):
        ref, found = f"{scenario.id}/{checkpoint.id}", []
        for r in self.rubrics:
            for c in r.checks:
                how = role(c, ref, checkpoint.approach)
                if how:
                    fixed = checkpoint.always_on.get(c.id) == "not_applicable"
                    found.append(Applied(scenario.id, checkpoint.id, c, r.id, how, fixed))
        return tuple(sorted(found, key=lambda a: ROLES.index(a.role)))  # a stable sort keeps file order within a role

    def checkpoints(self):
        """Every checkpoint's global id: scenario by scenario, each in its file's order (main line, then branches)."""
        return list(self._applied)

    def checkpoint(self, ref):
        """(the scenario, the checkpoint) for a global id "<scenario>/<checkpoint>"."""
        self._known(ref)
        scenario_id, local = ref.split("/", 1)
        scenario = self.scenarios[scenario_id]
        return scenario, scenario.checkpoint(local)

    def applied(self, ref):
        """The checks graded at a checkpoint, in grading order."""
        return self._known(ref)

    def items_at(self, ref):
        """The ids of the checks graded at a checkpoint, in order, local to its scenario as Spec.items_at gives them."""
        return [a.check.id for a in self._known(ref)]

    def check(self, check_ref):
        """The check a global id "<scenario>/<check id>" names."""
        if check_ref not in self._checks:
            raise KeyError(f"no check {check_ref!r}: a check is named <scenario>/<check id>, and the scenario must be "
                           f"one of {', '.join(self.scenarios)} with a rubric in which that check can be graded")
        return self._checks[check_ref]

    def _known(self, ref):
        if ref not in self._applied:
            raise KeyError(f"no checkpoint {ref!r}: a checkpoint is named <scenario>/<checkpoint>, such as "
                           f"{next(iter(self._applied), 'walking-v0/C1')}")
        return self._applied[ref]
