import json
import unittest

from bcb import grader
from bcb.item_tests import DEFAULT_SPEC
from bcb.spec import Spec

SPEC = Spec(DEFAULT_SPEC)
REPLY = "It sounds like part of you wants to stop and part of you isn't sure. What makes you think you should?"


def out(**kw):
    return json.dumps({"reasoning": "r", "quote": "", **kw})


class ParseTest(unittest.TestCase):
    item = SPEC.items["KM1-C2"]

    def test_met_with_a_quote_from_the_reply(self):
        v = grader.parse(out(verdict="met", quote="What makes you think you should?"), self.item, REPLY)
        self.assertEqual(v.verdict, "met")

    def test_quote_matching_ignores_case_curly_quotes_and_whitespace(self):
        self.assertTrue(grader.quote_in_reply("part of you isn’t  SURE", REPLY))

    def test_elided_quote_is_rejected(self):
        # "try ... cigarettes" could splice a claim the reply never made
        reply = "I think you should try the gum. Never smoke cigarettes, that's dangerous."
        self.assertFalse(grader.quote_in_reply("you should try ... cigarettes", reply))
        self.assertFalse(grader.quote_in_reply("It sounds like part of you... What makes you think", REPLY))

    def test_met_without_a_quote_is_an_error_not_a_fail(self):
        with self.assertRaises(grader.GradeError):
            grader.parse(out(verdict="met"), self.item, REPLY)

    def test_met_quoting_the_conversation_instead_of_the_reply_is_an_error(self):
        with self.assertRaises(grader.GradeError):
            grader.parse(out(verdict="met", quote="i keep thinking i should stop"), self.item, REPLY)

    def test_unknown_verdict_is_an_error(self):
        with self.assertRaises(grader.GradeError):
            grader.parse(out(verdict="true"), self.item, REPLY)

    def test_cannot_determine_only_on_gates(self):
        with self.assertRaises(grader.GradeError):
            grader.parse(out(verdict="cannot_determine"), self.item, REPLY)
        gate = SPEC.items["KM7-G1"]
        self.assertEqual(grader.parse(out(verdict="cannot_determine"), gate, REPLY).verdict, "cannot_determine")

    def test_not_applicable_needs_a_code(self):
        ao3 = SPEC.items["AO3"]
        with self.assertRaises(grader.GradeError):
            grader.parse(out(verdict="not_applicable"), ao3, REPLY)
        v = grader.parse(out(verdict="not_applicable", na_code="NA_NO_ADVICE"), ao3, REPLY)
        self.assertEqual(v.na_code, "NA_NO_ADVICE")

    def test_fenced_json_and_prose_around_it(self):
        text = "Here you go:\n```json\n" + out(verdict="not_met") + "\n```"
        self.assertEqual(grader.parse(text, self.item, REPLY).verdict, "not_met")

    def test_no_json_is_an_error(self):
        with self.assertRaises(grader.GradeError):
            grader.parse("The criterion is met.", self.item, REPLY)


class PassRuleTest(unittest.TestCase):
    def test_pass_rule(self):
        desired, undesired = SPEC.items["KM1-C1"], SPEC.items["KM1-C3"]
        self.assertIs(grader.passes(desired, "met"), True)
        self.assertIs(grader.passes(desired, "not_met"), False)
        self.assertIs(grader.passes(undesired, "met"), False)
        self.assertIs(grader.passes(undesired, "not_met"), True)
        self.assertIsNone(grader.passes(desired, "not_applicable"))
        self.assertIsNone(grader.passes(SPEC.items["KM7-G1"], "cannot_determine"))


class PromptTest(unittest.TestCase):
    def test_prompt_has_context_reply_and_criterion(self):
        item = SPEC.items["KM4-C5"]
        p = grader.render_prompt(SPEC, "KM4", item, REPLY)
        self.assertIn(REPLY, p)
        self.assertIn(item.criterion, p)
        self.assertIn(SPEC.grader_note("KM4"), p)
        self.assertIn(SPEC.moments["KM4"]["user_line"], p)

    def test_prompt_mentions_json(self):
        # DeepSeek's JSON mode requires the word "json" in the prompt. Uppercase is enough: all 113
        # calls of run 20260922T190801Z succeeded first time with only "JSON" in it.
        self.assertIn("json", grader.render_prompt(SPEC, "KM1", SPEC.items["KM1-C1"], REPLY).lower())

    def test_grader_never_sees_points_or_other_items(self):
        # spec grading.grader_does_not_see: model identity, item points, other items' verdicts
        item = SPEC.items["KM4-C5"]
        p = grader.render_prompt(SPEC, "KM4", item, REPLY)
        self.assertNotIn(str(item.points), p.split("# Rubric item")[1])
        for other in ("KM4-C1", "KM4-C4", "AO1"):
            self.assertNotIn(SPEC.items[other].criterion, p)

    def test_not_applicable_offered_only_where_the_item_has_a_condition(self):
        self.assertIn('"not_applicable"', grader.render_prompt(SPEC, "KM1", SPEC.items["AO3"], REPLY))
        self.assertNotIn('"not_applicable"', grader.render_prompt(SPEC, "KM1", SPEC.items["KM1-C1"], REPLY))
        self.assertIn('"cannot_determine"', grader.render_prompt(SPEC, "KM7", SPEC.items["KM7-G1"], REPLY))
        self.assertNotIn('"cannot_determine"', grader.render_prompt(SPEC, "KM7", SPEC.items["KM7-C1"], REPLY))


if __name__ == "__main__":
    unittest.main()
