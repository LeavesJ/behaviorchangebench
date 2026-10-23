"""The models config (infra audit 4.2): valid files load, each malformed case is named, README rule 5 is enforced."""

import tempfile
import unittest
from pathlib import Path

from bcb import grader, models_config as mc
from bcb.schema import FormatError
from tests.formats_examples import AUDIT_MODELS, assert_each_named, assert_never_raises, parse

MINIMAL = """\
models: [{id: grok, provider: openrouter, model: x-ai/grok-4.7, family: x-ai}]
"""


class ValidModelsConfigTest(unittest.TestCase):
    def test_a_minimal_file_loads_with_vendor_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.yaml"
            path.write_text(MINIMAL)
            c = mc.load(path)
        self.assertEqual(c.models, (mc.Model("grok", "openrouter", "x-ai/grok-4.7", "x-ai", mc.Sampling(None, None, None, False)),))
        self.assertEqual((c.graders, c.models[0].target), ({}, "openrouter:x-ai/grok-4.7"))

    def test_the_audits_example_loads_with_each_entrys_own_settings(self):
        self.assertEqual(mc.problems(parse(AUDIT_MODELS)), [])
        c = mc.build(parse(AUDIT_MODELS))
        self.assertEqual(c.models[0].sampling, mc.Sampling(None, 16000, True, False), "reasoning: on reads as yes in YAML")
        primary, second = c.graders["primary"], c.graders["second"]
        self.assertEqual((primary.target, primary.prompt, primary.sampling),
                         ("openrouter:anthropic/claude-opus-5.5", grader.PROMPT_VERSION, mc.Sampling(None, 8000, True, True)))
        self.assertEqual(second.sampling, mc.Sampling(0.0, 1000, False, True))

    def test_reasoning_may_be_written_in_quotes_or_left_to_the_vendor(self):
        raw = parse(AUDIT_MODELS)
        raw["models"][0]["sampling"].update(reasoning="off")
        raw["models"][1]["sampling"].update(reasoning="vendor_default")
        self.assertEqual(mc.problems(raw), [])
        models = mc.build(raw).models
        self.assertEqual((models[0].sampling.reasoning, models[1].sampling.reasoning), (False, None))

    def test_a_model_under_test_may_carry_a_system_prompt(self):
        raw = parse(AUDIT_MODELS)
        model(raw, "gpt-6-astra")["system_prompt"] = "You are a helpful assistant."
        self.assertEqual(mc.problems(raw), [])
        models = mc.build(raw).models
        self.assertEqual((models[0].system_prompt, models[1].system_prompt), ("You are a helpful assistant.", None))

    def test_max_tokens_may_be_left_to_the_vendor_as_the_docstring_says(self):
        raw = parse(AUDIT_MODELS)
        raw["models"][0]["sampling"].update(max_tokens="vendor_default")
        self.assertEqual(mc.problems(raw), [])
        self.assertEqual(mc.build(raw).models[0].sampling, mc.Sampling(None, None, True, False))


def model(raw, model_id):
    return next(m for m in raw["models"] if m["id"] == model_id)


CASES = [
    ("misspelled top key", lambda r: r.update(model=r.pop("models")), "unknown key 'model' (did you mean models?)"),
    ("no models", lambda r: r.update(models=[]), "models must be a list with at least one model"),
    ("graders as a list", lambda r: r.update(graders=[r["graders"]["primary"]]), "graders must be a block of named graders"),
    ("model id twice", lambda r: r["models"].append(dict(model(r, "gpt-6-astra"), family="x")), "model id gpt-6-astra is used twice"),
    ("model id with a space", lambda r: model(r, "gpt-6-astra").update(id="gpt 6"), "model gpt 6 id 'gpt 6' may use only letters"),
    ("grader name with a space", lambda r: r["graders"].update({"first pass": r["graders"].pop("second")}),
     "grader name 'first pass' may use only letters"),
    ("a provider the client cannot call", lambda r: model(r, "gpt-6-astra").update(provider="anthropic"),
     "model gpt-6-astra provider must be one of deepseek, openrouter, openai, not 'anthropic'"),
    ("no family", lambda r: r["graders"]["primary"].pop("family"), "grader primary: missing family"),
    ("model name a number", lambda r: model(r, "gemini-3.1-pro").update(model=3.1), "model gemini-3.1-pro model must be text"),
    ("a grader prompt this code does not have", lambda r: r["graders"]["second"].update(prompt="grader-v0.4"),
     "grader second prompt 'grader-v0.4' is not the grader prompt this code has (grader-v0.3)"),
    ("temperature in words", lambda r: model(r, "gpt-6-astra")["sampling"].update(temperature="hot"),
     "model gpt-6-astra sampling.temperature must be a number from 0 to 2, or vendor_default"),
    ("temperature too high", lambda r: model(r, "gpt-6-astra")["sampling"].update(temperature=5), "not the number 5"),
    ("max_tokens zero", lambda r: r["graders"]["primary"]["sampling"].update(max_tokens=0),
     "grader primary sampling.max_tokens must be a whole number, 1 or more"),
    ("reasoning a guess", lambda r: model(r, "gpt-6-astra")["sampling"].update(reasoning="maybe"),
     "model gpt-6-astra sampling.reasoning must be on, off or vendor_default, not 'maybe'"),
    ("reasoning a number", lambda r: model(r, "gpt-6-astra")["sampling"].update(reasoning=1), "not the number 1"),
    ("json for a model under test", lambda r: model(r, "gpt-6-astra")["sampling"].update(json=True),
     "model gpt-6-astra sampling.json is for graders only"),
    ("json in words", lambda r: r["graders"]["primary"]["sampling"].update(json="yes please"),
     "grader primary sampling.json must be true or false"),
    ("sampling key misspelled", lambda r: model(r, "gpt-6-astra")["sampling"].update(temp=1),
     "model gpt-6-astra sampling: unknown key 'temp' (did you mean temperature?)"),
    ("a system prompt a number", lambda r: model(r, "gpt-6-astra").update(system_prompt=3),
     "model gpt-6-astra system_prompt must be text; write it in quotes"),
    ("a system prompt for a grader", lambda r: r["graders"]["primary"].update(system_prompt="Be strict."),
     "grader primary: system_prompt is for models under test only; a grader is sent the versioned grader prompt"),
    ("the family rule turned off", lambda r: r["rules"].update(grader_family_differs_from_every_model=False),
     "rules.grader_family_differs_from_every_model must be true"),
    ("a grader in a tested model's family", lambda r: r["models"].append(
        {"id": "claude", "provider": "openrouter", "model": "anthropic/claude-sonnet-5", "family": "Anthropic"}),
     "grader primary is in the anthropic family, and so is model claude; a grader never grades its own family (README rule 5)"),
    ("a family in capitals, served through another vendor's endpoint", lambda r: r["models"].append(
        {"id": "claude", "provider": "openai", "model": "claude-sonnet-5", "family": "ANTHROPIC"}),
     "grader primary is in the anthropic family, and so is model claude"),
    # a family written differently does not hide the vendor the model name shows
    ("a tested model's family misspelled", lambda r: r["models"].append(
        {"id": "claude", "provider": "openrouter", "model": "anthropic/claude-sonnet-5", "family": "claude"}),
     "grader primary is in the anthropic family, and so is model claude"),
    ("a grader's provider is a tested model's vendor", lambda r: (r["graders"]["second"].update(family="ds-flash"),
                                                                  r["models"].append({"id": "ds", "provider": "openrouter", "model": "deepseek/deepseek-v4", "family": "deepseek-ai"})),
     "grader second is in the deepseek family, and so is model ds"),
]


class MalformedModelsConfigTest(unittest.TestCase):
    def test_each_mistake_is_named(self):
        assert_each_named(self, mc.problems, parse(AUDIT_MODELS), CASES)

    def test_a_value_of_the_wrong_kind_anywhere_is_named_not_raised(self):
        assert_never_raises(self, mc.problems, parse(AUDIT_MODELS))

    def test_a_file_that_is_not_a_block_is_named(self):
        self.assertEqual(mc.problems(["gpt-6-astra"]), ["the file must be a block of keys (models, graders, rules), not a list"])

    def test_a_same_family_grader_is_refused_on_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.yaml"
            path.write_text(MINIMAL + "graders: {primary: {provider: openrouter, model: x-ai/grok-4.7-fast, family: x-ai}}\n")
            with self.assertRaises(FormatError) as e:
                mc.load(path)
        self.assertEqual(e.exception.problems, ["grader primary is in the x-ai family, and so is model grok; a grader "
                                                "never grades its own family (README rule 5)"])


if __name__ == "__main__":
    unittest.main()
