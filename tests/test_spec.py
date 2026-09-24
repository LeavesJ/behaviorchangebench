import unittest

from bcb.item_tests import DEFAULT_SPEC
from bcb.spec import FACT_ID, Spec

SPEC = Spec(DEFAULT_SPEC)
ALL = SPEC.checkpoints(include_optional=True)


class RenderTest(unittest.TestCase):
    def test_no_reference_fact_id_reaches_any_model_input(self):
        # spec conversation_schema.checks: no \bF\d\d\b in any text a model sees
        for cp in ALL:
            self.assertEqual(FACT_ID.findall(SPEC.transcript(cp)), [], cp)

    def test_persona_name_never_appears(self):
        for cp in ALL:
            self.assertNotIn("Dani", SPEC.transcript(cp), cp)

    def test_checkpoint_ends_on_the_key_moment_line(self):
        for cp in ALL:
            last = SPEC.messages(cp)[-1]
            self.assertEqual(last["role"], "user", cp)
            self.assertTrue(last["content"].endswith(SPEC.moments[cp]["user_line"]), cp)

    def test_roles_alternate(self):
        for cp in ALL:
            roles = [m["role"] for m in SPEC.messages(cp)]
            self.assertEqual(roles, ["user", "assistant"] * (len(roles) // 2) + ["user"], cp)

    def test_one_date_header_per_session_on_its_first_user_turn(self):
        msgs = SPEC.messages("KM8")
        headers = [m["content"].split("\n", 1)[0] for m in msgs if m["content"].startswith("[")
                   and not m["content"].startswith("[Draft")]
        self.assertEqual(headers, [s["when"]["header"] for s in SPEC.raw["sessions"]])

    def test_twin_replaces_the_cigarette_line(self):
        km7t = SPEC.transcript("KM7-T")
        self.assertNotIn(SPEC.moments["KM7"]["user_line"], km7t)
        self.assertEqual(SPEC.transcript("KM7").replace("smoke cigarettes", "chew nicotine gum"), km7t)

    def test_pushback_variant_sees_our_declining_reply(self):
        ids = [t.id for t in SPEC.turns("KM7-P")]
        self.assertEqual(ids[-3:], ["S4.U3", "S4.A3", "S4.U4"])

    def test_generated_text_replaces_placeholders(self):
        generated = Spec(DEFAULT_SPEC, conversation={"S1.A1": "Nicotine is addictive."})
        self.assertIn("assistant: Nicotine is addictive.", generated.transcript("KM1"))
        self.assertFalse(generated.is_draft_prefix("KM1"))
        self.assertTrue(SPEC.is_draft_prefix("KM1"))


class ItemsTest(unittest.TestCase):
    def test_every_item_is_graded_somewhere(self):
        graded = {i for cp in ALL for i in SPEC.items_at(cp)}
        self.assertEqual(graded, set(SPEC.items))

    def test_gate_is_graded_at_its_twin_as_false_positive_check(self):
        self.assertIn("KM7-G1", SPEC.items_at("KM7-T"))
        self.assertEqual(SPEC.items["KM7-G1"].type, "gate")

    def test_grader_note_carries_goal_at_checkpoint(self):
        self.assertIn("quit since Mon Feb 9", SPEC.grader_note("KM5"))


if __name__ == "__main__":
    unittest.main()
