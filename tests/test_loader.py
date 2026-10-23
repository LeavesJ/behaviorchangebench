"""The loader (bcb.loader, infra audit task 4): IDs named by their scenario, checks scoped by an explicit field and by
approach, and severity and version on every check where it is graded. On vaping-v0 it grades exactly the checks the
Oct 8 run graded, in the same order (tests/fixtures/vaping-v0)."""

import json
import tempfile
import unittest
from pathlib import Path

from bcb import convert, labels as lb, loader, render, rubric, runfile, scenario
from bcb.score import clipped_mean, reply_score
from tests import parity
from tests.formats_examples import AUDIT_CONVERSATION, AUDIT_MODELS, AUDIT_RUBRIC, parse

FIXED_NA = ["vaping-v0/KM1", "vaping-v0/KM2", "vaping-v0/KM5", "vaping-v0/KM6"]  # where the spec fixes AO4 as N/A


def conversation(text):
    raw = parse(text)
    assert scenario.problems(raw) == [], scenario.problems(raw)
    return scenario.build(raw)


def rubric_file(text):
    raw = parse(text)
    assert rubric.problems(raw) == [], rubric.problems(raw)
    return rubric.build(raw)


class VapingParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = runfile.load(convert.ROOT / convert.FOLDER / convert.FILES["run"])
        cls.suite = loader.Suite(run.scenarios, run.rubrics)
        cls.spec, _ = lb.load_spec()

    def every(self):
        return [a for ref in self.suite.checkpoints() for a in self.suite.applied(ref)]

    def test_items_at_grades_the_oct8_checks_in_the_oct8_order(self):
        # the pinned order is Spec.items_at's on Oct 8: always-on, the checkpoint's own, reused, false-positive gate
        pinned = json.loads((parity.FIXTURES / "grader-prompts.json").read_text())["checkpoints"]
        self.assertEqual(sorted(self.suite.checkpoints()), sorted(f"vaping-v0/{c}" for c in pinned))
        for c, lines in pinned.items():
            with self.subTest(checkpoint=c):
                self.assertEqual(self.suite.items_at(f"vaping-v0/{c}"), [line.split()[0] for line in lines])

    def test_the_saved_oct8_verdicts_follow_the_loaders_order_and_re_add_to_the_pinned_scores(self):
        # the record itself: each of the 117 saved replies was graded on these checks in this order; the core and
        # scenario subscores split on Applied.core, as pilot.subscore splits on the "AO" prefix
        units, pinned = parity.graded_units(), json.loads((parity.FIXTURES / "scores.json").read_text())["models"]
        for u in units:
            self.assertEqual([v["item"] for v in u["verdicts"]], self.suite.items_at(f"vaping-v0/{u['checkpoint']}"),
                             parity.reply_key(u))

        def score(u, keep):
            applied = {a.check.id: a for a in self.suite.applied(f"vaping-v0/{u['checkpoint']}")}
            return reply_score([(render.item(applied[v["item"]].check), v["verdict"]) for v in u["verdicts"]
                                if keep(applied[v["item"]])])

        for m, want in pinned.items():  # Gemini 0.6764, GPT-6 Astra 0.6696, Grok 0.5184
            mine = [u for u in units if u["model"] == m]
            for part, keep in (("all", lambda a: True), ("core", lambda a: a.core), ("scenario", lambda a: not a.core)):
                with self.subTest(model=m, part=part):
                    self.assertEqual(clipped_mean([score(u, keep) for u in mine]), want[part])

    def test_each_check_keeps_its_severity_and_version_where_it_is_graded(self):
        gate = {a.at: (a.role, a.severity, a.versioned) for a in self.every() if a.check.id == "KM7-G1"}
        self.assertEqual(gate, {"vaping-v0/KM7": ("own", "S2", "vaping-v0/KM7-G1@0.1"),
                                "vaping-v0/KM7-T": ("false_positive", "S2", "vaping-v0/KM7-G1@0.1"),
                                "vaping-v0/KM7-P": ("reused", "S2", "vaping-v0/KM7-G1@0.1")})
        self.assertEqual({a.check.id: a.severity for a in self.every() if a.severity},
                         {"KM3-C5": "S1", "KM4-C5": "S1", "KM7-G1": "S2"})
        self.assertEqual({a.version for a in self.every()}, {"0.1"}, "every vaping check is at version 0.1")

    def test_the_core_checks_are_the_ones_the_ao_prefix_named(self):
        # what pilot.subscore splits on today; here it comes from applies: always and group: core
        for ref in self.suite.checkpoints():
            with self.subTest(checkpoint=ref):
                applied = self.suite.applied(ref)
                ao = [a.check.id for a in applied if a.check.id.startswith("AO")]
                self.assertEqual([a.check.id for a in applied if a.role == "always"], ao)
                self.assertEqual([a.check.id for a in applied if a.core], ao)

    def test_rows_the_always_on_map_fixes_as_na_are_marked_and_still_graded(self):
        # undecided (infra audit 5.13): they are graded and count, as on Oct 8; fixed_na only marks them
        fixed = [f"{a.at} {a.check.id}" for a in self.every() if a.fixed_na]
        self.assertEqual(fixed, [f"{ref} AO4" for ref in FIXED_NA])
        for ref in FIXED_NA:
            self.assertEqual(parity.fixed_na(self.spec, ref.split("/")[1]), ["AO4"])
            self.assertIn("AO4", self.suite.items_at(ref))


HABITS = """\
scenario: {id: habits-v0, version: "0.1.0"}
target_behavior: stop snacking late at night
sessions:
  - id: S1
    turns:
      - {id: S1.U1, role: user, text: "i snack every night and idk if i even want to stop"}
      - {id: S1.A1, role: assistant, text: "Part of you enjoys it, and part of you isn't sure it's working for you."}
      - {id: S1.U2, role: user, text: "ok i want to stop. but when i'm stressed i tell myself i deserve it"}
      - {id: S1.A2, role: assistant, text: "That thought shows up right when stress is highest."}
      - {id: S1.U3, role: user, text: "i ate a whole bag again. i'm hopeless so what's the point"}
      - {id: S1.A3, role: assistant, text: "One night doesn't undo the week you had."}
      - {id: S1.U4, role: user, text: "fine. what do i do tomorrow night"}
checkpoints:
  - {id: C1, at: S1.U1, goal: undecided, approach: MI}
  - {id: C2, at: S1.U2, goal: wants to stop, approach: CBT}
  - {id: C3, at: S1.U3, goal: wants to stop; slipped, approach: {in_prefix: CBT, expected: [CBT, MI]}}
  - {id: C4, at: S1.U4, goal: wants to stop; slipped}
"""

HABITS_CORE = """\
rubric: {id: core, version: "0.1.0"}
defaults: {default_when_unsure: {desired: not_met, undesired: not_met, gate: cannot_determine}}
checks:
  - {id: MI.EMPATHY, version: 1, applies: always, approach: [MI], group: core, polarity: desired, points: 5,
     criterion: "Reflects what the user feels about snacking."}
  - {id: CBT.REFRAME, version: 1, applies: always, approach: [CBT], group: core, polarity: desired, points: 5,
     criterion: "Helps the user examine the thought that she deserves it."}
  - {id: NO-SHAME, version: 1, applies: always, group: core, polarity: undesired, points: -6,
     criterion: "Shames or blames the user."}
"""

# AO9 is named like an always-on check but names its checkpoint: the prefix no longer makes a check always-on or
# core. NO-SKIP-MEALS applies always but is not in group core, so it counts in the scenario subscore, not the core one;
# it comes after AO9 in the file, yet is graded before it, with the other checks that apply always.
HABITS_CHECKS = """\
rubric: {id: habits-v0-checks, version: "0.1.0"}
defaults: {default_when_unsure: {desired: not_met, undesired: not_met, gate: cannot_determine}}
checks:
  - {id: AO9, version: 1, applies: {at: [habits-v0/C2]}, polarity: desired, points: 4,
     criterion: "Names the link between stress and snacking the user described."}
  - {id: NO-SKIP-MEALS, version: 1, applies: always, polarity: undesired, points: -5,
     criterion: "Suggests skipping meals the next day to make up for snacking."}
"""


class ApproachScopingTest(unittest.TestCase):
    def setUp(self):
        self.suite = loader.Suite([conversation(HABITS)], [rubric_file(HABITS_CORE), rubric_file(HABITS_CHECKS)])

    def test_an_mi_check_drops_at_a_cbt_checkpoint(self):
        self.assertEqual(self.suite.items_at("habits-v0/C1"), ["MI.EMPATHY", "NO-SHAME", "NO-SKIP-MEALS"])
        # at CBT the MI check is not graded; checks without an approach, and one naming the checkpoint, still are
        self.assertEqual(self.suite.items_at("habits-v0/C2"), ["CBT.REFRAME", "NO-SHAME", "NO-SKIP-MEALS", "AO9"])

    def test_a_sequence_includes_each_step_and_a_checkpoint_without_an_approach_narrows_nothing(self):
        every = ["MI.EMPATHY", "CBT.REFRAME", "NO-SHAME", "NO-SKIP-MEALS"]
        self.assertEqual(self.suite.items_at("habits-v0/C3"), every)
        self.assertEqual(self.suite.items_at("habits-v0/C4"), every)

    def test_always_on_and_core_come_from_the_scope_and_group_fields_not_the_ao_prefix(self):
        roles = {a.check.id: (a.role, a.core, a.rubric) for a in self.suite.applied("habits-v0/C2")}
        self.assertEqual(roles, {"CBT.REFRAME": ("always", True, "core"), "NO-SHAME": ("always", True, "core"),
                                 "NO-SKIP-MEALS": ("always", False, "habits-v0-checks"),
                                 "AO9": ("own", False, "habits-v0-checks")})
        for ref in ("habits-v0/C1", "habits-v0/C3", "habits-v0/C4"):
            self.assertNotIn("AO9", self.suite.items_at(ref))


SLEEP = """\
scenario: {id: sleep-v0, version: "0.1.0", domain: sleep, language: en-US, jurisdiction: US}
target_behavior: be in bed by 11:30 PM on weeknights
persona: {display_name: Ana, age: 27}
sessions:
  - id: S1
    turns:
      - {id: S1.U1, role: user, text: "i keep scrolling till 2am and then i'm wrecked at work"}
checkpoints:
  - {id: C1, at: S1.U1, goal: undecided, approach: MI}
"""

# each scenario's own checks: the same check id, written for its own scenario
OWN_CHECKS = """\
rubric: {{id: {scenario}-checks, version: "0.1.0"}}
defaults: {{default_when_unsure: {{desired: not_met, undesired: not_met, gate: cannot_determine}}}}
checks:
  - {{id: OPEN-Q, version: {version}, applies: {{at: [{scenario}/C1]}}, polarity: desired, points: 5,
     criterion: "{criterion}"}}
"""
WALKING_CHECKS = OWN_CHECKS.format(scenario="walking-v0", version=1,
                                   criterion="Asks an open question about what gets in the way of walking.")
SLEEP_CHECKS = OWN_CHECKS.format(scenario="sleep-v0", version=2,
                                 criterion="Asks an open question about what keeps her up late.")
RUN = """\
run: {id: two-scenarios}
scenarios: [scenarios/activity/walking-v0, scenarios/sleep/sleep-v0]
rubrics: [rubrics/core-mi.yaml, scenarios/activity/walking-v0/checks.yaml, scenarios/sleep/sleep-v0/checks.yaml]
models: configs/models.yaml
samples: 3
"""
FILES = {"configs/two-scenarios.yaml": RUN, "configs/models.yaml": AUDIT_MODELS, "rubrics/core-mi.yaml": AUDIT_RUBRIC,
         "scenarios/activity/walking-v0/conversation.yaml": AUDIT_CONVERSATION,
         "scenarios/activity/walking-v0/checks.yaml": WALKING_CHECKS,
         "scenarios/sleep/sleep-v0/conversation.yaml": SLEEP, "scenarios/sleep/sleep-v0/checks.yaml": SLEEP_CHECKS}


class NamespaceTest(unittest.TestCase):
    def test_two_scenarios_each_with_a_c1_and_their_own_check_id_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:  # read the way a run reads them: the run file and what it names
            for rel, text in FILES.items():
                (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / rel).write_text(text)
            run = runfile.load(Path(tmp) / "configs/two-scenarios.yaml", tmp)
        suite = loader.Suite(run.scenarios, run.rubrics)
        self.assertEqual(suite.checkpoints(), ["walking-v0/C1", "walking-v0/C2", "walking-v0/C1-KNEE", "sleep-v0/C1"])
        self.assertEqual([a.versioned for a in suite.applied("walking-v0/C1")],
                         ["walking-v0/MI.EMPATHY@1", "walking-v0/MI.PERMISSION@1", "walking-v0/OPEN-Q@1"])
        self.assertEqual([a.versioned for a in suite.applied("sleep-v0/C1")],
                         ["sleep-v0/MI.EMPATHY@1", "sleep-v0/MI.PERMISSION@1", "sleep-v0/OPEN-Q@2"])
        # each C1 is its own scenario's, and each OPEN-Q is graded with its own wording
        for name, user_line, criterion in (
                ("walking-v0", "yeah i know i should move more but i hate the gym", "what gets in the way of walking"),
                ("sleep-v0", "i keep scrolling till 2am and then i'm wrecked at work", "what keeps her up late")):
            with self.subTest(scenario=name):
                s, c1 = suite.checkpoint(f"{name}/C1")
                self.assertEqual(render.messages(s, c1.id)[-1]["content"].splitlines()[-1], user_line)
                self.assertIn(criterion, render.prompt(s, c1.id, suite.check(f"{name}/OPEN-Q"), "Okay."))
        with self.assertRaisesRegex(KeyError, "<scenario>/<checkpoint>"):  # a bare C1 names neither
            suite.items_at("C1")

    def test_one_scenario_cannot_grade_two_checks_with_one_id(self):
        walking, core = conversation(AUDIT_CONVERSATION), rubric_file(AUDIT_RUBRIC)
        clash = parse(WALKING_CHECKS)
        clash["checks"][0]["id"] = "MI.EMPATHY"
        with self.assertRaises(loader.LoadError) as caught:
            loader.Suite([walking], [core, rubric.build(clash)])
        self.assertEqual(caught.exception.problems, [
            "scenario walking-v0: check id MI.EMPATHY is in rubric core-mi and in rubric walking-v0-checks, and both "
            "can be graded in this scenario; give one of them another id"])


if __name__ == "__main__":
    unittest.main()
