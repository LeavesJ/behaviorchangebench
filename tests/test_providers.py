"""The models config at run time (infra audit task 6): each model and grader is called with its own settings, the
Makefile's variables still override the file, a grader in a tested model's family is refused before any call, and
the manifest records what each model was sent. Every call goes to a fake client."""

import contextlib
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from bcb import models_config, pilot, providers
from bcb.models_config import Sampling
from tests.test_pilot import RAW, attached

ROOT = Path(__file__).resolve().parent.parent
OCT8_MODELS = "trajectories/vaping-v0/v2/models.yaml"  # the Oct 8 models and grader, for make ... MODELS_CONFIG=
SYSTEM = "You are a helpful assistant."  # the system-prompt arm's prompt, spec.yaml run.sensitivity_variants
CONFIG = f"""\
models:
  - {{id: one, provider: openrouter, model: a/one, family: a,
     sampling: {{temperature: 0.7, max_tokens: 500, reasoning: off}}, system_prompt: "{SYSTEM}"}}
  - {{id: two, provider: openrouter, model: b/two, family: b, sampling: {{max_tokens: vendor_default}}}}
graders:
  primary: {{provider: deepseek, model: deepseek-flash, family: deepseek,
            sampling: {{temperature: 0, max_tokens: 2000, reasoning: off, json: true}}}}
  second: {{provider: openrouter, model: c/judge, family: c, sampling: {{reasoning: on, json: true}}}}
"""
PRIMARY_SENT = {"temperature": 0.0, "max_tokens": 2000, "json_mode": True, "thinking": False}


def config(text=CONFIG):
    return models_config.build(yaml.safe_load(text), Path("models.yaml"))


def recording(calls, graders=("deepseek:", "openrouter:c/judge")):
    """A fake client that keeps every call; a grader always answers not_met, which needs no quote."""
    def call(target, messages, **kw):
        calls.append((target, messages, kw))
        text = (json.dumps({"reasoning": "r", "quote": "", "verdict": "not_met"}) if target.startswith(graders)
                else "A kind and specific answer.")
        return {"text": text, "model": target, "usage": {}, "latency_s": 0.0, "finish_reason": "stop"}
    return call


def boom(*a, **k):
    raise AssertionError("a refused run called the network")


class Oct8SetupTest(unittest.TestCase):
    def test_the_oct8_models_config_sends_what_the_oct8_runs_sent(self):
        """The v2 models config, read through this layer, gives each Oct 8 manifest's models and settings."""
        c = models_config.load(ROOT / OCT8_MODELS)
        models, judge = providers.models_for_run(c), providers.grader_for_run(c)
        for path in sorted((ROOT / "runs/oct8-graded").glob("*/manifest.json")):
            with self.subTest(path.parent.name):
                m = json.loads(path.read_text())
                self.assertLessEqual(set(m["models"]), {e.target for e in models})
                self.assertEqual(json.dumps(providers.reply_sampling(models)), json.dumps(m["reply_sampling"]))
                self.assertEqual(judge.target, m["grader"])
                # key order too: the manifest's grader_sampling is written as client.chat is called
                self.assertEqual(json.dumps(providers.grader_settings(judge)), json.dumps(m["grader_sampling"]))


class ResolveTest(unittest.TestCase):
    def test_a_name_or_a_listed_target_gets_its_entry_and_an_unlisted_target_the_old_defaults(self):
        c = config()
        one = providers.model_under_test("one", c)
        self.assertEqual(providers.model_under_test("openrouter:a/one", c), one)
        self.assertEqual((one.sampling, one.system_prompt), (Sampling(0.7, 500, False), SYSTEM))
        self.assertEqual(providers.model_under_test("openrouter:z/new", c).sampling, Sampling(None, 16000, None))
        pinned = providers.model_under_test("one", c, reasoning="on")
        self.assertEqual(pinned.sampling, Sampling(0.7, 500, True), "--reasoning replaces reasoning only")

    def test_a_grader_keeps_its_settings_unless_the_reasoning_flag_is_given(self):
        c = config()
        self.assertEqual(providers.grader_settings(providers.grader_for_run(c)), PRIMARY_SENT)
        self.assertEqual(providers.grader_settings(providers.grader_for_run(c, "second")),
                         {"temperature": None, "max_tokens": None, "json_mode": True, "thinking": True})
        for name, flag, sent in (("primary", "on", providers.sampling("on")),
                                 ("deepseek:deepseek-chat", None, providers.sampling("off")),
                                 ("deepseek:deepseek-chat", "on", providers.sampling("on"))):
            with self.subTest(name=name, flag=flag):
                self.assertEqual(providers.grader_settings(providers.grader_for_run(c, name, flag)), sent)
        self.assertIsNone(providers.grader_for_run(None))

    def test_a_name_the_config_does_not_give_is_named(self):
        for call, said in ((lambda: providers.model_under_test("grok"), "no models config was given"),
                           (lambda: providers.model_under_test("grok", config()),
                            "model 'grok' is not in models.yaml (its models: one, two); write provider:model"),
                           (lambda: providers.grader_for_run(config(), "third"), "(its graders: primary, second)"),
                           (lambda: providers.grader_for_run(config(CONFIG.replace("primary:", "first:"))),
                            "models.yaml has no grader called primary")):
            with self.subTest(said), self.assertRaises(ValueError) as e:
                call()
            self.assertIn(said, str(e.exception))

    def test_two_entries_that_call_one_model_cannot_share_a_run(self):
        twice = CONFIG.replace("{id: two, provider: openrouter, model: b/two", "{id: two, provider: openrouter, model: a/one")
        with self.assertRaisesRegex(ValueError, "models one and two both call openrouter:a/one"):
            providers.models_for_run(config(twice))
        self.assertEqual(len(providers.models_for_run(config(twice), ["two"])), 1)


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.branches, self.config = self.tmp / "branches.yaml", self.tmp / "models.yaml"
        self.branches.write_text(yaml.safe_dump(RAW))
        self.config.write_text(CONFIG)

    def run_pilot(self, *args, call, with_config=True):
        """(exit code, the --out folder, what went to stderr)."""
        out = self.tmp / f"out{len(list(self.tmp.glob('out*')))}"
        argv = ["--branches", str(self.branches), "--only", "HQ-TEST", "--samples", "1", "--workers", "1",
                "--out", str(out), *(["--models-config", str(self.config)] if with_config else []), *args]
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = pilot.main(argv, call=call)
        return code, out, err.getvalue()

    def manifest(self, out):
        return json.loads((next(out.glob("*-models")) / "manifest.json").read_text())

    def test_each_model_is_sent_its_own_settings_and_system_prompt_and_the_manifest_says_so(self):
        calls = []
        code, out, _ = self.run_pilot(call=recording(calls))
        self.assertEqual(code, 0)
        sent = {t: (messages, kw) for t, messages, kw in calls if not t.startswith("deepseek:")}
        conversation = attached().messages("HQ-TEST")
        one = {"temperature": 0.7, "max_tokens": 500, "thinking": False}
        two = {"temperature": None, "max_tokens": None, "thinking": None}
        self.assertEqual(sent["openrouter:a/one"], ([{"role": "system", "content": SYSTEM}] + conversation, one))
        self.assertEqual(sent["openrouter:b/two"], (conversation, two))
        self.assertEqual([kw for t, _, kw in calls if t.startswith("deepseek:")], [PRIMARY_SENT] * 12)
        m = self.manifest(out)
        self.assertEqual(m["model_settings"], {
            "openrouter:a/one": {"id": "one", "family": "a", "from": "models config", "sent": one, "system_prompt": SYSTEM},
            "openrouter:b/two": {"id": "two", "family": "b", "from": "models config", "sent": two, "system_prompt": None}})
        self.assertIsNone(m["reply_sampling"], "the two models were sent different settings")
        self.assertEqual(m["models_config"], {"path": str(self.config.resolve()),
                                              "sha256": hashlib.sha256(self.config.read_bytes()).hexdigest()})
        self.assertEqual((m["grader"], m["grader_name"], m["grader_sampling"]), ("deepseek:deepseek-flash", "primary",
                                                                                PRIMARY_SENT))

    def test_the_command_line_still_overrides_the_config(self):
        calls = []
        args = ("--models", "two,openrouter:z/new", "--reasoning", "on", "--grader", "deepseek:deepseek-chat")
        self.assertEqual(self.run_pilot(*args, call=recording(calls))[0], 0)
        sent = {t: kw for t, _, kw in calls}
        self.assertEqual(sent["openrouter:b/two"], {"temperature": None, "max_tokens": None, "thinking": True})
        self.assertEqual(sent["openrouter:z/new"], {"temperature": None, "max_tokens": 16000, "thinking": True})
        self.assertEqual(sent["deepseek:deepseek-chat"], providers.sampling("off"))
        self.assertNotIn("openrouter:a/one", sent)
        calls.clear()
        code, out, _ = self.run_pilot("--grader-reasoning", "on", call=recording(calls))
        self.assertEqual({json.dumps(kw) for t, _, kw in calls if t.startswith("deepseek:")},
                         {json.dumps(providers.sampling("on"))})
        self.assertEqual(self.manifest(out)["grader_sampling"], providers.sampling("on"))

    def test_without_a_config_the_runner_calls_as_it_always_has(self):
        calls = []
        code, out, _ = self.run_pilot("--models", "openrouter:z/new", "--grader", "deepseek:deepseek-flash",
                                      call=recording(calls), with_config=False)
        self.assertEqual(code, 0)
        self.assertEqual({json.dumps(kw) for t, _, kw in calls if t == "openrouter:z/new"},
                         {json.dumps({"temperature": None, "max_tokens": 16000, "thinking": None})})
        m = self.manifest(out)
        self.assertEqual(m["reply_sampling"], {"temperature": "vendor default", "max_tokens": 16000,
                                               "reasoning": "vendor default"})
        self.assertEqual((m["models_config"], m["model_settings"]["openrouter:z/new"]["from"]), (None, "command line"))

    def test_a_grader_in_the_family_of_a_model_under_test_is_refused_before_any_call(self):
        same = "and so is model openrouter:deepseek/deepseek-v4"
        cases = [
            (["--models", "openrouter:deepseek/deepseek-v4", "--grader", "deepseek:deepseek-flash"], False,
             f"grader deepseek:deepseek-flash is in the deepseek family, {same}"),
            (["--models", "openrouter:deepseek/deepseek-v4"], True,
             f"grader primary (deepseek:deepseek-flash) is in the deepseek family, {same}"),
            (["--models", "openrouter:deepseek/deepseek-v4", "--dry-run"], True, same),
            (["--models", "openrouter:c/three", "--grader", "second"], True, "grader second (openrouter:c/judge) is in "
                                                                             "the c family, and so is model openrouter:c/three"),
            # Oct 2: a draw for labels drew Claude replies that the Opus grader would have graded
            (["--models", "openrouter:c/three", "--no-grade"], True, "This run only draws replies, but the graders"),
        ]
        for args, with_config, said in cases:
            with self.subTest(args=args):
                code, out, err = self.run_pilot(*args, call=boom, with_config=with_config)
                self.assertEqual(code, 2)
                for words in (said, "README rule 5", "Nothing was called."):
                    self.assertIn(words, err)
                self.assertFalse(out.exists(), "a refused run made a run folder")

    def test_replies_on_disk_from_a_model_in_the_graders_family_are_not_graded(self):
        draw = ("--models", "openrouter:a/one,openrouter:deepseek/deepseek-v4", "--no-grade")
        self.assertEqual(self.run_pilot(*draw, call=recording([]), with_config=False)[0], 0)
        run = next(self.tmp.glob("out*/*-models"))
        before = (run / "units.jsonl").read_text()
        grade = ("--models", "openrouter:a/one", "--grader", "deepseek:deepseek-flash", "--resume", str(run))
        code, _, err = self.run_pilot(*grade, call=boom, with_config=False)
        self.assertEqual(code, 2)
        self.assertIn("and so is model openrouter:deepseek/deepseek-v4", err)
        self.assertIn(f"already in {run / 'units.jsonl'}", err)
        self.assertEqual((run / "units.jsonl").read_text(), before)


class MakefileTest(unittest.TestCase):
    """What each make target runs, printed by make -n: as before without MODELS_CONFIG, the config with it."""

    def make(self, target, *overrides):
        if not shutil.which("make"):
            self.skipTest("make is not installed")
        env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "MAKELEVEL")
               and k not in ("GRADER", "GRADER_REASONING", "PILOT_MODELS", "MODELS_CONFIG", "REASONING")}
        done = subprocess.run(["make", "-n", "-s", target, "PYTHON=py", *overrides], cwd=ROOT, env=env,
                              capture_output=True, text=True, check=True)
        return shlex.split(done.stdout.strip().splitlines()[-1])

    def with_config(self, target, *overrides):
        return self.make(target, f"MODELS_CONFIG={OCT8_MODELS}", *overrides)

    def test_without_a_models_config_every_target_runs_what_it_ran_before(self):
        """The Makefile at ed83abb, word for word: the Oct 8 models at the vendor's reasoning, Opus with reasoning on."""
        models = ["--models", "openrouter:openai/gpt-6-astra,openrouter:google/gemini-3.1-pro-preview,"
                              "openrouter:x-ai/grok-4.7"]
        opus, on = ["--grader", "openrouter:anthropic/claude-opus-5.5"], ["--grader-reasoning", "on"]
        for target, argv in (("pilot", ["bcb.pilot", *models, *opus, *on]),
                             ("draw", ["bcb.pilot", *models, "--no-grade", *on]),
                             ("pilot-dry", ["bcb.pilot", "--dry-run", *on]),
                             ("pilot-refs", ["bcb.pilot", "--references", *opus, *on]),
                             ("item-tests", ["bcb.item_tests", *opus, *on]),
                             ("generate", ["bcb.generate", "--generator", "openrouter:z/gen", *opus])):
            with self.subTest(target):
                self.assertEqual(self.make(target, "GENERATOR=openrouter:z/gen"), ["py", "-m", *argv])

    def test_targets_read_the_models_config_when_given_one(self):
        config = ["--models-config", OCT8_MODELS]
        self.assertEqual(self.with_config("pilot"), ["py", "-m", "bcb.pilot", *config])
        self.assertEqual(self.with_config("draw"), ["py", "-m", "bcb.pilot", *config, "--no-grade"])
        self.assertEqual(self.with_config("pilot-dry"), ["py", "-m", "bcb.pilot", "--dry-run", *config])
        self.assertEqual(self.with_config("pilot-refs"), ["py", "-m", "bcb.pilot", "--references", *config])
        self.assertEqual(self.with_config("item-tests"), ["py", "-m", "bcb.item_tests", *config])
        self.assertEqual(self.with_config("generate", "GENERATOR=openrouter:z/gen"),
                         ["py", "-m", "bcb.generate", "--generator", "openrouter:z/gen", *config])

    def test_the_old_variables_still_override_the_models_config(self):
        self.assertEqual(self.with_config("pilot", "GRADER=deepseek:deepseek-flash", "PILOT_MODELS=openrouter:z/new"),
                         ["py", "-m", "bcb.pilot", "--models-config", OCT8_MODELS, "--models", "openrouter:z/new",
                          "--grader", "deepseek:deepseek-flash", "--grader-reasoning", "off"])
        self.assertEqual(self.with_config("item-tests", "GRADER=openrouter:anthropic/claude-opus-5.5")[-2:],
                         ["--grader-reasoning", "on"])  # Opus refuses reasoning off
        # a grader the config names keeps the settings written there
        self.assertNotIn("--grader-reasoning", self.with_config("pilot", "GRADER=second"))
        self.assertEqual(self.with_config("pilot-dry", "PILOT_MODELS=grok-4.7", "GRADER=second")[3:],
                         ["--dry-run", "--models-config", OCT8_MODELS, "--models", "grok-4.7", "--grader", "second"])
        self.assertEqual(self.make("pilot", "REASONING=off", "MODELS_CONFIG=configs/other.yaml")[3:],
                         ["--models-config", "configs/other.yaml", "--reasoning", "off"])


if __name__ == "__main__":
    unittest.main()
