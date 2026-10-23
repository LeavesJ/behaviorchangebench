"""The run file (infra audit 4.2) and `python -m bcb.validate`: a run file is checked with every file it names, alone
and against each other, and each malformed case gets a problem that names it."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import yaml

from bcb import runfile, validate
from bcb.schema import CANARY, FormatError
from tests.formats_examples import (AUDIT_CHECKS, AUDIT_CONVERSATION, AUDIT_MODELS, AUDIT_RUBRIC, AUDIT_RUN,
                                    assert_each_named, assert_never_raises, parse)

PATHS = {"run": "configs/walking-repro.yaml", "conversation": "scenarios/activity/walking-v0/conversation.yaml",
         "core": "rubrics/core-mi.yaml", "checks": "scenarios/activity/walking-v0/checks.yaml",
         "models": "configs/models.yaml"}


def audit_files():
    return {"run": parse(AUDIT_RUN), "conversation": parse(AUDIT_CONVERSATION), "core": parse(AUDIT_RUBRIC),
            "checks": parse(AUDIT_CHECKS), "models": parse(AUDIT_MODELS)}


def walking_files():
    """The audit's files, plus the spec fields that cross files: approach names and a trigger, and an always_on map;
    and BCB's canary and a stand-in license, so that a good file has no warnings either (tests/test_publishing.py)."""
    files = audit_files()
    files["checks"]["theory_consistency"] = {"approaches": ["MI", "RP"], "licensed_triggers": {"T6": "a plan is not working"}}
    c1, c2 = files["conversation"]["checkpoints"]
    c1["always_on"] = {"MI.PERMISSION": "depends_on_reply"}
    c2["approach"] = {"in_prefix": "MI", "expected": "RP", "trigger": {"code": "T6", "quote": "made it 2 days"}}
    for key in ("conversation", "core", "checks"):
        files[key]["canary"] = CANARY
    files["conversation"]["scenario"]["license"] = "LicenseRef-test-only"  # a test value, not a choice of license
    return files


def write_all(root, files):
    for key, raw in files.items():
        path = Path(root) / PATHS[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return Path(root) / PATHS["run"]


def problems(files):
    with tempfile.TemporaryDirectory() as tmp:
        write_all(tmp, files)
        return runfile.run_problems(files["run"], tmp)


class ValidRunFileTest(unittest.TestCase):
    def test_the_audits_run_file_loads_with_everything_it_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = runfile.load(write_all(tmp, audit_files()), tmp)
            self.assertEqual(run.out, Path(tmp) / "runs/")
        self.assertEqual((run.id, run.samples, run.grade_with, run.grade_read, run.budget_usd),
                         ("walking-repro", 3, "primary", "normalized", 25))
        self.assertEqual(run.checkpoints, ("walking-v0/C1", "walking-v0/C2", "walking-v0/C1-KNEE"))
        self.assertEqual(run.gates, runfile.Gates(3, "pass_k", "rank_below"))
        self.assertEqual(([s.id for s in run.scenarios], [r.id for r in run.rubrics], [m.id for m in run.models.models]),
                         (["walking-v0"], ["core-mi", "walking-v0-checks"], ["gpt-6-astra", "gemini-3.1-pro"]))

    def test_a_minimal_run_file_keeps_todays_scoring_rules(self):
        files = audit_files()
        files["run"] = {"run": {"id": "minimal"}, "scenarios": ["scenarios/activity/walking-v0"],
                        "rubrics": ["rubrics/core-mi.yaml"], "models": "configs/models.yaml", "samples": 2}
        with tempfile.TemporaryDirectory() as tmp:
            run = runfile.load(write_all(tmp, files), tmp)
            self.assertEqual(run.out, Path(tmp) / "runs")
        self.assertEqual(run.checkpoints, ("walking-v0/C1", "walking-v0/C2", "walking-v0/C1-KNEE"), "no patterns: all")
        # no gate cap and fixed-N/A rows counted: what the Oct 8 numbers used; both are undecided (runfile docstring)
        self.assertEqual((run.gates, run.static_na), (runfile.Gates(2, "pass_k", "none"), "count"))
        self.assertEqual((run.grade_with, run.grade_read, run.budget_usd), (None, "raw", None))

    def test_the_spec_fields_that_cross_files_agree(self):
        self.assertEqual(problems(walking_files()), [])

    def test_a_check_for_a_scenario_outside_the_run_is_left_alone(self):
        files = walking_files()
        files["checks"]["checks"][0]["applies"]["at"].append("running-v0/C9")
        self.assertEqual(problems(files), [])


def check(files, key, check_id):
    return next(c for c in files[key]["checks"] if c["id"] == check_id)


CASES = [
    # the run file's own shape
    ("misspelled top key", lambda f: f["run"].update(sample=f["run"].pop("samples")), "unknown key 'sample' (did you mean samples?)"),
    ("no samples", lambda f: f["run"].pop("samples"), "the file: missing samples"),
    ("zero samples", lambda f: f["run"].update(samples=0), "samples must be a whole number, 1 or more"),
    ("k above samples", lambda f: f["run"].update(samples=2),
     "gates.k is 3 but samples is 2: pass^k needs at least k replies per checkpoint and model"),
    ("an unknown cap", lambda f: f["run"]["gates"].update(cap="always"), "gates.cap must be one of none, rank_below"),
    ("an unknown gate rule", lambda f: f["run"]["gates"].update(rule="majority"), "gates.rule must be one of pass_k"),
    ("an unknown reading", lambda f: f["run"]["grade"].update(read="clean"), "grade.read must be one of raw, normalized"),
    ("grade without a grader", lambda f: f["run"]["grade"].pop("with"), "grade: missing with"),
    ("a budget below zero", lambda f: f["run"].update(budget_usd=-5), "budget_usd must be a number of US dollars above 0"),
    ("an unknown static_na rule", lambda f: f["run"].update(static_na="drop"), "static_na must be one of count, exclude"),
    ("a pattern without its scenario", lambda f: f["run"].update(checkpoints=["C*"]),
     "checkpoints item 1 'C*' must name the scenario too, as <scenario>/<checkpoint>"),
    ("run id with a space", lambda f: f["run"]["run"].update(id="walking repro"), "run.id 'walking repro' may use only letters"),
    ("run a word, not a block", lambda f: f["run"].update(run="walking-repro"), "run must be a block of keys (id, out), not 'walking-repro'"),
    ("out a number", lambda f: f["run"]["run"].update(out=5), "run.out must be text"),
    ("models a list", lambda f: f["run"].update(models=["configs/models.yaml"]), "models must be text, not a list"),
    ("grader name with a space", lambda f: f["run"]["grade"].update(**{"with": "first pass"}), "grade.with 'first pass' may use only letters"),
    ("k zero", lambda f: f["run"]["gates"].update(k=0), "gates.k must be a whole number, 1 or more"),
    ("rubrics a number", lambda f: f["run"].update(rubrics=5), "rubrics must be a list of text items"),
    # the files it names
    ("a scenario folder that is not there", lambda f: f["run"].update(scenarios=["scenarios/activity/running-v0"]),
     "scenarios/activity/running-v0: no such file"),
    ("a problem inside a named file carries its path", lambda f: check(f, "core", "MI.EMPATHY").update(points=0),
     "rubrics/core-mi.yaml: check MI.EMPATHY points must be a whole number from -10 to 10"),
    ("a same-family grader in the models file", lambda f: f["models"]["models"].append(
        {"id": "claude", "provider": "openrouter", "model": "anthropic/claude-sonnet-5", "family": "anthropic"}),
     "configs/models.yaml: grader primary is in the anthropic family, and so is model claude"),
    # the files against each other
    ("a pattern that matches nothing", lambda f: f["run"].update(checkpoints=["walking-v0/C9"]),
     "checkpoints: 'walking-v0/C9' matches no checkpoint (the scenarios have walking-v0/C1, walking-v0/C2, walking-v0/C1-KNEE)"),
    ("a pattern for a scenario not in the run", lambda f: f["run"].update(checkpoints=["running-v0/C1"]),
     "checkpoints: 'running-v0/C1' names a scenario that is not under scenarios (walking-v0)"),
    ("two files with one scenario id", lambda f: f["run"]["scenarios"].append(PATHS["conversation"]),
     "scenarios: two files have scenario id walking-v0"),
    ("a check id in two rubrics", lambda f: check(f, "checks", "WALK.C1.OPEN-Q").update(id="MI.EMPATHY"),
     "scenario walking-v0: check id MI.EMPATHY is in rubric core-mi and in rubric walking-v0-checks, and both can be "
     "graded in this scenario"),
    ("one rubric listed twice", lambda f: f["run"]["rubrics"].append(PATHS["core"]), "rubrics: two files have rubric id core-mi"),
    ("a check at a checkpoint the scenario lacks", lambda f: check(f, "checks", "WALK.C1.OPEN-Q")["applies"].update(at=["walking-v0/C9"]),
     "rubric walking-v0-checks: check WALK.C1.OPEN-Q applies at walking-v0/C9, but scenario walking-v0 has no checkpoint C9"),
    ("a false-positive checkpoint the scenario lacks", lambda f: check(f, "checks", "WALK.G1")["applies"].update(
        false_positive_at=["walking-v0/C3"]), "check WALK.G1 applies at walking-v0/C3, but scenario walking-v0 has no checkpoint C3"),
    ("always_on naming a check that is not always-on", lambda f: f["conversation"]["checkpoints"][0].update(
        always_on={"WALK.C1.OPEN-Q": "not_applicable"}),
     "scenario walking-v0: checkpoint C1 always_on names WALK.C1.OPEN-Q, but no rubric in this run has a check "
     "WALK.C1.OPEN-Q that applies always"),
    ("a checkpoint approach the rubrics do not name", lambda f: f["conversation"]["checkpoints"][0].update(approach="CBT"),
     "scenario walking-v0: checkpoint C1 approach 'CBT' is not an approach the rubrics name (MI, RP)"),
    ("an in_prefix approach the rubrics do not name", lambda f: f["conversation"]["checkpoints"][1]["approach"].update(
        in_prefix="mi"), "checkpoint C2 approach 'mi' is not an approach the rubrics name"),
    ("a trigger the rubrics do not license", lambda f: f["conversation"]["checkpoints"][1]["approach"]["trigger"].update(code="T9"),
     "scenario walking-v0: checkpoint C2 trigger 'T9' is not a licensed trigger the rubrics name (T6)"),
    ("a core check approach the rubrics do not name", lambda f: check(f, "core", "MI.EMPATHY").update(approach=["MI", "CBT"]),
     "rubric core-mi: check MI.EMPATHY approach 'CBT' is not an approach the rubrics name (MI, RP)"),
    ("a grader the models file lacks", lambda f: f["run"]["grade"].update(**{"with": "third"}),
     "grade.with 'third' is not a grader in configs/models.yaml (primary, second)"),
]


class MalformedRunFileTest(unittest.TestCase):
    def test_each_mistake_is_named(self):
        assert_each_named(self, problems, walking_files(), CASES)

    def test_a_value_of_the_wrong_kind_anywhere_in_the_run_file_is_named_not_raised(self):
        # the files it names stay valid, so a wrong kind in the run file also reaches the checks across files
        files = walking_files()
        with tempfile.TemporaryDirectory() as tmp:
            write_all(tmp, files)
            assert_never_raises(self, lambda raw: runfile.run_problems(raw, tmp), files["run"])

    def test_a_file_that_is_not_a_block_is_named(self):
        self.assertEqual(runfile.run_problems("configs/walking-repro.yaml"), [
            "the file must be a block of keys (run, scenarios, checkpoints, rubrics, models, samples, grade, gates, "
            "budget_usd, static_na), not 'configs/walking-repro.yaml'"])

    def test_load_lists_every_problem_from_every_file(self):
        files = walking_files()
        files["run"]["samples"] = 0
        check(files, "core", "MI.EMPATHY")["points"] = 0
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FormatError) as e:
                runfile.load(write_all(tmp, files), tmp)
        # the run file's own mistake and the one inside a file it names, in one pass; k is not compared with a bad count
        self.assertEqual(e.exception.problems, [
            "samples must be a whole number, 1 or more, not the number 0",
            "rubrics/core-mi.yaml: check MI.EMPATHY points must be a whole number from -10 to 10 other than 0 "
            "(HealthBench's range), not the number 0"])


def run_cli(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = validate.main(argv)
    return code, out.getvalue()


class ValidateCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_all(self.root, walking_files())

    def tearDown(self):
        self.tmp.cleanup()

    def test_good_files_say_ok_and_exit_0(self):
        args = [str(self.root / PATHS[k]) for k in ("conversation", "core", "checks", "models", "run")]
        code, out = run_cli(args + ["--root", str(self.root)])
        self.assertEqual(code, 0, out)
        self.assertEqual([line.rsplit(" (", 1)[1] for line in out.splitlines()],
                         ["conversation): ok", "rubric): ok", "rubric): ok", "models config): ok", "run file): ok"])

    def test_a_scenario_folder_means_its_conversation(self):
        code, out = run_cli([str(self.root / "scenarios/activity/walking-v0")])
        self.assertEqual((code, out.strip().rsplit(" ", 2)[1:]), (0, ["(conversation):", "ok"]))

    def test_a_bad_file_lists_each_problem_and_exits_1(self):
        bad = self.root / "bad.yaml"
        bad.write_text("rubric: {id: core, version: 1}\nchecks: []\n")
        code, out = run_cli([str(self.root / PATHS["core"]), str(bad)])
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[1:], [f"{bad} (rubric): 1 problem",
                                                "  - checks must be a list with at least one check, not an empty list"])

    def test_a_run_file_without_its_run_block_is_still_read_as_a_run_file(self):
        path = self.root / PATHS["run"]
        files = walking_files()
        files["run"].pop("run")
        path.write_text(yaml.safe_dump(files["run"], sort_keys=False))
        code, out = run_cli([str(path), "--root", str(self.root)])
        self.assertEqual((code, out.splitlines()), (1, [f"{path} (run file): 1 problem", "  - the file: missing run"]))

    def test_an_empty_file_and_a_folder_without_a_conversation_are_named(self):
        (self.root / "empty.yaml").write_text("# nothing yet\n")
        (self.root / "scenarios/activity/running-v0").mkdir(parents=True)
        code, out = run_cli([str(self.root / "empty.yaml"), str(self.root / "scenarios/activity/running-v0")])
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[1::2], ["  - the file is empty",
                                                  "  - a scenario folder must hold its conversation.yaml, and this one "
                                                  "does not"])

    def test_a_file_of_no_known_kind_is_named(self):
        odd = self.root / "odd.yaml"
        odd.write_text("notes: [a list of things]\n")
        code, out = run_cli([str(odd)])
        self.assertEqual(code, 1)
        self.assertIn("cannot tell what kind of file this is", out)


if __name__ == "__main__":
    unittest.main()
