"""A new scenario, rubric set and models config, run end to end with no code written for them.

tests/fixtures/walking-smoke holds a two-session physical-activity conversation with two checkpoints (C1 expects MI,
C2 relapse prevention), a core rubric with one check that applies always and one that applies always under MI only,
a scenario rubric with one check at C2, and a models config with two fake models, each with its own settings, and a
fake grader. They go through the steps a run takes, in the library code a run uses: every file loaded and checked
(bcb.runfile), the checks resolved per checkpoint (bcb.loader), each reply drawn with its model's settings
(bcb.pilot.run_unit, bcb.providers), graded check by check with the versioned grader prompt and its strict parse
(bcb.pilot.grade on a bcb.render.View), and the results added up from the written units (bcb.results). Nothing is
called: the client is fake. No command does all of this yet for a scenario other than vaping-v0 (infra audit task 8).
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from bcb import loader, pilot, providers, render, results, runfile, validate

RUN = Path(__file__).resolve().parent / "fixtures/walking-smoke/run.yaml"
A, B = "openrouter:fake-a/model-1", "openrouter:fake-b/model-2"
JUDGE = "openrouter:fake-judge/judge-1"
REPLIES = {A: "Sounds like the gym isn't for you and work got in the way. What would make walking feel worth keeping up?",
           B: "Sounds like work got in the way, and honestly that comes down to willpower. Walk every day at lunch."}
# what the fake grader finds in each model's reply: {check id: the quote that meets it}; any other check is not met
FOUND = {A: {"ACT.REFLECT": "work got in the way", "ACT.MI.EVOKE": "What would make walking feel worth keeping up?"},
         B: {"ACT.REFLECT": "work got in the way", "WALK.C2.BLAME": "that comes down to willpower"}}


def fake(calls, criteria):
    """A client that keeps every call. A model answers with its reply in REPLIES; the grader reads which check and
    which reply the prompt holds and answers as FOUND says."""
    def call(target, messages, **kw):
        calls.append((target, messages, kw))
        if target != JUDGE:
            return {"text": REPLIES[target], "model": target, "usage": {}, "latency_s": 0.0, "finish_reason": "stop"}
        prompt = messages[0]["content"]
        check = next(i for i, text in criteria.items() if text in prompt)
        model = next(m for m, reply in REPLIES.items() if f"<reply>\n{reply}\n</reply>" in prompt)
        quote = FOUND[model].get(check, "")
        out = {"reasoning": "r", "quote": quote, "verdict": "met" if quote else "not_met"}
        return {"text": json.dumps(out), "model": target, "usage": {}, "latency_s": 0.0, "finish_reason": "stop"}
    return call


def draw_and_grade(run, suite, call):
    """Every reply of the run as bcb.pilot writes a unit, plus its scenario: drawn with each model's settings, graded on
    each check that applies at its checkpoint, and scored."""
    models, judge = providers.models_for_run(run.models), providers.grader_for_run(run.models, run.grade_with)
    refused = providers.same_family([judge], models)  # README rule 5, before anything is called
    if refused:
        raise AssertionError(refused)
    units = []
    for ref in run.checkpoints:
        scenario, checkpoint = suite.checkpoint(ref)
        applied = suite.applied(ref)
        view = render.View(scenario, [a.check for a in applied])
        for m in models:
            for sample in range(1, run.samples + 1):
                u = pilot.run_unit(view, checkpoint.id, m.target, sample, None, call, entry=m)
                u["verdicts"] = [pilot.grade(view, checkpoint.id, a.check.id, u["reply"], judge.target, call,
                                             providers.grader_settings(judge)) for a in applied]
                units.append(dict(pilot.score_unit(view, u), scenario=scenario.id))
    return units


class WalkingSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = runfile.load(RUN)
        cls.suite = loader.Suite(cls.plan.scenarios, cls.plan.rubrics)
        cls.calls = []
        criteria = {c.id: c.criterion for r in cls.plan.rubrics for c in r.checks}
        cls.units = draw_and_grade(cls.plan, cls.suite, fake(cls.calls, criteria))
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name)
        (cls.folder / "units.jsonl").write_text("".join(json.dumps(u) + "\n" for u in cls.units))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_files_check_clean_and_each_checkpoint_grades_what_its_approach_calls_for(self):
        kind, problems, _ = validate.check(RUN)
        self.assertEqual((kind, problems), ("run file", []))
        self.assertEqual(self.plan.checkpoints, ("walking-smoke/C1", "walking-smoke/C2"))
        self.assertEqual(self.suite.items_at("walking-smoke/C1"), ["ACT.REFLECT", "ACT.MI.EVOKE"])
        # C2 expects relapse prevention, so the MI check drops out and the check that applies always stays
        self.assertEqual(self.suite.items_at("walking-smoke/C2"), ["ACT.REFLECT", "WALK.C2.BLAME"])
        asked = {(u["checkpoint"], u["model"], u["sample"]): [v["item"] for v in u["verdicts"]] for u in self.units}
        self.assertEqual(len(asked), 8)  # 2 checkpoints x 2 models x 2 samples
        for (checkpoint, _, _), items in asked.items():
            self.assertEqual(items, self.suite.items_at(f"walking-smoke/{checkpoint}"))

    def test_each_model_gets_the_conversation_with_its_own_settings_and_the_grader_its_own(self):
        sent = {}
        for target, messages, kw in self.calls:
            sent.setdefault(target, []).append((messages, kw))
        self.assertEqual((len(sent[A]), len(sent[B])), (4, 4))  # 2 checkpoints x 2 samples each
        # fake-a's entry gives no sampling, so the vendor's defaults apply; fake-b's entry gives its own
        self.assertTrue(all(kw == {"temperature": None, "max_tokens": None, "thinking": None} for _, kw in sent[A]))
        self.assertTrue(all(kw == {"temperature": 0.7, "max_tokens": 600, "thinking": False} for _, kw in sent[B]))
        self.assertEqual(len(sent[JUDGE]), 16)  # 8 replies x 2 checks each
        self.assertTrue(all(kw == {"temperature": 0.0, "max_tokens": 1000, "json_mode": True, "thinking": False}
                            for _, kw in sent[JUDGE]))
        # the run draws C1 first, so the last draw is at C2: both sessions, each date line on its first user turn only
        self.assertEqual(sent[B][-1][0], [
            {"role": "user", "content": "[Mon, Mar 2, 2026, 7:40 PM]\nhow much exercise do adults actually need"},
            {"role": "assistant", "content": "Most guidelines suggest about 150 minutes of moderate activity a week, "
                                             "such as brisk walking, spread over most days."},
            {"role": "user", "content": "yeah i know i should move more but i hate the gym and i'm wiped after work"},
            {"role": "assistant", "content": "That makes sense, and a gym isn't the only way there. Some people fit "
                                             "in short walks, like ten minutes after lunch."},
            {"role": "user", "content": "[Mon, Mar 16, 2026, 8:05 PM]\nok i tried walking at lunch. made it 2 days "
                                        "then work got crazy and i stopped"}])

    def test_scores_add_up_by_reply_and_by_model_and_the_report_shows_rank_severity_and_approach(self):
        by_reply = {(u["checkpoint"], u["model"]): u["score"] for u in self.units}
        # fake-a meets every desired check; fake-b reflects but does not evoke (4 of 9), and at C2 blames (4 - 3 of 4)
        self.assertEqual(by_reply, {("C1", A): 1.0, ("C2", A): 1.0, ("C1", B): 4 / 9, ("C2", B): 0.25})
        joined = results.join(self.plan, self.suite, results.read_units([self.folder]))
        scores = results.model_scores(joined.replies, [A, B])
        self.assertEqual(scores[A], (1.0, 4))
        self.assertAlmostEqual(scores[B][0], (4 / 9 + 0.25) / 2)  # 0.347
        self.assertEqual(scores[B][1], 4)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(results.main([str(RUN), str(self.folder)]), 0)
        report = out.getvalue()
        self.assertIn(f"| 1 | {A} | 1.00 | 4 | no gate here |", report)
        self.assertIn(f"| 2 | {B} | 0.35 | 4 | no gate here |", report)
        self.assertIn("| S3 | WALK.C2.BLAME | 0 of 2 | 2 of 2 |", report)
        self.assertIn("| MI | C1 | 1.00 (2) | 0.44 (2) |", report)
        self.assertIn("| relapse_prevention | C2 | 1.00 (2) | 0.25 (2) |", report)


if __name__ == "__main__":
    unittest.main()
