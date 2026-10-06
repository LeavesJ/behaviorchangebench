import random
import unittest

from bcb import labels


class NormalizeTest(unittest.TestCase):
    def test_strips_model_typography_and_keeps_lists(self):
        raw = "## Plan\nMy view is yes—quit. **Your reasons** matter “most”.\n- step one\n* step two \U0001F602\n3–5 days"
        out = labels.normalize(raw)
        self.assertNotIn("—", out)
        self.assertNotIn("**", out)
        self.assertNotIn("#", out)
        self.assertNotIn("\U0001F602", out)
        self.assertIn("yes, quit.", out)
        self.assertIn('"most"', out)
        self.assertIn("- step one\n- step two", out)
        self.assertIn("3-5 days", out)

    def test_name_scan_catches_a_planted_name(self):
        self.assertTrue(labels.MODEL_NAMES.search(labels.normalize("As Gemini, I think")))
        self.assertIsNone(labels.MODEL_NAMES.search(labels.normalize("I think you already know")))


class PlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec, _ = labels.load_spec()

    def test_rows_per_person_match_the_guide(self):
        rows = {p: sum(t[2] for t in tabs) for p, tabs in labels.plan_rows(self.spec).items()}
        self.assertEqual(rows, {"Vincent": 234, "Ryan": 261, "Emily": 228, "Tyler": 279, "Jarron": 246, "Extra KM8": 81})
        first = {p: labels.first_ten_rows(t) for p, t in labels.plan_rows(self.spec).items()}
        self.assertEqual([first[p] for p in ("Vincent", "Ryan", "Emily", "Tyler", "Jarron")], [71, 98, 80, 61, 82])

    def test_fixed_na_left_out_and_gate_kept_on_the_twin(self):
        self.assertNotIn("AO4", labels.rows_for(self.spec, "KM1"))
        self.assertIn("AO4", labels.rows_for(self.spec, "KM3"))
        self.assertIn("KM7-G1", labels.rows_for(self.spec, "KM7-T"))

    def test_if_unsure_answers(self):
        items = self.spec.items
        self.assertEqual(labels.if_unsure(items["AO3"]), labels.UNSURE_AO3)
        self.assertEqual(labels.if_unsure(items["KM7-G1"]), "no")
        self.assertEqual(labels.if_unsure(items["AO2"]), "no")
        self.assertEqual(labels.if_unsure(items["MS-LAPSE-C3"]), "no")

    def test_every_person_gets_unique_ids_and_the_right_replies(self):
        units = [{"checkpoint": cp, "model": m, "sample": s, "reply": "x", "folder": "f"}
                 for cp in labels.MAIN + ["MS-DECIDE", "MS-WHOLEPLAN", "MS-LAPSE"]
                 for m in ("a", "b", "c") for s in (1, 2, 3)]
        out = labels.assign(units, random.Random(0))
        for person, tabs in out.items():
            ids = [rid for _, replies in tabs for _, rid, _ in replies]
            self.assertEqual(len(ids), len(set(ids)))
            for cp, replies in tabs:
                which = dict(labels.PLAN[person])[cp]
                self.assertEqual(len(replies), 9 if which == "all" else 3)
                if which == 1:
                    self.assertEqual({u["sample"] for _, _, u in replies}, {1})


if __name__ == "__main__":
    unittest.main()
