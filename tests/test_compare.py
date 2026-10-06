import csv
import json
import tempfile
import unittest
from pathlib import Path

from bcb import compare

FOLDER = "runs/main-line/test"
MODEL = "openrouter:a/one"


def label(check, verdict, role="first", unsure=False, source="human", checkpoint="KM7", sample=1, who="Ana"):
    return {"labeler": who, "role": role, "tab": "1 KM7", "row": 2, "checkpoint": checkpoint, "folder": FOLDER,
            "model": MODEL, "sample": sample, "n": 1, "reply_id": "ABCD", "check_id": check, "check_version": "0.1",
            "verdict": verdict, "na_code": "NA_NO_ADVICE" if verdict == "not_applicable" else None, "unsure": unsure,
            "quote": "", "quote_ok": None, "why": "a reason", "source": source, "corrected": None}


def unit(verdicts, checkpoint="KM7", sample=1, read="normalized"):
    return {"checkpoint": checkpoint, "model": MODEL, "sample": sample, "reply": "r", "error": None, "grader_read": read,
            "verdicts": [{"item": i, "verdict": v, "passes": None, "quote": "q", "reasoning": "because", "na_code": None,
                          "error": None if v else "timeout"} for i, v in verdicts.items()], "score": 0.5}


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.run = self.tmp / "run"
        self.run.mkdir()
        (self.run / "source.json").write_text(json.dumps({"copied_from": FOLDER}))

    def go(self, units, rows):
        (self.run / "units.jsonl").write_text("".join(json.dumps(u) + "\n" for u in units))
        (self.tmp / "labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        rc = compare.main([str(self.run), "--labels", str(self.tmp / "labels.jsonl"), "--out", str(self.tmp / "out")])
        if rc:
            return rc, None, None, None
        read = lambda name: list(csv.DictReader((self.tmp / "out" / name).read_text().splitlines()))  # noqa: E731
        return rc, {r["item"]: r for r in read("per_item.csv")}, read("disagreements.primary.csv"), \
            (self.tmp / "out" / "summary.md").read_text()

    def test_the_first_coder_is_compared_and_every_split_is_listed_with_its_direction(self):
        rows = [label("AO1", "met"), label("AO2", "not_met"), label("AO3", "not_applicable")]
        rc, items, splits, text = self.go([unit({"AO1": "met", "AO2": "met", "AO3": "not_met"})], rows)
        self.assertEqual(rc, 0)
        self.assertEqual((items["AO1"]["agree"], items["AO2"]["disagree"], items["AO3"]["na_mismatch"]), ("1", "1", "1"))
        self.assertEqual(items["AO2"]["grader_fails_people_pass"], "1")  # AO2 is undesired: met means the reply fails
        self.assertEqual(sorted((s["item"], s["kind"]) for s in splits),
                         [("AO2", "met vs not met"), ("AO3", "N/A mismatch")])
        self.assertIn("1/3 (33%)", text)

    def test_a_gate_cannot_determine_is_neither_agreement_nor_disagreement(self):
        rows = [label("KM7-G1", "not_met", unsure=True), label("AO1", "met")]
        rc, items, splits, text = self.go([unit({"KM7-G1": "cannot_determine", "AO1": "met"})], rows)
        self.assertEqual((items["KM7-G1"]["agree"], items["KM7-G1"]["disagree"], items["KM7-G1"]["grader_cannot_determine"]),
                         ("0", "0", "1"))
        self.assertEqual([s["kind"] for s in splits], ["grader cannot_determine"])
        self.assertIn("1/1 (100%)", text)
        self.assertIn("| not met (unsure) | can't tell | 1 |", text)

    def test_spec_fixed_rows_are_checked_against_the_spec_and_kept_out_of_agreement(self):
        rows = [label("AO1", "met", checkpoint="KM1"), label("AO4", "not_applicable", source="spec", checkpoint="KM1"),
                label("AO4", "not_applicable", source="spec", checkpoint="KM1", sample=2), label("AO1", "met", checkpoint="KM1", sample=2)]
        units = [unit({"AO1": "met", "AO4": "not_applicable"}, checkpoint="KM1"),
                 unit({"AO1": "met", "AO4": "met"}, checkpoint="KM1", sample=2)]
        rc, items, splits, text = self.go(units, rows)
        self.assertNotIn("AO4", items)
        self.assertEqual([(s["item"], s["kind"]) for s in splits], [("AO4", "spec says N/A")])
        self.assertIn("N/A on 1/2 (50%)", text)

    def test_second_codes_give_agreement_between_people_and_the_grader_beside_both(self):
        rows = [label("AO1", "met"), label("AO1", "not_met", role="second", who="Ben"),
                label("AO2", "not_met"), label("AO2", "not_met", role="second", who="Ben")]
        rc, items, splits, text = self.go([unit({"AO1": "met", "AO2": "not_met"})], rows)
        self.assertEqual((items["AO1"]["people_pairs"], items["AO1"]["people_agree"]), ("1", "0"))
        self.assertIn("| first coder vs second coder | 1/2 (50%) |", text)
        self.assertIn("| grader vs second coder | 1/2 (50%) |", text)
        self.assertIn("Second codes by: Ben 2.", text)
        people = list(csv.DictReader((self.tmp / "out" / "people_disagreements.csv").read_text().splitlines()))
        self.assertEqual([(p["item"], p["first"], p["second"]) for p in people], [("AO1", "Ana", "Ben")])

    def test_scores_give_the_main_line_and_the_carried_branches_together_and_apart(self):
        lapse = dict(unit({"AO1": "met"}, checkpoint="MS-LAPSE"), score=1.0)
        rc, *_ = self.go([unit({"AO1": "met"}), lapse], [label("AO1", "met")])
        self.assertEqual(rc, 0)
        self.assertIn(f"| {MODEL} | 0.75 (2) | 0.50 (1) | 1.00 (1) |", (self.tmp / "out" / "scores.md").read_text())

    def test_a_run_graded_on_raw_text_or_left_ungraded_is_refused(self):
        self.assertEqual(self.go([unit({"AO1": "met"}, read="raw")], [label("AO1", "met")])[0], 2)
        bare = dict(unit({}), verdicts=[])
        self.assertEqual(self.go([bare], [label("AO1", "met")])[0], 2)

    def test_a_label_without_a_graded_reply_or_verdict_is_refused(self):
        self.assertEqual(self.go([unit({"AO1": "met"}, sample=2)], [label("AO1", "met")])[0], 2)
        self.assertEqual(self.go([unit({"AO1": "met"})], [label("AO2", "not_met")])[0], 2)


if __name__ == "__main__":
    unittest.main()
