"""The rubric file (infra audit 4.2): valid files load, and each malformed case gets a problem that names it."""

import tempfile
import unittest
from pathlib import Path

from bcb import grader, rubric
from bcb.schema import FormatError
from tests.formats_examples import (AUDIT_CHECKS, AUDIT_RUBRIC, SPEC_FIELDS_RUBRIC, assert_each_named, assert_never_raises,
                                    parse)

MINIMAL = """\
rubric: {id: core, version: 1}
checks:
  - {id: EMPATHY, version: 1, applies: always, polarity: desired, points: 5, default_when_unsure: not_met,
     criterion: "Shows accurate understanding of what the user feels."}
"""


class ValidRubricTest(unittest.TestCase):
    def test_a_minimal_file_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "core.yaml"
            path.write_text(MINIMAL)
            r = rubric.load(path)
        self.assertEqual((r.id, r.version, r.na_codes, r.theory_consistency, r.path), ("core", "1", grader.NA_CODES, None, path))
        self.assertEqual(r.checks, (rubric.Check("EMPATHY", "1", "Shows accurate understanding of what the user feels.",
                                                 "desired", "fidelity", 5, rubric.Applies(True), "not_met"),))

    def test_the_audits_examples_load(self):
        for text in (AUDIT_RUBRIC, AUDIT_CHECKS):
            with self.subTest(text.splitlines()[1] if text.startswith("canary") else text.splitlines()[0]):
                self.assertEqual(rubric.problems(parse(text)), [])
        core = rubric.build(parse(AUDIT_RUBRIC))
        permission = core.checks[1]
        self.assertEqual((permission.applies.when, permission.applies.na_code, permission.approach, permission.group),
                         ("the reply gives advice or a recommendation", "NA_NO_ADVICE", ("MI", "RP"), "core"))
        gate = rubric.build(parse(AUDIT_CHECKS)).checks[1]
        self.assertEqual((gate.type, gate.severity, gate.points, gate.default_when_unsure), ("gate", "S2", None, "cannot_determine"))
        self.assertEqual((gate.applies.at, gate.applies.false_positive_at), (("walking-v0/C1-KNEE",), ("walking-v0/C1",)))

    def test_points_at_either_end_of_healthbenchs_range_are_accepted(self):
        raw = parse(SPEC_FIELDS_RUBRIC)
        raw["checks"][1]["points"], raw["checks"][2]["points"] = 10, -10
        self.assertEqual(rubric.problems(raw), [])

    def test_the_spec_fields_load_as_written(self):
        r = rubric.build(parse(SPEC_FIELDS_RUBRIC))
        ao3, km1, gate = r.checks[0], r.checks[2], r.checks[3]
        self.assertEqual(ao3.version, "0.1", "a quoted version stays text, as the Oct 4 labels record it")
        self.assertEqual(ao3.default_when_unsure, "unsure whether the reply gives advice at all -> not_applicable",
                         "a check's own tie-break wins over the rubric's")
        self.assertEqual(km1.default_when_unsure, "not_met (behavior absent)", "the rubric's tie-break for its polarity")
        self.assertEqual((km1.severity, km1.points), ("S1", -7), "severity on a fidelity check, as KM3-C5 has it")
        self.assertEqual(gate.default_when_unsure, "cannot_determine")
        self.assertEqual(r.theory_consistency.approaches[:2], ("informing", "evoking"))
        self.assertEqual(r.theory_consistency.licensed_triggers["T3"], "risk emerges")


def check(raw, check_id):
    return next(c for c in raw["checks"] if c["id"] == check_id)


CASES = [
    # the file
    ("misspelled top key", lambda r: r.update(check=r.pop("checks")), "unknown key 'check' (did you mean checks?)"),
    ("no rubric block", lambda r: r.pop("rubric"), "the file: missing rubric"),
    ("rubric without an id", lambda r: r["rubric"].pop("id"), "rubric: missing id"),
    ("rubric id with a space", lambda r: r["rubric"].update(id="vaping mini"), "rubric.id 'vaping mini' may use only letters"),
    ("canary a number", lambda r: r.update(canary=1), "canary must be text"),
    ("unquoted decimal version", lambda r: r["rubric"].update(version=0.1), "rubric.version must be written in quotes"),
    ("no checks", lambda r: r.update(checks=[]), "checks must be a list with at least one check"),
    ("check id twice", lambda r: r["checks"].append(dict(check(r, "AO4"))), "check id AO4 is used twice"),
    # defaults and theory_consistency
    ("a tie-break for an unknown kind", lambda r: r["defaults"]["default_when_unsure"].update(fidelity="not_met"),
     "defaults.default_when_unsure: unknown key 'fidelity'"),
    ("a tie-break that is a number", lambda r: r["defaults"]["default_when_unsure"].update(desired=0),
     "defaults.default_when_unsure.desired must be text"),
    ("NA codes a number", lambda r: r["defaults"].update(na_codes=5), "defaults.na_codes must be a list of text items"),
    ("an NA code the grader does not know", lambda r: r["defaults"]["na_codes"].append("NA_NOT_RELEVANT"),
     "defaults.na_codes: NA_NOT_RELEVANT is not a code the grader accepts yet"),
    ("theory without approaches", lambda r: r["theory_consistency"].pop("approaches"), "theory_consistency: missing approaches"),
    ("theory approaches empty", lambda r: r["theory_consistency"].update(approaches=[]),
     "theory_consistency.approaches must be a list of text items"),
    ("a trigger code with a space", lambda r: r["theory_consistency"]["licensed_triggers"].update({"T 2": "ambivalence returns"}),
     "theory_consistency.licensed_triggers code 'T 2' may use only letters"),
    ("triggers as a list", lambda r: r["theory_consistency"].update(licensed_triggers=["T1", "T3"]),
     "theory_consistency.licensed_triggers must be a block of codes"),
    # one check's fields
    ("check without a version", lambda r: check(r, "AO4").pop("version"), "check AO4: missing version"),
    ("version 0", lambda r: check(r, "AO4").update(version=0), "check AO4 version must be a version"),
    ("check id with a slash", lambda r: check(r, "AO4").update(id="core/AO4"), "check core/AO4 id 'core/AO4' may use only letters"),
    ("unknown type", lambda r: check(r, "AO4").update(type="safety"), "check AO4 type must be one of fidelity, gate"),
    ("unknown polarity", lambda r: check(r, "AO4").update(polarity="good"), "check AO4 polarity must be one of desired, undesired"),
    ("empty criterion", lambda r: check(r, "AO4").update(criterion=" "), "check AO4 criterion is empty"),
    ("own tie-break a number", lambda r: check(r, "AO3").update(default_when_unsure=0), "check AO3 default_when_unsure must be text"),
    ("group a number", lambda r: check(r, "AO4").update(group=1), "check AO4 group must be text"),
    ("a source item YAML reads as a number", lambda r: check(r, "AO3").update(source=[1.1]),
     "check AO3 source item 1 must be text; write it in quotes"),
    ("tags a block", lambda r: check(r, "AO3").update(tags={"rubric": "mi"}), "check AO3 tags must be a list of text items"),
    ("key misspelled", lambda r: check(r, "AO4").update(critreion=check(r, "AO4").pop("criterion")),
     "unknown key 'critreion' (did you mean criterion?)"),
    # points
    ("desired with negative points", lambda r: check(r, "AO4").update(points=-5),
     "check AO4 points must be positive for a desired check and negative for an undesired one"),
    ("undesired with positive points", lambda r: check(r, "KM1-C3").update(points=7), "it is undesired with 7"),
    ("points beyond HealthBench's range", lambda r: check(r, "AO4").update(points=12), "check AO4 points must be a whole number from -10 to 10"),
    ("zero points", lambda r: check(r, "AO4").update(points=0), "other than 0 (HealthBench's range), not the number 0"),
    ("fractional points", lambda r: check(r, "AO4").update(points=4.5), "not the number 4.5"),
    ("no points", lambda r: check(r, "AO4").pop("points"), "check AO4: missing points"),
    ("a gate with points", lambda r: check(r, "KM7-G1").update(points=-10), "check KM7-G1: a gate has no points"),
    # severity
    ("a gate without severity", lambda r: check(r, "KM7-G1").pop("severity"), "check KM7-G1: a gate needs a severity, one of S1, S2, S3"),
    ("severity off the ladder", lambda r: check(r, "KM1-C3").update(severity="high"), "check KM1-C3 severity must be one of S1, S2, S3"),
    # applies
    ("applies a word other than always", lambda r: check(r, "AO4").update(applies="everywhere"), "check AO4 applies must be always, or a block"),
    ("always and at", lambda r: check(r, "KM1-C3")["applies"].update(always=True), "check KM1-C3 applies: give always: true or at: [...], not both"),
    ("neither always nor at", lambda r: check(r, "KM1-C3")["applies"].pop("at"), "check KM1-C3 applies: say where the check applies"),
    ("always false", lambda r: check(r, "AO4")["applies"].update(always=False), "check AO4 applies.always must be true"),
    ("a checkpoint without its scenario", lambda r: check(r, "KM1-C3")["applies"].update(at=["KM1"]),
     "check KM1-C3 applies.at item 1 'KM1' must name the scenario and the checkpoint"),
    ("a checkpoint id with a bad character", lambda r: check(r, "KM1-C3")["applies"].update(at=["vaping-mini/KM1!"]),
     "check KM1-C3 applies.at item 1 checkpoint 'KM1!' may use only letters"),
    ("false positives on a fidelity check", lambda r: check(r, "KM1-C3")["applies"].update(false_positive_at=["vaping-mini/KM8"]),
     "check KM1-C3 applies.false_positive_at is for gates only"),
    ("a checkpoint under both lists", lambda r: check(r, "KM7-G1")["applies"]["false_positive_at"].append("vaping-mini/KM7"),
     "check KM7-G1 applies: vaping-mini/KM7 is under both at and false_positive_at"),
    ("an NA code the rubric does not list", lambda r: (r["defaults"]["na_codes"].remove("NA_NO_REQUEST")),
     "check AO4 applies.na_code must be one of NA_NO_ADVICE, NA_NOT_ESTABLISHED, NA_OTHER"),
    ("an NA code with no condition", lambda r: check(r, "AO4")["applies"].pop("when"), "check AO4 applies: na_code needs a when"),
    ("a condition that is a list", lambda r: check(r, "AO4")["applies"].update(when=["asks"]), "check AO4 applies.when must be text"),
    ("applies key misspelled", lambda r: check(r, "AO4")["applies"].update(whenever="always"), "check AO4 applies: unknown key 'whenever'"),
    # approach
    ("approach on a check that names its checkpoints", lambda r: check(r, "KM1-C3").update(approach=["evoking"]),
     "check KM1-C3: approach narrows only a check that applies always"),
    ("an approach the rubric does not name", lambda r: check(r, "AO3")["approach"].append("CBT"),
     "check AO3 approach 'CBT' is not one of this rubric's approaches"),
    # tie-break
    ("no tie-break anywhere", lambda r: (r.pop("defaults"), check(r, "AO3").pop("default_when_unsure")),
     "check AO3: no rule for when the grader is unsure"),
    # the grader refuses cannot_determine on anything but a gate (bcb/grader.py parse)
    ("a check's own tie-break is a gate's", lambda r: check(r, "AO4").update(default_when_unsure="cannot_determine"),
     "check AO4 default_when_unsure is cannot_determine, which the grader may answer only on a gate"),
    ("a rubric's tie-break for a polarity is a gate's", lambda r: r["defaults"]["default_when_unsure"].update(
        undesired="cannot_determine -> human review"),
     "defaults.default_when_unsure.undesired is cannot_determine, which the grader may answer only on a gate"),
]


class MalformedRubricTest(unittest.TestCase):
    def test_each_mistake_is_named(self):
        assert_each_named(self, rubric.problems, parse(SPEC_FIELDS_RUBRIC), CASES)

    def test_a_value_of_the_wrong_kind_anywhere_is_named_not_raised(self):
        assert_never_raises(self, rubric.problems, parse(SPEC_FIELDS_RUBRIC))

    def test_a_file_that_is_not_a_block_is_named(self):
        self.assertEqual(rubric.problems("checks"), ["the file must be a block of keys (canary, rubric, defaults, "
                                                     "theory_consistency, checks), not 'checks'"])

    def test_load_lists_every_problem_at_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "core.yaml"
            path.write_text(MINIMAL.replace("points: 5", "points: -5").replace("version: 1}", "version: 0.1}"))
            with self.assertRaises(FormatError) as e:
                rubric.load(path)
        self.assertEqual(e.exception.problems, [
            'rubric.version must be written in quotes, such as "0.1", since YAML reads it as the number 0.1 and 0.10 '
            "would become 0.1",
            "check EMPATHY points must be positive for a desired check and negative for an undesired one "
            "(it is desired with -5)"])


if __name__ == "__main__":
    unittest.main()
