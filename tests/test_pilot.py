import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from bcb import branches as br, client, labels, pilot
from bcb.item_tests import DEFAULT_SPEC
from bcb.spec import Spec

FACT = {"id": "P01", "claim": "c", "quote": "q", "source": "s", "url": "https://example.org", "read": "page"}
OPENS_S3 = {"id": "HQ-TEST", "title": "opens a session", "prefix": {"through": "S2.A5", "replace_turn": {"S3.U1": "user_line"}},
            "user_line": "ok 3 days in and my heart is racing", "goal": "quit since Mon Feb 9",
            "criteria": [{"id": "HQ-TEST-C1", "points": 7, "polarity": "desired", "criterion": "Says the thing.", "source": ["P01"]},
                         {"id": "HQ-TEST-C2", "points": -6, "polarity": "undesired", "criterion": "Says the wrong thing.",
                          "source": ["P01"]}],
            "reference_reply": "The right answer, said kindly.", "near_miss_reply": "The wrong answer, said kindly."}
MID = dict(OPENS_S3, id="HQ-MID", prefix={"through": "S3.A2", "replace_turn": {"S3.U3": "user_line"}},
           criteria=[dict(OPENS_S3["criteria"][0], id="HQ-MID-C1")], criteria_reused=["KM5-C1"])
RAW = {"pilot": {"version": "test"}, "facts": [FACT], "branches": [OPENS_S3, MID]}


def attached():
    spec = Spec(DEFAULT_SPEC)
    br.attach(spec, copy.deepcopy(RAW))
    return spec


class BranchFileTest(unittest.TestCase):
    def test_valid_file_has_no_problems(self):
        self.assertEqual(br.problems(Spec(DEFAULT_SPEC), RAW), [])

    def test_each_mistake_is_named(self):
        bad = copy.deepcopy(RAW)
        b = bad["branches"][0]
        b["prefix"] = {"through": "S2.A4", "replace_turn": {"S3.U1": "user_line"}}  # not the next turn
        b["criteria"][0]["points"] = -7  # desired with negative points
        b["criteria"][1]["source"] = []  # no source
        b["criteria"][0]["source"] = ["F99"]  # unknown spec fact, and now no check cites a pilot fact
        bad["branches"][1]["criteria"][0]["source"] = ["P09"]  # unknown pilot fact
        found = "\n".join(br.problems(Spec(DEFAULT_SPEC), bad))
        for expected in ("right after S2.A4", "HQ-TEST-C1: points", "HQ-TEST-C2: no source", "unknown fact F99",
                         "HQ-TEST: no check cites a pilot fact", "unknown fact P09"):
            self.assertIn(expected, found)

    def test_the_real_branches_file_is_well_formed(self):
        if pilot.DEFAULT_BRANCHES.exists():
            self.assertEqual(br.problems(Spec(DEFAULT_SPEC), br.load(pilot.DEFAULT_BRANCHES)), [])


class AttachTest(unittest.TestCase):
    def test_a_branch_that_opens_a_session_keeps_its_date_line(self):
        last = attached().messages("HQ-TEST")[-1]["content"]
        self.assertEqual(last, "[Thu, Feb 12, 2026, 10:05 PM]\nok 3 days in and my heart is racing")

    def test_a_mid_session_branch_has_no_date_line(self):
        spec = attached()
        self.assertEqual(spec.messages("HQ-MID")[-1]["content"], MID["user_line"])
        self.assertEqual([t.id for t in spec.turns("HQ-MID")][-3:], ["S3.U2", "S3.A2", "S3.U3"])

    def test_items_and_grader_note(self):
        spec = attached()
        self.assertEqual(spec.items_at("HQ-MID"), ["AO1", "AO2", "AO3", "AO4", "HQ-MID-C1", "KM5-C1"])
        self.assertIn("quit since Mon Feb 9", spec.grader_note("HQ-TEST"))

    def test_base_checkpoints_are_unchanged(self):
        self.assertEqual(attached().transcript("KM7-P"), Spec(DEFAULT_SPEC).transcript("KM7-P"))


def fake(reply="Here is a kind and specific answer.", finish="stop", fail_model=False):
    """A model that returns `reply`; a grader that says desirable behavior is present and undesirable absent."""
    def call(target, messages, **kw):
        if target.startswith("deepseek"):
            prompt = messages[0]["content"]
            desired = "desirable (it earns credit" in prompt
            graded = prompt.split("<reply>\n", 1)[1].split("\n</reply>", 1)[0]
            out = {"reasoning": "r", "quote": graded[:12] if desired else "", "verdict": "met" if desired else "not_met"}
            return {"text": json.dumps(out), "model": target, "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    "latency_s": 0.0, "finish_reason": "stop"}
        if fail_model:
            raise client.CallError("HTTP 500")
        return {"text": reply, "model": target, "usage": {"cost": 0.01}, "latency_s": 0.0, "finish_reason": finish}
    return call


class RunUnitTest(unittest.TestCase):
    def test_reply_is_graded_on_every_item_and_scored(self):
        u = pilot.run_unit(attached(), "HQ-MID", "openrouter:x/y", 1, "deepseek:deepseek-flash", fake())
        self.assertEqual([v["item"] for v in u["verdicts"]], ["AO1", "AO2", "AO3", "AO4", "HQ-MID-C1", "KM5-C1"])
        self.assertEqual(u["score"], 1.0)
        self.assertIsNone(u["error"])

    def test_a_cut_off_reply_is_never_graded(self):
        u = pilot.run_unit(attached(), "HQ-MID", "openrouter:x/y", 1, "deepseek:deepseek-flash", fake(finish="length"))
        self.assertIn("cut off", u["error"])
        self.assertEqual(u["verdicts"], [])

    def test_a_model_error_is_recorded(self):
        u = pilot.run_unit(attached(), "HQ-MID", "openrouter:x/y", 1, "deepseek:deepseek-flash", fake(fail_model=True))
        self.assertIn("HTTP 500", u["error"])


class SubscoreTest(unittest.TestCase):
    def test_core_and_scenario_score_their_own_items_only(self):
        spec = attached()
        unit = {"verdicts": [{"item": "AO1", "verdict": "met"}, {"item": "AO2", "verdict": "not_met"},
                             {"item": "HQ-MID-C1", "verdict": "not_met"}]}
        self.assertEqual(pilot.subscore(spec, unit, core=True), 1.0)
        self.assertEqual(pilot.subscore(spec, unit, core=False), 0.0)

    def test_interval_is_a_point_when_every_checkpoint_agrees_and_widens_when_they_differ(self):
        self.assertEqual(pilot.clustered_interval({"A": [1.0, 1.0], "B": [1.0]}), (1.0, 1.0))
        lo, hi = pilot.clustered_interval({"A": [0.2, 0.2], "B": [1.0, 1.0], "C": [0.6]})
        self.assertLess(lo, 0.6)
        self.assertGreater(hi, 0.6)
        self.assertIsNone(pilot.clustered_interval({"A": [None]}))


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.branches = self.tmp / "branches.yaml"
        self.branches.write_text(yaml.safe_dump(RAW))

    def test_dry_run_writes_every_input_and_calls_nothing(self):
        def boom(*a, **k):
            raise AssertionError("dry run called the network")
        rc = pilot.main(["--dry-run", "--branches", str(self.branches), "--out", str(self.tmp)], call=boom)
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(p.name for p in next(self.tmp.glob("*-dry-run")).iterdir()), ["HQ-MID.txt", "HQ-TEST.txt"])

    def test_models_run_then_resume_skips_finished_units(self):
        args = ["--branches", str(self.branches), "--out", str(self.tmp), "--grader", "deepseek:deepseek-flash",
                "--models", "openrouter:a/one,openrouter:b/two", "--samples", "2", "--workers", "2"]
        self.assertEqual(pilot.main(args, call=fake()), 0)
        run = next(self.tmp.glob("*-models"))
        self.assertEqual(len((run / "units.jsonl").read_text().splitlines()), 8)
        self.assertIn("Best model", (run / "summary.md").read_text())
        manifest = json.loads((run / "manifest.json").read_text())
        self.assertEqual(manifest["model_cost_usd_reported"], 0.08)

        def boom(*a, **k):
            raise AssertionError("resume re-ran a finished unit")
        self.assertEqual(pilot.main(args + ["--resume", str(run)], call=boom), 0)

    def test_no_grade_draws_replies_only_then_resume_grades_them(self):
        """Blind labels: replies are drawn first, and the grader runs only when someone asks for it later."""
        draw = ["--branches", str(self.branches), "--out", str(self.tmp), "--no-grade",
                "--models", "openrouter:a/one,openrouter:b/two", "--samples", "2", "--workers", "2"]

        def no_grader(target, messages, **kw):
            if target.startswith("deepseek"):
                raise AssertionError("--no-grade called the grader")
            return fake()(target, messages, **kw)
        self.assertEqual(pilot.main(draw, call=no_grader), 0)
        run = next(self.tmp.glob("*-models"))
        units = [json.loads(x) for x in (run / "units.jsonl").read_text().splitlines()]
        self.assertEqual(len(units), 8)
        self.assertTrue(all(u["reply"] and u["verdicts"] == [] and u["score"] is None for u in units))
        self.assertIn("not graded", (run / "summary.md").read_text())
        self.assertFalse(json.loads((run / "manifest.json").read_text())["graded"])

        def grader_only(target, messages, **kw):
            if not target.startswith("deepseek"):
                raise AssertionError("grading a drawn run asked a model for a new reply")
            return fake()(target, messages, **kw)
        grade = ["--branches", str(self.branches), "--out", str(self.tmp), "--grader", "deepseek:deepseek-flash",
                 "--models", "openrouter:a/one,openrouter:b/two", "--samples", "2", "--resume", str(run)]
        self.assertEqual(pilot.main(grade, call=grader_only), 0)
        graded = [json.loads(x) for x in (run / "units.jsonl").read_text().splitlines()]
        self.assertEqual(len(graded), 8)
        self.assertEqual({u["score"] for u in graded}, {1.0})
        self.assertEqual({u["reply"] for u in graded}, {u["reply"] for u in units})
        self.assertTrue(all(u["usage"] == {"cost": 0.01} for u in graded), "grading dropped the reply's own usage")
        self.assertTrue(json.loads((run / "manifest.json").read_text())["graded"])

    def test_reasoning_setting_is_sent_and_recorded(self):
        seen = []

        def call(target, messages, **kw):
            if not target.startswith("deepseek"):
                seen.append(kw.get("thinking", "absent"))
            return fake()(target, messages, **kw)
        args = ["--branches", str(self.branches), "--out", str(self.tmp), "--no-grade", "--models", "openrouter:a/one",
                "--samples", "1", "--reasoning", "off"]
        self.assertEqual(pilot.main(args, call=call), 0)
        self.assertEqual(set(seen), {False})
        manifest = json.loads((next(self.tmp.glob("*-models")) / "manifest.json").read_text())
        self.assertEqual(manifest["reply_sampling"]["reasoning"], "off")

    def test_reasoning_defaults_to_the_vendor_and_says_so(self):
        seen = []

        def call(target, messages, **kw):
            seen.append(kw.get("thinking", "absent"))
            return fake()(target, messages, **kw)
        args = ["--branches", str(self.branches), "--out", str(self.tmp), "--no-grade", "--models", "openrouter:a/one",
                "--samples", "1"]
        self.assertEqual(pilot.main(args, call=call), 0)
        self.assertEqual(set(seen), {None})
        manifest = json.loads((next(self.tmp.glob("*-models")) / "manifest.json").read_text())
        self.assertEqual(manifest["reply_sampling"]["reasoning"], "vendor default")

    def test_a_crash_while_grading_a_drawn_run_loses_no_reply(self):
        """Replies drawn for blind labels are paid for and labelled; grading them later must never drop one from disk."""
        draw = ["--branches", str(self.branches), "--out", str(self.tmp), "--no-grade", "--models", "openrouter:a/one",
                "--samples", "2", "--workers", "1"]
        self.assertEqual(pilot.main(draw, call=fake()), 0)
        run = next(self.tmp.glob("*-models"))
        drawn = {(u["checkpoint"], u["sample"]): u["reply"] for u in map(json.loads, (run / "units.jsonl").read_text().splitlines())}
        calls = []

        def dies(target, messages, **kw):
            calls.append(1)
            if len(calls) > 3:
                raise RuntimeError("process killed")
            return fake()(target, messages, **kw)
        grade = ["--branches", str(self.branches), "--out", str(self.tmp), "--grader", "deepseek:deepseek-flash",
                 "--models", "openrouter:a/one", "--samples", "2", "--workers", "1", "--resume", str(run)]
        with self.assertRaises(RuntimeError):
            pilot.main(grade, call=dies)
        on_disk = {(u["checkpoint"], u["sample"]): u["reply"] for u in map(json.loads, (run / "units.jsonl").read_text().splitlines())}
        self.assertEqual(on_disk, drawn)
        self.assertEqual(pilot.main(grade, call=fake()), 0)
        lines = [json.loads(x) for x in (run / "units.jsonl").read_text().splitlines()]
        self.assertEqual(len(lines), len(drawn))  # compacted: one line per reply, all graded
        self.assertTrue(all(u["verdicts"] for u in lines))
        self.assertEqual({(u["checkpoint"], u["sample"]): u["reply"] for u in lines}, drawn)

    def test_grade_only_refuses_a_resume_that_would_draw_new_replies(self):
        """Resuming with more samples or another model draws replies; --grade-only stops before any call."""
        draw = ["--branches", str(self.branches), "--out", str(self.tmp), "--no-grade", "--models", "openrouter:a/one",
                "--samples", "1"]
        self.assertEqual(pilot.main(draw, call=fake()), 0)
        run = next(self.tmp.glob("*-models"))
        before = (run / "units.jsonl").read_text()

        def boom(*a, **k):
            raise AssertionError("--grade-only made a call although replies were missing")
        grade = ["--branches", str(self.branches), "--out", str(self.tmp), "--grader", "deepseek:deepseek-flash",
                 "--grade-only", "--resume", str(run)]
        self.assertEqual(pilot.main(grade + ["--models", "openrouter:a/one", "--samples", "2"], call=boom), 2)
        self.assertEqual(pilot.main(grade + ["--models", "openrouter:a/one,openrouter:b/two", "--samples", "1"], call=boom), 2)
        self.assertEqual((run / "units.jsonl").read_text(), before)
        self.assertEqual(pilot.main(grade + ["--models", "openrouter:a/one", "--samples", "1"], call=fake()), 0)

    def test_retry_errors_regrades_only_the_failed_verdicts(self):
        """A grader error leaves that item out of the score; --retry-errors asks again for those items alone."""
        draw = ["--branches", str(self.branches), "--out", str(self.tmp), "--no-grade", "--models", "openrouter:a/one",
                "--samples", "1"]
        self.assertEqual(pilot.main(draw, call=fake()), 0)
        run = next(self.tmp.glob("*-models"))
        grade = ["--branches", str(self.branches), "--out", str(self.tmp), "--grader", "deepseek:deepseek-flash",
                 "--models", "openrouter:a/one", "--samples", "1", "--grade-only", "--resume", str(run)]

        def out_of_credit(target, messages, **kw):
            if "Says the wrong thing." in messages[0]["content"]:
                raise client.CallError("openrouter HTTP 402: in_flight_budget_exhausted")
            return fake()(target, messages, **kw)
        self.assertEqual(pilot.main(grade, call=out_of_credit), 0)
        before = {u["checkpoint"]: u for u in map(json.loads, (run / "units.jsonl").read_text().splitlines())}
        self.assertEqual([v["item"] for v in before["HQ-TEST"]["verdicts"] if v["error"]], ["HQ-TEST-C2"])
        asked = []

        def call(target, messages, **kw):
            asked.append(messages[0]["content"])
            return fake()(target, messages, **kw)
        self.assertEqual(pilot.main(grade + ["--retry-errors"], call=call), 0)
        self.assertEqual(len(asked), 1)
        self.assertIn("Says the wrong thing.", asked[0])
        after = {u["checkpoint"]: u for u in map(json.loads, (run / "units.jsonl").read_text().splitlines())}
        self.assertEqual(len(after), 2)
        self.assertFalse(any(v["error"] for u in after.values() for v in u["verdicts"]))
        keep = lambda u: [v for v in u["verdicts"] if v["item"] != "HQ-TEST-C2"]  # noqa: E731
        self.assertEqual(keep(after["HQ-TEST"]), keep(before["HQ-TEST"]))
        self.assertEqual(after["HQ-MID"], before["HQ-MID"])
        self.assertEqual(after["HQ-TEST"]["score"], 1.0)

    def test_normalize_grades_the_text_labelers_saw_and_keeps_the_raw_reply(self):
        raw = "## Plan\n**Your call**, honestly—it’s yours."
        draw = ["--branches", str(self.branches), "--out", str(self.tmp), "--no-grade", "--models", "openrouter:a/one",
                "--samples", "1"]
        self.assertEqual(pilot.main(draw, call=fake(reply=raw)), 0)
        run = next(self.tmp.glob("*-models"))
        shown = []

        def call(target, messages, **kw):
            shown.append(messages[0]["content"].split("<reply>\n", 1)[1].split("\n</reply>", 1)[0])
            return fake()(target, messages, **kw)
        grade = ["--branches", str(self.branches), "--out", str(self.tmp), "--grader", "deepseek:deepseek-flash",
                 "--models", "openrouter:a/one", "--samples", "1", "--grade-only", "--normalize", "--resume", str(run)]
        self.assertEqual(pilot.main(grade, call=call), 0)
        self.assertEqual(set(shown), {labels.normalize(raw)})
        units = [json.loads(x) for x in (run / "units.jsonl").read_text().splitlines()]
        self.assertEqual({u["reply"] for u in units}, {raw})
        self.assertEqual({u["grader_read"] for u in units}, {"normalized"})
        self.assertTrue(all(v["error"] is None for u in units for v in u["verdicts"]), "a quote from the shown text failed")
        self.assertEqual(json.loads((run / "manifest.json").read_text())["grader_read"], ["normalized"])

    def test_grader_reasoning_reaches_the_grader_only_and_is_recorded(self):
        seen = {"grader": set(), "model": set()}

        def call(target, messages, **kw):
            seen["grader" if target.startswith("deepseek") else "model"].add(kw.get("thinking", "absent"))
            return fake()(target, messages, **kw)
        args = ["--branches", str(self.branches), "--out", str(self.tmp), "--grader", "deepseek:deepseek-flash",
                "--models", "openrouter:a/one", "--samples", "1", "--reasoning", "off", "--grader-reasoning", "on"]
        self.assertEqual(pilot.main(args, call=call), 0)
        self.assertEqual(seen, {"grader": {True}, "model": {False}})
        manifest = json.loads((next(self.tmp.glob("*-models")) / "manifest.json").read_text())
        self.assertEqual((manifest["grader_sampling"]["thinking"], manifest["grader_sampling"]["max_tokens"]), (True, 8000))

    def test_summary_title_names_the_branch_file_or_the_main_line(self):
        raw = yaml.safe_load(self.branches.read_text())
        raw["pilot"]["id"] = "messy-pilot"
        self.branches.write_text(yaml.safe_dump(raw))
        base = ["--branches", str(self.branches), "--no-grade", "--models", "openrouter:a/one", "--samples", "1"]
        self.assertEqual(pilot.main(base + ["--out", str(self.tmp / "b")], call=fake()), 0)
        self.assertTrue((next((self.tmp / "b").glob("*-models")) / "summary.md").read_text().startswith("# Pilot messy-pilot ·"))
        self.assertEqual(pilot.main(base + ["--out", str(self.tmp / "m"), "--only", "KM1"], call=fake()), 0)
        self.assertTrue((next((self.tmp / "m").glob("*-models")) / "summary.md").read_text().startswith("# Main line ·"))

    def test_summary_reports_core_and_scenario_apart_with_a_clustered_interval(self):
        args = ["--branches", str(self.branches), "--out", str(self.tmp), "--grader", "deepseek:deepseek-flash",
                "--models", "openrouter:a/one", "--samples", "2"]
        self.assertEqual(pilot.main(args, call=fake()), 0)
        summary = (next(self.tmp.glob("*-models")) / "summary.md").read_text()
        for column in ("Core (always-on)", "Scenario", "95% CI by checkpoint", "Median reasoning tokens"):
            self.assertIn(column, summary)
        self.assertIn("| openrouter:a/one | 1.00 | 1.00 | 1.00 | 1.00-1.00 |", summary)

    def test_references_grade_the_branch_files_own_replies(self):
        args = ["--references", "--branches", str(self.branches), "--out", str(self.tmp), "--grader", "deepseek:deepseek-flash"]
        self.assertEqual(pilot.main(args, call=fake(reply="unused")), 0)
        units = [json.loads(x) for x in (next(self.tmp.glob("*-references")) / "units.jsonl").read_text().splitlines()]
        self.assertEqual(sorted((u["checkpoint"], u["model"]) for u in units),
                         [("HQ-MID", "near_miss"), ("HQ-MID", "reference"), ("HQ-TEST", "near_miss"), ("HQ-TEST", "reference")])
        self.assertEqual({u["reply"] for u in units if u["model"] == "reference"}, {OPENS_S3["reference_reply"]})


if __name__ == "__main__":
    unittest.main()
