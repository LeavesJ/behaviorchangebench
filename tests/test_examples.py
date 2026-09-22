import unittest

from bcb import examples
from bcb.item_tests import DEFAULT_EXAMPLES, DEFAULT_SPEC
from bcb.spec import Spec

SPEC = Spec(DEFAULT_SPEC)
RAW, EXAMPLES = examples.load(DEFAULT_EXAMPLES)


class ExamplesFileTest(unittest.TestCase):
    """README rule 4, the part that needs no API: every item has examples, and each is well formed."""

    def test_every_example_is_well_formed(self):
        self.assertEqual(examples.problems(SPEC, EXAMPLES), [])

    def test_every_item_has_a_known_pass_and_a_known_fail(self):
        self.assertEqual(examples.coverage_gaps(SPEC, EXAMPLES), [])

    def test_examples_name_the_spec_version_they_were_written_against(self):
        self.assertEqual(RAW["spec"], {"id": SPEC.raw["spec"]["id"], "version": SPEC.version})


class ProblemsCatchesTest(unittest.TestCase):
    """The checks above only mean something if they reject a bad example."""

    def one(self, **kw):
        base = dict(id="t", item="KM1-C3", checkpoint="KM1", kind="known_fail", expected="met",
                    reply="Vaping harms your brain.", decisive_span="harms your brain")
        return examples.problems(SPEC, [examples.Example(**{**base, **kw})])

    def test_good_example_passes(self):
        self.assertEqual(self.one(), [])

    def test_polarity_contradiction(self):
        self.assertTrue(self.one(expected="not_met"))

    def test_item_not_graded_at_checkpoint(self):
        self.assertTrue(self.one(checkpoint="KM4"))

    def test_fact_id_in_reply(self):
        self.assertTrue(self.one(reply="Vaping harms your brain (F01).", decisive_span="harms your brain"))

    def test_persona_name_in_reply(self):
        self.assertTrue(self.one(reply="Dani, vaping harms your brain."))

    def test_span_not_in_reply(self):
        self.assertTrue(self.one(decisive_span="nicotine is poison"))

    def test_unknown_status(self):
        self.assertTrue(self.one(status="retired"))

    def test_pending_example_does_not_count_toward_coverage(self):
        pending = examples.Example("p", "KM1-C3", "KM1", "known_fail", "met", "Vaping harms your brain.",
                                   "harms your brain", status="pending_rewording")
        self.assertIn("KM1-C3 has no known_fail", examples.coverage_gaps(SPEC, [pending]))

    def test_known_na_on_an_item_that_is_always_live(self):
        self.assertTrue(self.one(kind="known_na", expected="not_applicable", na_code="NA_OTHER"))


if __name__ == "__main__":
    unittest.main()
