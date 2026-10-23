"""The models config: the models under test and the graders, each with its own settings.

    configs/models.yaml        (infra audit 4.2)

Each entry names a provider bcb/client.py can call, the provider's name for the model, and the model's family. The
file is refused if a grader shares a family with any model under test (README rule 5), judged by the family each
entry declares and by the vendor its model name shows, so a misspelled family does not let a clash through.
Sampling is per entry: temperature, max_tokens and reasoning each default to the vendor's own (write vendor_default
to say so), and only a grader may ask for JSON output. A model under test may carry a system_prompt, sent before
the conversation (the system-prompt arm, spec.yaml run.sensitivity_variants); a grader may not, since its whole
prompt is the versioned grader prompt. bcb.providers turns an entry into a call.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import client, grader, schema
from .schema import an_id, block, describe, duplicates, is_text, one_of, text, whole_number

TOP = ("models", "graders", "rules")
MODEL = ("id", "provider", "model", "family", "sampling", "system_prompt")
GRADER = ("provider", "model", "family", "prompt", "sampling")
SAMPLING = ("temperature", "max_tokens", "reasoning", "json")
RULES = ("grader_family_differs_from_every_model",)
VENDOR_DEFAULT = "vendor_default"
MAX_TEMPERATURE = 2.0  # the top of OpenAI's and Google's range; Anthropic's stops at 1
REASONING = {True: True, False: False, "on": True, "off": False, VENDOR_DEFAULT: None}  # YAML reads on/off as yes/no


@dataclass(frozen=True)
class Sampling:
    temperature: float | None = None  # None: the vendor's default
    max_tokens: int | None = None  # None: the vendor's default
    reasoning: bool | None = None  # None: the vendor's default
    json: bool = False  # graders only


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    model: str
    family: str
    sampling: Sampling = Sampling()
    system_prompt: str | None = None  # sent before the conversation; None sends none

    @property
    def target(self):
        """The "provider:model" form bcb.client.chat takes."""
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class Grader:
    name: str | None  # primary, second, ...; None for a grader a run names only as provider:model
    provider: str
    model: str
    family: str
    prompt: str = grader.PROMPT_VERSION
    sampling: Sampling = Sampling()

    @property
    def target(self):
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class ModelsConfig:
    models: tuple[Model, ...]
    graders: dict = field(default_factory=dict, hash=False)  # {name: Grader}
    path: Path | None = None


# ---------- checking ----------

def problems(raw):
    """Everything wrong with a models config, one line each; empty means it can be loaded."""
    if not isinstance(raw, dict):
        return [f"the file must be a block of keys ({', '.join(TOP)}), not {describe(raw)}"]
    out = block("the file", raw, TOP, ("models",))
    models = raw.get("models")
    if models is not None and (not isinstance(models, list) or not models):
        out.append(f"models must be a list with at least one model, not {describe(models)}")
    elif models is not None:
        out += [f"model id {i} is used twice" for i in duplicates(m.get("id") for m in models if isinstance(m, dict))]
        for n, m in enumerate(models, 1):
            out += _entry(f"model {m['id']}" if isinstance(m, dict) and is_text(m.get("id")) else f"model {n}",
                          m, MODEL, ("id", "provider", "model", "family"), judge=False)
    graders = raw.get("graders")
    if graders is not None and (not isinstance(graders, dict) or not graders):
        out.append(f"graders must be a block of named graders, such as primary: {{...}}, not {describe(graders)}")
    elif graders is not None:
        for name, g in graders.items():
            out += an_id("grader name", name)
            out += _entry(f"grader {name}", g, GRADER, ("provider", "model", "family"), judge=True)
    if raw.get("rules") is not None:
        out += _rules(raw["rules"])
    if isinstance(models, list) and isinstance(graders, dict):
        out += _same_family(models, graders)
    return out


def _entry(name, e, keys, required, judge):
    prompted = judge and isinstance(e, dict) and "system_prompt" in e
    out = block(name, {k: v for k, v in e.items() if k != "system_prompt"} if prompted else e, keys, required)
    if not isinstance(e, dict):
        return out
    if prompted:
        out.append(f"{name}: system_prompt is for models under test only; a grader is sent the versioned grader prompt "
                   f"({grader.PROMPT_VERSION}) and nothing else")
    elif e.get("system_prompt") is not None:
        out += text(f"{name} system_prompt", e["system_prompt"])
    out += an_id(f"{name} id", e["id"]) if e.get("id") is not None else []
    out += one_of(f"{name} provider", e["provider"], tuple(client.PROVIDERS)) if e.get("provider") is not None else []
    out += [p for k in ("model", "family") if e.get(k) is not None for p in text(f"{name} {k}", e[k])]
    if e.get("prompt") is not None and e["prompt"] != grader.PROMPT_VERSION:
        out.append(f"{name} prompt {e['prompt']!r} is not the grader prompt this code has ({grader.PROMPT_VERSION})")
    return out + (_sampling(name, e["sampling"], judge) if e.get("sampling") is not None else [])


def _sampling(name, s, judge):
    where = f"{name} sampling"
    out = block(where, s, SAMPLING)
    if not isinstance(s, dict):
        return out
    t = s.get("temperature")
    if t is not None and t != VENDOR_DEFAULT and not (
            isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= t <= MAX_TEMPERATURE):
        out.append(f"{where}.temperature must be a number from 0 to {MAX_TEMPERATURE:g}, or {VENDOR_DEFAULT}, "
                   f"not {describe(t)}")
    n = s.get("max_tokens")
    if n not in (None, VENDOR_DEFAULT) and whole_number(f"{where}.max_tokens", n):
        out.append(f"{where}.max_tokens must be a whole number, 1 or more, or {VENDOR_DEFAULT}, not {describe(n)}")
    r = s.get("reasoning")
    if r is not None and not (isinstance(r, bool) or r in ("on", "off", VENDOR_DEFAULT)):
        out.append(f"{where}.reasoning must be on, off or {VENDOR_DEFAULT}, not {describe(r)}")
    if s.get("json") is not None:
        if not isinstance(s["json"], bool):
            out.append(f"{where}.json must be true or false, not {describe(s['json'])}")
        elif s["json"] and not judge:
            out.append(f"{where}.json is for graders only: a model under test answers the user in plain text")
    return out


def _rules(value):
    out = block("rules", value, RULES)
    if isinstance(value, dict) and value.get("grader_family_differs_from_every_model") not in (None, True):
        out.append("rules.grader_family_differs_from_every_model must be true: README rule 5 (a grader never grades "
                   "its own family) has no opt-out")
    return out


def _same_family(models, graders):
    """README rule 5: a grader's family is never the family of a model it grades."""
    out = []
    for name, g in graders.items():
        for m in models:
            shared = families(g) & families(m)
            if shared:
                out.append(f"grader {name} is in the {min(shared)} family, and so is model {m.get('id')}; a grader "
                           "never grades its own family (README rule 5)")
    return out


def families(e):
    """The family an entry declares and the vendor its model name shows (for openrouter the part before '/', else
    the provider, as bcb.generate.family reads a target), so a misspelled family cannot hide a clash. bcb.providers
    reads entries the same way when a run starts."""
    if not isinstance(e, dict):
        return set()
    out = {e["family"].casefold()} if is_text(e.get("family")) else set()
    provider, model = e.get("provider"), e.get("model")
    if provider == "openrouter" and is_text(model) and "/" in model:
        out.add(model.split("/", 1)[0].casefold())
    elif isinstance(provider, str) and provider in client.PROVIDERS and provider != "openrouter":
        out.add(provider)
    return out


# ---------- loading ----------

def load(path):
    """The models config as a ModelsConfig; FormatError listing every problem if it has any."""
    return schema.load(path, problems, build)


def build(raw, path=None):
    """A ModelsConfig from a file that `problems` passed."""
    models = tuple(Model(m["id"], m["provider"], m["model"], m["family"], _sampling_of(m.get("sampling")),
                         m.get("system_prompt")) for m in raw["models"])
    graders = {name: Grader(name, g["provider"], g["model"], g["family"], g.get("prompt") or grader.PROMPT_VERSION,
                            _sampling_of(g.get("sampling")))
               for name, g in (raw.get("graders") or {}).items()}
    return ModelsConfig(models, graders, path)


def _sampling_of(s):
    s = s or {}
    t, n, r = s.get("temperature"), s.get("max_tokens"), s.get("reasoning")
    return Sampling(None if t in (None, VENDOR_DEFAULT) else float(t), None if n in (None, VENDOR_DEFAULT) else n,
                    REASONING[VENDOR_DEFAULT if r is None else r], bool(s.get("json")))
