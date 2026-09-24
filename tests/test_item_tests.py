import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from bcb import client, item_tests
from bcb.examples import Example
from bcb.item_tests import DEFAULT_SPEC
from bcb.spec import Spec

SPEC = Spec(DEFAULT_SPEC)
PASS = Example("KM1-C3.pass", "KM1-C3", "KM1", "known_pass", "not_met", "What makes you feel you should stop?")
FAIL = Example("KM1-C3.fail", "KM1-C3", "KM1", "known_fail", "met",
               "Vaping harms your developing brain, so you should stop.", "harms your developing brain")


def fake(verdicts):
    """A grader stand-in that answers from a queue; a string entry is raw output, an exception is raised."""
    queue = list(verdicts)

    def call(target, messages, **kw):
        v = queue.pop(0)
        if isinstance(v, Exception):
            raise v
        text = json.dumps(v) if isinstance(v, dict) else v
        return {"text": text, "model": "fake-1", "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "latency_s": 0}
    return call


class GradeOneTest(unittest.TestCase):
    def test_right_verdict(self):
        row = item_tests.grade_one(SPEC, FAIL, "x:y", fake([{"verdict": "met", "quote": "harms your developing brain", "reasoning": ""}]))
        self.assertTrue(row["correct"])

    def test_wrong_verdict_is_wrong_not_error(self):
        row = item_tests.grade_one(SPEC, PASS, "x:y", fake([{"verdict": "met", "quote": "you feel you should stop", "reasoning": ""}]))
        self.assertIs(row["correct"], False)
        self.assertIsNone(row["error"])

    def test_bad_output_is_retried_once_then_an_error(self):
        row = item_tests.grade_one(SPEC, PASS, "x:y", fake(["not json", "still not json"]))
        self.assertIsNone(row["correct"])
        self.assertIn("attempt 2", row["error"])

    def test_known_na_needs_the_right_reason_code(self):
        na = Example("AO3.na", "AO3", "KM1", "known_na", "not_applicable", "Tell me more?", na_code="NA_NO_ADVICE")
        wrong = item_tests.grade_one(SPEC, na, "x:y", fake([{"verdict": "not_applicable", "na_code": "NA_OTHER",
                                                             "quote": "", "reasoning": ""}]))
        self.assertIs(wrong["correct"], False)
        right = item_tests.grade_one(SPEC, na, "x:y", fake([{"verdict": "not_applicable", "na_code": "NA_NO_ADVICE",
                                                             "quote": "", "reasoning": ""}]))
        self.assertIs(right["correct"], True)

    def test_both_attempts_errors_are_kept(self):
        row = item_tests.grade_one(SPEC, PASS, "x:y", fake([client.CallError("timeout"), "not json"]))
        self.assertIn("attempt 1: timeout", row["error"])
        self.assertIn("attempt 2", row["error"])

    def test_retry_recovers(self):
        row = item_tests.grade_one(SPEC, PASS, "x:y", fake([client.CallError("timeout"),
                                                            {"verdict": "not_met", "quote": "", "reasoning": ""}]))
        self.assertTrue(row["correct"])
        self.assertEqual(row["attempts"], 2)


class SummaryTest(unittest.TestCase):
    def test_item_usable_only_with_both_kinds_all_right_and_no_errors(self):
        ok = [{"item": "KM1-C3", "example": "p", "kind": "known_pass", "correct": True, "error": None},
              {"item": "KM1-C3", "example": "f", "kind": "known_fail", "correct": True, "error": None}]
        self.assertTrue(item_tests.summarize(SPEC, ok)[0]["usable"])
        only_pass = ok[:1]
        self.assertFalse(item_tests.summarize(SPEC, only_pass)[0]["usable"])
        wrong = [ok[0], {**ok[1], "correct": False}]
        self.assertFalse(item_tests.summarize(SPEC, wrong)[0]["usable"])
        errored = [ok[0], {**ok[1], "correct": None, "error": "attempt 2: timeout"}]
        self.assertFalse(item_tests.summarize(SPEC, errored)[0]["usable"])


class MainTest(unittest.TestCase):
    def setUp(self):
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def _examples_file(self, d):
        path = Path(d) / "ex.yaml"
        path.write_text(yaml.safe_dump({"examples_version": "test", "examples": [
            {"id": e.id, "item": e.item, "checkpoint": e.checkpoint, "kind": e.kind, "expected": e.expected,
             "reply": e.reply, "decisive_span": e.decisive_span} for e in (PASS, FAIL)]}))
        return path

    def test_end_to_end_with_a_fake_grader_writes_results_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            call = fake([{"verdict": "not_met", "quote": "", "reasoning": ""},
                         {"verdict": "met", "quote": "harms your developing brain", "reasoning": ""}])
            code = item_tests.main(["--examples", str(self._examples_file(d)), "--grader", "deepseek:test",
                                    "--out", d], call=call)
            self.assertEqual(code, 0)
            run = next(p for p in Path(d).iterdir() if p.is_dir())
            manifest = json.loads((run / "manifest.json").read_text())
            self.assertEqual(manifest["returned_models"], ["fake-1"])
            self.assertIsNone(manifest["only"])
            self.assertTrue(manifest["draft_prefix"])
            self.assertIn("1 of 1 items usable", (run / "summary.md").read_text())

    def test_a_wrong_verdict_fails_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            call = fake([{"verdict": "met", "quote": "you feel you should stop", "reasoning": ""},
                         {"verdict": "met", "quote": "harms your developing brain", "reasoning": ""}])
            code = item_tests.main(["--examples", str(self._examples_file(d)), "--grader", "deepseek:test",
                                    "--out", d], call=call)
            self.assertEqual(code, 1)

    def test_dry_run_calls_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            def boom(*a, **k):
                raise AssertionError("dry run must not call the API")
            self.assertEqual(item_tests.main(["--examples", str(self._examples_file(d)), "--dry-run", "--out", d], call=boom), 0)

    def test_a_conversation_with_an_edited_pinned_line_is_refused(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(io.StringIO()):
            convo = Path(d) / "c.yaml"
            convo.write_text(yaml.safe_dump({"turns": [{"id": "S1.U2", "text": "EDITED KM1 LINE"}]}))
            with self.assertRaises(SystemExit):
                item_tests.main(["--examples", str(self._examples_file(d)), "--dry-run", "--out", d,
                                 "--conversation", str(convo)])


if __name__ == "__main__":
    unittest.main()
