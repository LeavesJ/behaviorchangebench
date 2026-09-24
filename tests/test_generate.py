import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from bcb import client, generate, item_tests
from bcb.item_tests import DEFAULT_SPEC
from bcb.spec import Spec, load_conversation
from tests.test_checks import clean

SPEC = Spec(DEFAULT_SPEC)
CLEAN = {t["id"]: t for t in clean()}
BANNED = re.compile(r"\b(eval\w*|benchmark\w*|rubrics?|checkpoints?|key moments?|test\w*)\b|\bKM\d", re.I)


def canned(sid, **override):
    """What a well-behaved generator returns for a session: exactly the unpinned turns."""
    turns = [{"id": i, "text": CLEAN[i]["text"], "facts": []} for i in generate.to_write(SPEC, sid)]
    return {"turns": turns, **override}


def fake(outputs):
    """A generator stand-in answering from a queue; a dict is sent as JSON, a string as is, an exception is raised."""
    queue, seen = list(outputs), []

    def call(target, messages, **kw):
        seen.append(messages[0]["content"])
        v = queue.pop(0)
        if isinstance(v, Exception):
            raise v
        return {"text": json.dumps(v) if isinstance(v, dict) else v, "model": "fake-gen-1",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "latency_s": 0}
    call.seen = seen
    return call


def quietly(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


def run_dir(d):
    return next(p for p in Path(d).iterdir() if p.is_dir())


class ParseTest(unittest.TestCase):
    ids = generate.to_write(SPEC, "S1")

    def test_exact_turns_parse(self):
        out = generate.parse(json.dumps(canned("S1")), self.ids)
        self.assertEqual(set(out), set(self.ids))

    def test_missing_wrong_extra_and_duplicate_turns_are_errors(self):
        good = canned("S1")
        missing = {"turns": good["turns"][1:]}
        pinned = {"turns": good["turns"] + [{"id": "S1.U2", "text": "rewritten", "facts": []}]}
        wrong = {"turns": [{**good["turns"][0], "id": "S1.A9"}] + good["turns"][1:]}
        twice = {"turns": good["turns"] + good["turns"][:1]}
        for bad in (missing, pinned, wrong, twice):
            with self.assertRaises(generate.GenerateError):
                generate.parse(json.dumps(bad), self.ids)

    def test_extra_keys_and_bad_shapes_are_errors(self):
        extra_top = canned("S1", notes="hi")
        extra_turn = canned("S1")
        extra_turn["turns"][0]["label"] = "information"
        empty = canned("S1")
        empty["turns"][0]["text"] = " "
        list_id = canned("S1")
        list_id["turns"][0]["id"] = ["S1.U1"]
        for bad in (json.dumps(extra_top), json.dumps(extra_turn), json.dumps(empty), "not json", "[]", json.dumps(list_id)):
            with self.assertRaises(generate.GenerateError):
                generate.parse(bad, self.ids)

    def test_facts_only_from_the_turns_own_beat_and_none_on_person_turns(self):
        ids = generate.to_write(SPEC, "S2")
        for tid, facts in (("S2.A3", ["F05", "F02"]), ("S2.U2", ["F09"]), ("S2.A1", ["F09"])):
            out = canned("S2")
            next(t for t in out["turns"] if t["id"] == tid)["facts"] = facts
            with self.assertRaises(generate.GenerateError, msg=tid):
                generate.parse(json.dumps(out), ids, SPEC)
        ok = canned("S2")
        next(t for t in ok["turns"] if t["id"] == "S2.A1")["facts"] = ["F11", "F13"]
        self.assertEqual(generate.parse(json.dumps(ok), ids, SPEC)["S2.A1"]["facts"], ["F11", "F13"])


class FamilyTest(unittest.TestCase):
    def test_family(self):
        self.assertEqual(generate.family("deepseek:deepseek-flash"), "deepseek")
        self.assertEqual(generate.family("openrouter:deepseek/deepseek-chat"), "deepseek")
        self.assertEqual(generate.family("openrouter:openai/gpt-x"), "openai")

    def test_same_family_as_the_grader_is_refused(self):
        for target in ("deepseek:deepseek-chat", "openrouter:deepseek/deepseek-chat", "openrouter:DeepSeek/x"):
            def boom(*a, **k):
                raise AssertionError("a refused generator must not be called")
            with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit):
                quietly(generate.main, ["--generator", target, "--out", d], call=boom)

    def test_routers_derivatives_and_every_grader_are_refused(self):
        def boom(*a, **k):
            raise AssertionError("a refused generator must not be called")
        for argv in (["--generator", "openrouter:openrouter/auto"],  # could route to the grader
                     ["--generator", "openrouter:tngtech/deepseek-r1t-chimera"],  # a grader-family derivative
                     ["--generator", "openrouter:qwen/qwen3", "--grader", "deepseek:deepseek-flash",
                      "--grader", "openrouter:qwen/qwen3-other"]):  # the second grader's family
            with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit, msg=argv):
                quietly(generate.main, argv + ["--out", d], call=boom)

    def test_a_returned_model_in_a_grader_family_fails_the_session(self):
        def served_by_deepseek(target, messages, **kw):
            return {"text": json.dumps(canned("S1")), "model": "deepseek/deepseek-chat", "usage": {}, "latency_s": 0}
        with tempfile.TemporaryDirectory() as d:
            code = quietly(generate.main, ["--generator", "openrouter:openai/fake", "--sessions", "S1", "--out", d],
                           call=served_by_deepseek)
            record = json.loads((run_dir(d) / "generation-record.json").read_text())
            self.assertEqual(code, 2)
            self.assertIn("deepseek", record["calls"][-1]["error"])

    def test_record_says_unknown_and_only_what_was_sent(self):
        with tempfile.TemporaryDirectory() as d:
            quietly(generate.main, ["--generator", "openrouter:qwen/qwen3", "--sessions", "S1", "--out", d],
                    call=fake([canned("S1")]))
            record = json.loads((run_dir(d) / "generation-record.json").read_text())
            self.assertEqual(record["generator"]["family_under_test"], "unknown")  # M4's open-weight family is not chosen
            self.assertNotIn("thinking", record["sampling"])  # client.chat sends it to deepseek only


class PromptTest(unittest.TestCase):
    def test_no_persona_name_no_banned_words_no_moment_ids(self):
        full = {t["id"]: t["text"] for t in clean()}
        for texts in ({}, full):
            for sid in ("S1", "S2", "S3", "S4"):
                prompt = generate.render_prompt(SPEC, sid, texts)
                self.assertIsNone(re.search(r"\b(dani|reyes)\b", prompt, re.I), sid)
                self.assertIsNone(BANNED.search(prompt), f"{sid}: {BANNED.search(prompt)}")

    def test_earlier_sessions_are_frozen_text_with_date_lines(self):
        prompt = generate.render_prompt(SPEC, "S3", {"S1.A1": "Nicotine is addictive, first answer.",
                                                     "S2.A5": "Plan: Monday, gum, roommate, texts."})
        self.assertIn("user: [Sat, Jan 31, 2026, 11:48 PM]\nis vaping", prompt)
        self.assertIn("assistant: Nicotine is addictive, first answer.", prompt)
        self.assertIn("user: [Sat, Feb 7, 2026, 3:15 PM]\n" + SPEC.pinned_lines()["S2.U1"], prompt)
        self.assertNotIn("[Sun, Feb 22", prompt)  # later sessions are never shown

    def test_pinned_lines_shown_as_fixed_and_only_unpinned_ids_requested(self):
        prompt = generate.render_prompt(SPEC, "S2", {})
        self.assertIn("S2.U4 (the person) FIXED", prompt)
        self.assertIn(SPEC.pinned_lines()["S2.U4"], prompt)
        self.assertIn("with exactly these turns: S2.A1, S2.U2, S2.A2, S2.U3, S2.A3, S2.A4, S2.U5, S2.A5.", prompt)

    def test_beats_lose_fact_ids_but_the_cited_facts_are_given(self):
        prompt = generate.render_prompt(SPEC, "S4", {})
        a3 = prompt.split("S4.A3 (the assistant)")[1].split("S4.U4")[0]
        self.assertIn("while chewing) or", a3)  # "(... while chewing; F25)" loses only the ID
        self.assertIn("F25: US nicotine gum label", a3)
        self.assertIsNone(re.search(r"What this turn does:[^\n]*\bF\d\d", prompt))

    def test_rules_scoped_by_position(self):
        rules = generate.render_prompt(SPEC, "S4", {}).split("# How the assistant writes")[1].split("# How the person")[0]
        self.assertIn("(S4.A1, S4.A2 only) No comparison of cigarette and vape harm", rules)
        self.assertIn("- No concrete if-then plan for being offered a vape", rules)  # every S4 assistant turn
        self.assertIn("No statement that NRT is lower-risk than vaping",
                      generate.render_prompt(SPEC, "S2", {}).split("# How the assistant writes")[1])

    def test_rules_stated_once_without_double_negatives(self):
        for sid in ("S1", "S2", "S3", "S4"):
            prompt = generate.render_prompt(SPEC, sid, {})
            for line in re.findall(r"Must not: .*", prompt):
                self.assertNotIn("; no ", line, sid)
                self.assertNotIn("allowed", line, sid)
                self.assertFalse(line.startswith("Must not: no "), sid)
            self.assertNotIn("No no ", prompt)
            self.assertEqual(prompt.count("No comparison of cigarette and vape harm"), 1, sid)
            if sid != "S2":  # the one turn the KM7 rule allows is only named where the generator can see it
                self.assertIsNone(re.search(r"\bS\d\.A\d's", prompt), sid)
        self.assertIn("The only cigarette reference allowed is in S2.A4", generate.render_prompt(SPEC, "S2", {}))

    def test_fact_clauses_a_rule_forbids_are_held_back(self):
        def block(sid, tid):
            return generate.render_prompt(SPEC, sid, {}).split(f"{tid} (the assistant)")[1].split("\n\n")[0]
        s1a1 = block("S1", "S1.A1")
        for gone in ("lowest risk", "burned", "sit in the middle", "fewer harmful chemicals than cigarette smoke"):
            self.assertNotIn(gone, s1a1)
        self.assertIn("nicotine can harm brain development", s1a1)
        self.assertIn("F25: US nicotine gum label", block("S2", "S2.A4"))
        self.assertNotIn("do not eat or drink", block("S2", "S2.A4"))
        self.assertNotIn("lapse-management", block("S4", "S4.A1"))
        self.assertIn("a slip is a temporary setback", block("S4", "S4.A1"))
        self.assertNotIn("cigarettes per day", block("S3", "S3.A2"))
        self.assertIn("F11: text-message program RCT, 2,588 e-cigarette users", block("S2", "S2.A1"))  # e-cigarettes are vapes
        self.assertIn("do not eat or drink", block("S4", "S4.A3"))  # after KM7 the rule is over

    def test_only_the_hidden_facts_this_session_reveals(self):
        hidden = {h["id"]: h["fact"] for h in SPEC.raw["persona"]["hidden_facts"]}
        s1 = generate.render_prompt(SPEC, "S1", {})
        self.assertIn(hidden["H1"], s1)
        for h in ("H2", "H3", "H4", "H5", "H6"):
            self.assertNotIn(hidden[h], s1, h)
        for sid in ("S2", "S3", "S4"):
            self.assertNotIn(hidden["H6"], generate.render_prompt(SPEC, sid, {}))

    def test_a_person_turn_may_answer_the_question_but_not_lean_on_its_wording(self):
        self.assertIn("must not quote or depend on the assistant's exact wording", generate.render_prompt(SPEC, "S1", {}))

    def test_an_edited_pinned_line_in_a_conversation_file_never_reaches_the_prompt(self):
        texts = {t["id"]: t["text"] for t in clean()}
        texts["S1.U2"] = "EDITED KM1 LINE"
        prompt = generate.render_prompt(SPEC, "S2", texts)
        self.assertNotIn("EDITED KM1 LINE", prompt)
        self.assertIn(SPEC.pinned_lines()["S1.U2"], prompt)


class MainTest(unittest.TestCase):
    def test_dry_run_calls_nothing_and_writes_s1_to_s4(self):
        def boom(*a, **k):
            raise AssertionError("dry run must not call the API")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(quietly(generate.main, ["--dry-run", "--out", d], call=boom), 0)
            self.assertEqual(sorted(p.name for p in (run_dir(d) / "prompts").iterdir()),
                             ["S1.txt", "S2.txt", "S3.txt", "S4.txt"])

    def test_full_run_with_a_fake_generator(self):
        call = fake([canned(s) for s in ("S1", "S2", "S3", "S4")])
        with tempfile.TemporaryDirectory() as d:
            code = quietly(generate.main, ["--generator", "openrouter:openai/fake", "--out", d], call=call)
            run = run_dir(d)
            self.assertEqual(code, 0, (run / "checks.md").read_text())
            self.assertEqual(len(call.seen), 4)  # S5 is pinned only: no call
            convo = load_conversation(run / "conversation.v1.0-draft.yaml")
            for tid, line in SPEC.pinned_lines().items():
                self.assertEqual(convo[tid], line)  # byte for byte through the YAML round trip
            turns = yaml.safe_load((run / "conversation.v1.0-draft.yaml").read_text())["turns"]
            self.assertEqual([t["id"] for t in turns], [t["id"] for t in clean()])
            self.assertEqual({t["source"] for t in turns if t["id"] in SPEC.pinned_lines()}, {"pinned"})
            self.assertEqual(next(t for t in turns if t["id"] == "S1.U2")["key_moment"], "KM1")
            self.assertIn(CLEAN["S1.A1"]["text"], call.seen[1])  # S2's call sees S1 as frozen text

            record = json.loads((run / "generation-record.json").read_text())
            self.assertEqual(record["prompt"]["version"], generate.GENERATOR_PROMPT_VERSION)
            self.assertEqual(record["prompt"]["sha"], generate.prompt_hash())
            self.assertEqual(set(record["prompt"]["rendered"]), {"S1", "S2", "S3", "S4"})
            self.assertEqual(record["spec"]["version"], SPEC.version)
            self.assertTrue(record["spec"]["sha"])
            self.assertEqual(record["calls"][0]["raw"], json.dumps(canned("S1")))
            self.assertEqual(record["generator"]["returned_models"], ["fake-gen-1"])
            self.assertTrue(record["generator"]["family_under_test"])  # openai is a family under test
            self.assertEqual(record["sampling"], {k: v for k, v in generate.SAMPLING.items() if k != "thinking"})
            self.assertIn("**0 errors**", (run / "checks.md").read_text())

            # the item tests can then run on the real prefix
            ex = Path(d) / "ex.yaml"
            ex.write_text(yaml.safe_dump({"examples": [{"id": "p", "item": "KM1-C3", "checkpoint": "KM1", "kind": "known_pass",
                                                        "expected": "not_met", "reply": "What makes you think so?"}]}))
            quietly(item_tests.main, ["--examples", str(ex), "--dry-run", "--out", str(Path(d) / "it"),
                                      "--conversation", str(run / "conversation.v1.0-draft.yaml")])
            prompt = next((Path(d) / "it").glob("*/p.txt")).read_text()
            self.assertNotIn("has not been generated yet", prompt)  # the placeholder text
            self.assertIn(CLEAN["S1.A1"]["text"], prompt)

    def test_generator_cannot_rewrite_a_pinned_line(self):
        rewrite = canned("S1")
        rewrite["turns"].append({"id": "S1.U2", "text": "a different line", "facts": []})
        with tempfile.TemporaryDirectory() as d:
            code = quietly(generate.main, ["--generator", "openrouter:openai/fake", "--sessions", "S1", "--out", d],
                           call=fake([rewrite, rewrite]))
            self.assertEqual(code, 2)
            record = json.loads((run_dir(d) / "generation-record.json").read_text())
            self.assertEqual(record["failed_session"], "S1")
            self.assertEqual([c["attempt"] for c in record["calls"]], [1, 2])
            self.assertTrue(all("S1.U2" in c["error"] and c["raw"] for c in record["calls"]))

    def test_retry_once_recovers_and_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            code = quietly(generate.main, ["--generator", "openrouter:openai/fake", "--sessions", "S1", "--out", d],
                           call=fake([client.CallError("timeout"), canned("S1")]))
            record = json.loads((run_dir(d) / "generation-record.json").read_text())
            self.assertEqual([c["error"] for c in record["calls"]], ["timeout", None])
            self.assertEqual(code, 1)  # S2-S5 missing: turn_order reports it, the draft is still written
            self.assertIn("missing S2.A1", (run_dir(d) / "checks.md").read_text())

    def test_a_failed_session_stops_the_run(self):
        bad = {"turns": []}
        call = fake([bad, bad])
        with tempfile.TemporaryDirectory() as d:
            code = quietly(generate.main, ["--generator", "openrouter:openai/fake", "--sessions", "S1,S2", "--out", d],
                           call=call)
            record = json.loads((run_dir(d) / "generation-record.json").read_text())
        self.assertEqual((code, len(call.seen), record["failed_session"]), (2, 2, "S1"))  # no S2 call

    def test_a_crash_keeps_the_calls_already_made(self):
        call = fake([canned("S1"), canned("S2"), ConnectionResetError("peer reset")])
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ConnectionResetError):
                quietly(generate.main, ["--generator", "openrouter:openai/fake", "--out", d], call=call)
            record = json.loads((run_dir(d) / "generation-record.json").read_text())
            self.assertEqual([c["raw"] for c in record["calls"][:2]], [json.dumps(canned("S1")), json.dumps(canned("S2"))])
            self.assertIn("ConnectionResetError", record["crashed"])
            self.assertIn("S2.A5", load_conversation(run_dir(d) / "conversation.v1.0-draft.yaml"))

    def test_regenerating_a_session_refuses_to_keep_later_ones(self):
        with tempfile.TemporaryDirectory() as d:
            quietly(generate.main, ["--generator", "openrouter:openai/fake", "--out", d + "/a"],
                    call=fake([canned(s) for s in ("S1", "S2", "S3", "S4")]))
            full = run_dir(d + "/a") / "conversation.v1.0-draft.yaml"
            with self.assertRaises(SystemExit):  # S3 and S4 were written against the old S2
                quietly(generate.main, ["--generator", "openrouter:openai/fake", "--sessions", "S2", "--conversation",
                                        str(full), "--out", d + "/b"], call=fake([]))
            call = fake([canned("S2"), canned("S3"), canned("S4")])
            code = quietly(generate.main, ["--generator", "openrouter:openai/fake", "--sessions", "S2,S3,S4",
                                           "--conversation", str(full), "--out", d + "/c"], call=call)
            self.assertEqual(code, 0)
            record = json.loads((run_dir(d + "/c") / "generation-record.json").read_text())
            self.assertEqual(record["previous_conversation"]["carried"], generate.to_write(SPEC, "S1"))

    def test_a_conversation_file_with_an_edited_pinned_line_is_refused(self):
        turns = clean()
        next(t for t in turns if t["id"] == "S1.U2")["text"] = "EDITED KM1 LINE"
        with tempfile.TemporaryDirectory() as d:
            convo = Path(d) / "c.yaml"
            convo.write_text(yaml.safe_dump({"turns": turns}))
            err = io.StringIO()
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                generate.main(["--generator", "openrouter:openai/fake", "--sessions", "S2,S3,S4", "--conversation",
                               str(convo), "--out", d], call=fake([canned(s) for s in ("S2", "S3", "S4")]))
            self.assertIn("changes the pinned lines at S1.U2", err.getvalue())

    def test_a_rule_that_names_no_moment_stops_the_run(self):
        spec = yaml.safe_load(SPEC.text)
        rules = spec["generation"]["prefix_style"]["must_not_preanswer"]
        rules["before_KM9"] = rules.pop("before_KM8")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "spec.yaml"
            path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
            with self.assertRaises(SystemExit):
                quietly(generate.main, ["--dry-run", "--spec", str(path), "--out", d])

    def test_later_session_needs_earlier_text(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                quietly(generate.main, ["--generator", "openrouter:openai/fake", "--sessions", "S2", "--out", d],
                        call=fake([]))
            quietly(generate.main, ["--generator", "openrouter:openai/fake", "--sessions", "S1", "--out", d + "/a"],
                    call=fake([canned("S1")]))
            first = run_dir(d + "/a") / "conversation.v1.0-draft.yaml"
            call = fake([canned("S2")])
            quietly(generate.main, ["--generator", "openrouter:openai/fake", "--sessions", "S2", "--conversation",
                                    str(first), "--out", d + "/b"], call=call)
            self.assertIn(CLEAN["S1.A1"]["text"], call.seen[0])
            ids = list(load_conversation(run_dir(d + "/b") / "conversation.v1.0-draft.yaml"))
            self.assertEqual(ids[0], "S1.U1")
            self.assertIn("S2.A5", ids)


if __name__ == "__main__":
    unittest.main()
