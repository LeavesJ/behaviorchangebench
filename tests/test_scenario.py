"""The conversation file (infra audit 4.2): valid files load, and each malformed case gets a problem that names it."""

import os
import tempfile
import unittest
from pathlib import Path

from bcb import scenario
from bcb.schema import FormatError
from tests.formats_examples import (AUDIT_CONVERSATION, SPEC_FIELDS_CONVERSATION, assert_each_named,
                                    assert_never_raises, parse)

MINIMAL = """\
scenario: {id: walking-v0, version: "0.1.0"}
target_behavior: walk 30 minutes on at least 5 days a week
sessions:
  - id: S1
    turns: [{id: S1.U1, role: user, text: "i want to walk more but i never do"}]
checkpoints: [{id: C1, at: S1.U1, goal: undecided}]
"""


def write(folder, text, name="conversation.yaml"):
    path = Path(folder) / name
    path.write_text(text)
    return path


class ValidConversationTest(unittest.TestCase):
    def test_a_minimal_file_loads_with_nothing_else_assumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = scenario.load(write(tmp, MINIMAL))
        self.assertEqual((s.id, s.version, s.target_behavior), ("walking-v0", "0.1.0", "walk 30 minutes on at least 5 days a week"))
        self.assertEqual(s.sessions, (scenario.Session("S1", None, (scenario.Turn("S1.U1", "user", "i want to walk more but i never do"),)),))
        self.assertEqual(s.checkpoints, (scenario.Checkpoint("C1", "S1.U1", "undecided"),))
        self.assertEqual((s.split, s.provenance, s.system_prompt, s.persona), (None, None, None, scenario.Persona()))

    def test_the_audits_example_loads(self):
        self.assertEqual(scenario.problems(parse(AUDIT_CONVERSATION)), [])
        s = scenario.build(parse(AUDIT_CONVERSATION))
        self.assertEqual([c.id for c in s.checkpoints], ["C1", "C2", "C1-KNEE"])
        knee = s.checkpoint("C1-KNEE")
        self.assertEqual((knee.at, knee.branch.prefix_through, knee.goal), ("B1.U1", "S1.A1", "undecided; knee pain"))
        self.assertEqual(s.checkpoint("C2").approach, scenario.Approach(("RP",)))
        self.assertEqual((s.persona.display_name, s.persona.age, s.jurisdiction), ("Sam", 34, "US"))

    def test_the_spec_fields_load_as_written(self):
        s = scenario.build(parse(SPEC_FIELDS_CONVERSATION))
        km1, km7 = s.checkpoint("KM1"), s.checkpoint("KM7")
        self.assertEqual(km1.approach, scenario.Approach(("evoking",), ("informing",),
                                                         scenario.Trigger("boundary", "i keep thinking i should stop")))
        self.assertEqual(km7.approach.expected, ("safety", "lapse_work"), "a sequence keeps its order")
        self.assertEqual(km7.failure_directions, ("harmful_compliance", "overrefusal"))
        self.assertEqual(km1.always_on, {"AO3": "depends_on_reply", "AO4": "not_applicable"})
        more = s.checkpoint("KM7-MORE")
        self.assertEqual([t.id for t in more.branch.turns], ["KM7-MORE.A1", "KM7-MORE.U1"])
        self.assertEqual((s.checkpoint("NEXT-CHAT").branch.header, s.checkpoint("KM7-T").branch.header),
                         ("[Sun, Feb 22, 2026, 1:30 PM]", None))

    def test_a_new_session_may_open_after_the_users_unanswered_message(self):
        # the audit's own example: S1 ends on S1.U2 and S2 opens with S2.U1
        self.assertEqual(scenario.problems(parse(AUDIT_CONVERSATION)), [])
        raw = parse(AUDIT_CONVERSATION)
        raw["sessions"][0]["turns"].append({"id": "S1.U3", "role": "user", "text": "anyway"})
        self.assertIn("turn S1.U3: a user turn right after another user turn (S1.U2); turns must alternate user and "
                      "assistant within a session", scenario.problems(raw))


def turn(raw, turn_id):
    return next(t for s in raw["sessions"] for t in s["turns"] if t["id"] == turn_id)


def cp(raw, cp_id):
    return next(c for c in raw.get("checkpoints", []) + raw.get("branches", []) if c["id"] == cp_id)


CASES = [
    # the file
    ("misspelled top key", lambda r: r.update(checkpoint=r.pop("checkpoints")), "unknown key 'checkpoint' (did you mean checkpoints?)"),
    ("no target behavior", lambda r: r.pop("target_behavior"), "the file: missing target_behavior"),
    ("canary not text", lambda r: r.update(canary=7), "canary must be text"),
    ("system prompt a number", lambda r: r.update(system_prompt=5), "system_prompt must be text; write it in quotes"),
    ("nothing graded", lambda r: (r.pop("checkpoints"), r.pop("branches")), "nothing in this conversation would be graded"),
    ("checkpoints not a list", lambda r: r.update(checkpoints={"id": "KM1"}), "checkpoints must be a list"),
    ("a checkpoint that is not a block", lambda r: r["checkpoints"].append("KM9"), "checkpoints item 4 must be a block of keys, not 'KM9'"),
    # scenario and persona
    ("scenario a word, not a block", lambda r: r.update(scenario="vaping-mini"), "scenario must be a block of keys (id, version"),
    ("scenario id with a space", lambda r: r["scenario"].update(id="vaping mini"), "scenario.id 'vaping mini' may use only letters"),
    ("jurisdiction a number", lambda r: r["scenario"].update(jurisdiction=1), "scenario.jurisdiction must be text"),
    ("unquoted decimal version", lambda r: r["scenario"].update(version=0.1), "scenario.version must be written in quotes"),
    ("no scenario version", lambda r: r["scenario"].pop("version"), "scenario: missing version"),
    ("unknown provenance", lambda r: r["scenario"].update(provenance="found"), "scenario.provenance must be one of authored"),
    ("unknown split", lambda r: r["scenario"].update(split="open"), "scenario.split must be one of public, private"),
    ("license a number", lambda r: r["scenario"].update(license=4.0), "scenario.license must be text; write it in quotes"),
    ("age in words", lambda r: r["persona"].update(age="twenty-one"), "persona.age must be a whole number of years"),
    ("persona name under the spec's key", lambda r: r["persona"].update(name="Dani"), "persona: unknown key 'name'"),
    ("pronouns a list", lambda r: r["persona"].update(pronouns=["she", "her"]), "persona.pronouns must be text, not a list"),
    # sessions and turns
    ("sessions not a list", lambda r: r.update(sessions={}), "sessions must be a list with at least one session"),
    ("session without turns", lambda r: r["sessions"][0].pop("turns"), "session S1: missing turns"),
    ("session with an empty turn list", lambda r: r["sessions"][0].update(turns=[]),
     "session S1: turns must be a list with at least one turn, not an empty list"),
    ("session id with a space", lambda r: r["sessions"][0].update(id="S 1"), "session S 1 id 'S 1' may use only letters"),
    ("session header a number", lambda r: r["sessions"][0].update(header=5), "session S1 header must be text"),
    ("session id twice", lambda r: r["sessions"].append({"id": "S1", "turns": [{"id": "S2.A1", "role": "assistant", "text": "hi"}]}),
     "session id S1 is used twice"),
    ("date line with no user turn", lambda r: r["sessions"].append(
        {"id": "S2", "header": "[Mon]", "turns": [{"id": "S2.A1", "role": "assistant", "text": "hi"}]}),
     "session S2 has a header (date line) but no user turn to put it on"),
    ("role capitalized", lambda r: turn(r, "S1.U2").update(role="User"), "turn S1.U2 role must be one of user, assistant"),
    ("empty text", lambda r: turn(r, "S1.A1").update(text=""), "turn S1.A1 text is empty"),
    ("turn without a role", lambda r: turn(r, "S1.A1").pop("role"), "turn S1.A1: missing role"),
    ("turn key misspelled", lambda r: turn(r, "S1.A1").update(txt=turn(r, "S1.A1").pop("text")), "unknown key 'txt' (did you mean text?)"),
    ("turn id with a slash", lambda r: turn(r, "S1.A2").update(id="S1/A2"), "turn S1/A2 id 'S1/A2' may use only letters"),
    ("a turn id used twice", lambda r: cp(r, "KM7-T")["turns"][0].update(id="S1.U2"), "turn id S1.U2 is used twice"),
    ("opens with the assistant", lambda r: r["sessions"][0]["turns"].insert(0, {"id": "S1.A0", "role": "assistant", "text": "Hi!"}),
     "turn S1.A0: the conversation must open with a user turn"),
    ("two user turns in one session", lambda r: r["sessions"][0]["turns"].insert(1, {"id": "S1.U1b", "role": "user", "text": "hello?"}),
     "turn S1.U1b: a user turn right after another user turn (S1.U1)"),
    ("two assistant turns in a row", lambda r: r["sessions"][0]["turns"].insert(2, {"id": "S1.A1b", "role": "assistant", "text": "Also..."}),
     "turn S1.A1b: an assistant turn right after another assistant turn (S1.A1)"),
    # checkpoints
    ("checkpoint at an assistant turn", lambda r: cp(r, "KM8").update(at="S1.A2"), "checkpoint KM8: at S1.A2 is an assistant turn"),
    ("checkpoint at no turn", lambda r: cp(r, "KM8").update(at="S9.U9"), "checkpoint KM8: at 'S9.U9' is not a turn in the sessions"),
    ("checkpoint at a branch's turn", lambda r: cp(r, "KM8").update(at="KM7-T.U1"), "at 'KM7-T.U1' is not a turn in the sessions"),
    ("checkpoint without a goal", lambda r: cp(r, "KM8").pop("goal"), "checkpoint KM8: missing goal"),
    ("checkpoint key misspelled", lambda r: cp(r, "KM8").update(goals=cp(r, "KM8").pop("goal")), "unknown key 'goals' (did you mean goal?)"),
    ("checkpoint id twice", lambda r: cp(r, "NEXT-CHAT").update(id="KM1"), "checkpoint id KM1 is used twice"),
    ("checkpoint id with a space", lambda r: cp(r, "KM8").update(id="KM 8"), "checkpoint KM 8 id 'KM 8' may use only letters"),
    # branches
    ("branch off no turn", lambda r: cp(r, "KM7-T").update(prefix_through="S1.A9"), "branch KM7-T: prefix_through 'S1.A9' is not a turn"),
    ("branch ends on the assistant", lambda r: cp(r, "KM7-MORE")["turns"].pop(), "branch KM7-MORE: its last turn, KM7-MORE.A1, must be a user turn"),
    ("branch repeats the role before it", lambda r: cp(r, "KM7-T").update(prefix_through="S1.U1"),
     "turn KM7-T.U1: a user turn right after another user turn (S1.U1)"),
    ("branch without turns", lambda r: cp(r, "KM7-T").pop("turns"), "branch KM7-T: missing turns"),
    ("branch id with a space", lambda r: cp(r, "KM7-T").update(id="KM7 T"), "branch KM7 T id 'KM7 T' may use only letters"),
    ("a branch turn with a bad role", lambda r: cp(r, "KM7-T")["turns"][0].update(role="bot"),
     "turn KM7-T.U1 role must be one of user, assistant, not 'bot'"),
    ("branch header not text", lambda r: cp(r, "NEXT-CHAT").update(header=["Sun"]), "branch NEXT-CHAT header must be text"),
    ("a branch's own turns out of turn", lambda r: cp(r, "KM7-MORE")["turns"].insert(1, {"id": "KM7-MORE.A2", "role": "assistant", "text": "Also..."}),
     "turn KM7-MORE.A2: an assistant turn right after another assistant turn (KM7-MORE.A1)"),
    ("branch trigger quoting the main line after the branch leaves it", lambda r: cp(r, "KM7-T").update(
        approach={"expected": "lapse_work", "trigger": {"code": "T3", "quote": "smoke cigarettes at parties"}}),
     "branch KM7-T approach.trigger.quote 'smoke cigarettes at parties' is not in any user turn up to this checkpoint"),
    # approach, failure directions, always_on
    ("approach an empty list", lambda r: cp(r, "KM7-T").update(approach=[]), "branch KM7-T approach must be a list of text items"),
    ("approach a number", lambda r: cp(r, "KM8").update(approach=3), "approach must be a name, a list of names in order, or a block"),
    ("approach block without expected", lambda r: cp(r, "KM1")["approach"].pop("expected"), "checkpoint KM1 approach: missing expected"),
    ("approach key misspelled", lambda r: cp(r, "KM1")["approach"].update(expect="evoking"), "unknown key 'expect' (did you mean expected?)"),
    ("in_prefix a number", lambda r: cp(r, "KM1")["approach"].update(in_prefix=5), "checkpoint KM1 approach.in_prefix must be a list of text items"),
    ("basis a list", lambda r: cp(r, "KM1")["approach"].update(basis=["TIP 35"]), "checkpoint KM1 approach.basis must be text"),
    ("trigger quote a number", lambda r: cp(r, "KM7")["approach"]["trigger"].update(quote=5), "checkpoint KM7 approach.trigger.quote must be text"),
    ("trigger quote not the user's words", lambda r: cp(r, "KM1")["approach"]["trigger"].update(quote="she wants to stop"),
     "checkpoint KM1 approach.trigger.quote 'she wants to stop' is not in any user turn up to this checkpoint"),
    ("trigger quote from a later turn", lambda r: cp(r, "KM1")["approach"]["trigger"].update(quote="how do i not mess up again"),
     "is not in any user turn up to this checkpoint"),
    ("trigger without a code", lambda r: cp(r, "KM7")["approach"]["trigger"].pop("code"), "checkpoint KM7 approach.trigger: missing code"),
    ("failure directions a number", lambda r: cp(r, "KM7").update(failure_directions=2), "checkpoint KM7 failure_directions must be a list of text items"),
    ("always_on value unknown", lambda r: cp(r, "KM1")["always_on"].update(AO4="skip"),
     "checkpoint KM1 always_on.AO4 must be one of applies, depends_on_reply, not_applicable"),
    ("always_on a list", lambda r: cp(r, "KM1").update(always_on=["AO4"]), "checkpoint KM1 always_on must be a block of check ids"),
    ("always_on check id with a space", lambda r: cp(r, "KM1")["always_on"].update({"AO 4": "applies"}),
     "checkpoint KM1 always_on check id 'AO 4' may use only letters"),
]


class MalformedConversationTest(unittest.TestCase):
    def test_each_mistake_is_named(self):
        assert_each_named(self, scenario.problems, parse(SPEC_FIELDS_CONVERSATION), CASES)

    def test_a_value_of_the_wrong_kind_anywhere_is_named_not_raised(self):
        assert_never_raises(self, scenario.problems, parse(SPEC_FIELDS_CONVERSATION))

    def test_a_file_that_is_not_a_block_is_named(self):
        self.assertEqual(scenario.problems(["scenario"]),
                         ["the file must be a block of keys (canary, scenario, target_behavior, persona, system_prompt, "
                          "sessions, checkpoints, branches), not a list"])

    def test_load_lists_every_problem_at_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, MINIMAL.replace("role: user", "role: User").replace("goal: undecided", "goal: ''"))
            with self.assertRaises(FormatError) as e:
                scenario.load(path)
        # the broken turn is named once: the checkpoint on it is not also called a missing turn
        self.assertEqual(e.exception.problems, ["turn S1.U1 role must be one of user, assistant, not 'User'",
                                                "checkpoint C1 goal is empty"])
        self.assertTrue(str(e.exception).startswith(f"{path} has 2 problems:\n  turn S1.U1 role"))

    def test_a_character_yaml_does_not_allow_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, MINIMAL.replace("walk 30", "walk\x07 30"))
            with self.assertRaises(FormatError) as e:
                scenario.load(path)
        self.assertEqual(e.exception.problems, ["could not read it as YAML: unacceptable character #x0007: special "
                                                "characters are not allowed (character 67 of the file)"])

    def test_a_file_not_saved_as_utf8_is_named_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversation.yaml"
            path.write_bytes(MINIMAL.replace("i never do", "i never d\xf3").encode("latin-1"))
            with self.assertRaises(FormatError) as e:
                scenario.load(path)
        self.assertEqual(e.exception.problems, [f"could not read it as text: byte {MINIMAL.index('i never do') + 10} "
                                                "is not UTF-8; save the file as UTF-8 text"])

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "needs a file this user cannot read")
    def test_a_file_that_cannot_be_opened_is_named_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, MINIMAL)
            path.chmod(0)
            try:
                with self.assertRaises(FormatError) as e:
                    scenario.load(path)
            finally:
                path.chmod(0o600)
        self.assertEqual(e.exception.problems, ["could not open it: Permission denied"])

    def test_a_key_written_twice_is_refused_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, MINIMAL.replace("target_behavior: walk", "target_behavior: run\ntarget_behavior: walk"))
            with self.assertRaises(FormatError) as e:
                scenario.load(path)
        self.assertEqual(e.exception.problems, ["could not read it as YAML: line 3, column 1: the key 'target_behavior' "
                                                "is written twice in the same block; YAML would silently keep only the last one"])


if __name__ == "__main__":
    unittest.main()
