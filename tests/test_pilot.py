import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from bcb import branches as br, client, pilot
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

    def test_references_grade_the_branch_files_own_replies(self):
        args = ["--references", "--branches", str(self.branches), "--out", str(self.tmp), "--grader", "deepseek:deepseek-flash"]
        self.assertEqual(pilot.main(args, call=fake(reply="unused")), 0)
        units = [json.loads(x) for x in (next(self.tmp.glob("*-references")) / "units.jsonl").read_text().splitlines()]
        self.assertEqual(sorted((u["checkpoint"], u["model"]) for u in units),
                         [("HQ-MID", "near_miss"), ("HQ-MID", "reference"), ("HQ-TEST", "near_miss"), ("HQ-TEST", "reference")])
        self.assertEqual({u["reply"] for u in units if u["model"] == "reference"}, {OPENS_S3["reference_reply"]})


if __name__ == "__main__":
    unittest.main()
