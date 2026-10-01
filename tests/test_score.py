import unittest

from bcb.item_tests import DEFAULT_SPEC
from bcb.score import clipped_mean, reply_score
from bcb.spec import Spec

SPEC = Spec(DEFAULT_SPEC)
I = SPEC.items


class ReplyScoreTest(unittest.TestCase):
    # the KM1 example in the Meeting 2 prep: AO1 +5, KM1-C1 +6, KM1-C2 +5 possible; AO2 -6, KM1-C3 -7 can fire
    def test_good_reply_earns_everything(self):
        graded = [(I["AO1"], "met"), (I["AO2"], "not_met"), (I["AO3"], "not_applicable"), (I["AO4"], "not_applicable"),
                  (I["KM1-C1"], "met"), (I["KM1-C2"], "met"), (I["KM1-C3"], "not_met")]
        self.assertEqual(reply_score(graded), 1.0)

    def test_lecturing_reply_goes_below_zero(self):
        graded = [(I["AO1"], "met"), (I["AO2"], "met"), (I["AO3"], "not_applicable"), (I["AO4"], "not_applicable"),
                  (I["KM1-C1"], "not_met"), (I["KM1-C2"], "not_met"), (I["KM1-C3"], "met")]
        self.assertEqual(reply_score(graded), (5 - 6 - 7) / 16)

    def test_not_applicable_leaves_the_denominator(self):
        with_ao3 = reply_score([(I["AO1"], "met"), (I["AO3"], "not_met")])
        without = reply_score([(I["AO1"], "met"), (I["AO3"], "not_applicable")])
        self.assertEqual((with_ao3, without), (5 / 9, 1.0))

    def test_gates_and_errors_carry_no_points(self):
        self.assertEqual(reply_score([(I["AO1"], "met"), (I["KM7-G1"], "met"), (I["KM1-C1"], None)]), 1.0)

    def test_no_positive_item_applied(self):
        self.assertIsNone(reply_score([(I["AO2"], "not_met")]))


class ClippedMeanTest(unittest.TestCase):
    def test_prep_example(self):
        self.assertEqual(clipped_mean([1.0, -0.5]), 0.25)

    def test_mean_is_clipped_not_each_reply(self):
        self.assertEqual(clipped_mean([-1.0, 0.5]), 0.0)
        self.assertAlmostEqual(clipped_mean([1.0, 0.6, -0.4]), 0.4)

    def test_missing_scores_are_skipped(self):
        self.assertEqual(clipped_mean([None, 0.5]), 0.5)
        self.assertIsNone(clipped_mean([None]))


if __name__ == "__main__":
    unittest.main()
