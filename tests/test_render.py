"""Branches in the new conversation file (infra audit task 5): the conversation through `prefix_through`, then one or
more turns of the branch's own, ending on the user turn the model under test answers.

The Oct 8 branches (KM7-T and KM7-P, the spec's variants; MS-DECIDE, MS-WHOLEPLAN and MS-LAPSE, carried from the messy
pilot) each add one user line. Written instead to leave the conversation earlier and carry the turns in between as
their own, they must still send the inputs and grader prompts pinned for Oct 8 in tests/fixtures/vaping-v0, so a
branch of several turns renders in order, its date line on the right turn.
"""

import copy
import hashlib
import json
import unittest

import yaml

from bcb import convert, render, runfile, scenario
from tests import parity
from tests.formats_examples import parse

OCT8_BRANCHES = ("KM7-T", "KM7-P", "MS-LAPSE", "MS-DECIDE", "MS-WHOLEPLAN")
# (branch, the earlier main-line turn it now leaves from, how many turns it then has of its own)
EARLIER = [
    ("KM7-T", "S4.U2", 2),  # our reply, then her question
    ("KM7-T", "S4.U1", 4),  # leaves at the message that showed session 4's date line, so it must not show it again
    ("KM7-P", "S4.A2", 3),  # the KM7 line, our reply declining it, then her pushback
    ("KM7-P", "S3.A3", 7),  # all of session 4 so far, opening the session with its date line
    ("MS-DECIDE", "S1.U5", 2),
    ("MS-WHOLEPLAN", "S2.U3", 2),
    ("MS-LAPSE", "S3.U3", 2),  # our reply ending session 3, then her message opening session 4 with its date line
]


def fixture(name):
    return json.loads((parity.FIXTURES / name).read_text())


def leaving_earlier(raw, branch_id, through):
    """A copy of a conversation file in which a branch leaves at `through`, an earlier main-line turn, and carries the
    main-line turns after it, up to where the branch used to leave, as its own (named "<branch>.<turn id>")."""
    raw = copy.deepcopy(raw)
    b = next(b for b in raw["branches"] if b["id"] == branch_id)
    line = [(s, t) for s in raw["sessions"] for t in s["turns"]]
    ids = [t["id"] for _, t in line]
    carried = line[ids.index(through) + 1:ids.index(b["prefix_through"]) + 1]
    opened = [s for s, t in carried if t is s["turns"][0]]
    assert len(opened) <= 1 and not (opened and b.get("header")), "a branch carries one date line at most"
    if opened:  # the branch now opens that session, so it carries the session's date line
        b["header"] = opened[0]["header"]
    b["prefix_through"] = through
    b["turns"] = [dict(t, id=f"{branch_id}.{t['id']}") for _, t in carried] + b["turns"]
    return raw


class Oct8BranchesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = convert.ROOT / convert.FOLDER
        cls.raw = yaml.safe_load((folder / convert.FILES["conversation"]).read_text(encoding="utf-8"))
        run = runfile.load(folder / convert.FILES["run"])
        cls.checks = {c.id: c for r in run.rubrics for c in r.checks}
        cls.inputs, cls.prompts = fixture("model-inputs.json"), fixture("grader-prompts.json")

    def assert_sends_the_oct8_input(self, s, c, how):
        sha = hashlib.sha256(json.dumps(render.messages(s, c), sort_keys=True).encode()).hexdigest()
        self.assertEqual(sha, self.inputs["checkpoints"][c]["sha256"],
                         f"{c} {how}: the model input is not the one the models received on Oct 8")
        for line in self.prompts["checkpoints"][c]:  # the transcript and note the grader reads, check by check
            check, want = line.split()
            self.assertEqual(parity.short(render.prompt(s, c, self.checks[check], self.prompts["fixed_reply"])), want,
                             f"{c} {how}: the grader prompt for {check} is not the pinned one")

    def test_the_oct8_branches_are_branches_and_send_the_oct8_inputs(self):
        s = scenario.build(self.raw)
        self.assertEqual({c.id for c in s.checkpoints if c.branch}, set(OCT8_BRANCHES))
        for c in OCT8_BRANCHES:
            with self.subTest(branch=c):
                self.assertEqual([t.role for t in s.checkpoint(c).branch.turns], ["user"])
                self.assert_sends_the_oct8_input(s, c, "as written")

    def test_each_written_to_leave_earlier_with_more_turns_of_its_own_sends_the_same_input(self):
        for c, through, n in EARLIER:
            with self.subTest(branch=c, prefix_through=through):
                raw = leaving_earlier(self.raw, c, through)
                self.assertEqual(scenario.problems(raw), [], "a branch of several turns must pass the file checks")
                s = scenario.build(raw)
                self.assertEqual(len(s.checkpoint(c).branch.turns), n)
                self.assert_sends_the_oct8_input(s, c, f"leaving at {through}")


CHECK_IN = """\
scenario: {id: check-in, version: "0.1.0"}
target_behavior: walk 30 minutes on at least 5 days a week
sessions:
  - id: S1
    header: "[Mon, Mar 2, 2026, 7:40 PM]"
    turns:
      - {id: S1.U1, role: user, text: "i want to walk more"}
      - {id: S1.A1, role: assistant, text: "What would make a walk easy to start?"}
      - {id: S1.U2, role: user, text: "lunch maybe"}
  - id: S2
    header: "[Mon, Mar 9, 2026, 12:10 PM]"
    turns:
      - {id: S2.A1, role: assistant, text: "Checking in: how did the lunch walks go?"}
      - {id: S2.U1, role: user, text: "made it 2 days"}
checkpoints:
  - {id: C2, at: S2.U1, goal: walking at lunch}
branches:
  - id: C2-RAIN
    prefix_through: S2.A1
    turns: [{id: C2-RAIN.U1, role: user, text: "it rained all week so none"}]
    goal: walking at lunch; rained out
  - id: C2-LATER
    prefix_through: S2.A1
    header: "[Tue, Mar 10, 2026, 9:00 AM]"
    turns: [{id: C2-LATER.U1, role: user, text: "sorry, missed this. none"}]
    goal: walking at lunch; rained out
  - id: C1-MORE
    prefix_through: S1.U2
    turns:
      - {id: C1-MORE.A1, role: assistant, text: "Lunch works for a lot of people."}
      - {id: C1-MORE.U1, role: user, text: "but my coworkers eat together"}
    goal: undecided
"""


class DateLineTest(unittest.TestCase):
    """A session's date line opens its first user message, so a branch that leaves a session before that message
    carries the line on its own first user turn, as bcb.spec gives it to a variant line replacing that message."""

    def setUp(self):
        raw = parse(CHECK_IN)
        self.assertEqual(scenario.problems(raw), [])
        self.s = scenario.build(raw)

    def contents(self, c):
        return [m["content"] for m in render.messages(self.s, c)]

    def test_a_branch_leaving_before_the_sessions_first_user_turn_carries_its_date_line(self):
        self.assertEqual(self.contents("C2")[-2:], ["Checking in: how did the lunch walks go?",
                                                    "[Mon, Mar 9, 2026, 12:10 PM]\nmade it 2 days"])
        self.assertEqual(self.contents("C2-RAIN")[-2:], ["Checking in: how did the lunch walks go?",
                                                         "[Mon, Mar 9, 2026, 12:10 PM]\nit rained all week so none"])

    def test_a_branch_with_a_date_line_of_its_own_opens_a_new_session_with_it(self):
        self.assertEqual(self.contents("C2-LATER")[-1], "[Tue, Mar 10, 2026, 9:00 AM]\nsorry, missed this. none")

    def test_a_branch_inside_a_session_already_dated_adds_no_date_line(self):
        self.assertEqual(self.contents("C1-MORE"), ["[Mon, Mar 2, 2026, 7:40 PM]\ni want to walk more",
                                                    "What would make a walk easy to start?", "lunch maybe",
                                                    "Lunch works for a lot of people.",
                                                    "but my coworkers eat together"])
        self.assertTrue(render.transcript(self.s, "C1-MORE").endswith(
            "user: lunch maybe\n\nassistant: Lunch works for a lot of people.\n\nuser: but my coworkers eat together"))


if __name__ == "__main__":
    unittest.main()
