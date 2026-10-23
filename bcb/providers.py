"""The models config at run time: whom a run calls, with which settings, and whether its grader may grade them.

    python -m bcb.pilot --models-config trajectories/vaping-v0/v2/models.yaml                # its models, its primary
    python -m bcb.pilot --models-config ... --models grok-4.7 --grader second                  # names the file gives
    python -m bcb.pilot --models-config ... --grader deepseek:deepseek-flash                   # one it does not list

A run names a model under test or a grader by the name the models config gives it (gpt-6-astra, primary) or as
provider:model. One the config lists is called with that entry's own settings and, for a model under test, its system
prompt. A provider:model the config does not list is called as the runners called every model before the config
existed: a model at the vendor's temperature and reasoning with max_tokens 16000, a grader with `sampling` below. The
flags that pin reasoning (--reasoning for the models, --grader-reasoning for the grader) still win over the config,
so the Makefile's REASONING and GRADER_REASONING keep working as overrides.

README rule 5, a grader never grades its own family, is checked when a run starts, before anything is called.
bcb.models_config already refuses a clash inside one file; here it covers what the file cannot see: a model or grader
given on the command line, and replies on disk from an earlier draw. Each is judged by the family its entry declares
and the vendor its model name shows, as bcb.models_config.families reads them.
"""

import hashlib
from dataclasses import replace
from pathlib import Path

from . import client, models_config
from .models_config import Grader, Model, Sampling

ROOT = Path(__file__).resolve().parent.parent
REPLY_MAX_TOKENS = 16000  # high enough that hidden reasoning never truncates a reply (spec run.sampling)
SAMPLING = {"temperature": 0.0, "max_tokens": 1000, "json_mode": True, "thinking": False}  # recorded in every manifest
REASONING = {"off": False, "on": True, "vendor default": None}  # the choices of --reasoning and --grader-reasoning
REASONING_MAX_TOKENS = 8000  # hidden reasoning counts toward max_tokens
PRIMARY = "primary"  # the grader a run calls when it names none


def sampling(reasoning="off"):
    """The call settings of a grader named as provider:model. Reasoning stays off unless asked, since some graders
    refuse it off (Opus 5.5 on OpenRouter); with it on, the vendor's temperature applies (Anthropic accepts no other)
    and max_tokens has room."""
    if reasoning == "off":
        return dict(SAMPLING)
    return dict(SAMPLING, thinking=REASONING[reasoning], temperature=None, max_tokens=REASONING_MAX_TOKENS)


# ---------- whom a run calls ----------

def model_under_test(name, config=None, reasoning=None):
    """The entry for a model under test, found by its name in the config or its provider:model. `reasoning` ("on",
    "off" or "vendor default"), when the run pins it, replaces the entry's own."""
    entry = _find(name, config, "model")
    if entry is None:
        provider, model = client.parse_target(name)
        entry = Model(name, provider, model, vendor(provider, model), Sampling(None, REPLY_MAX_TOKENS, None))
    if reasoning is not None:
        entry = replace(entry, sampling=replace(entry.sampling, reasoning=REASONING[reasoning]))
    return entry


def models_for_run(config, names=None, reasoning=None):
    """The models a run draws from, in order: those `names` lists, else every model in the config. A run keeps each
    reply under its provider:model, so two entries that call the same one cannot share a run."""
    chosen = names or ([m.id for m in config.models] if config else [])
    entries = [model_under_test(n, config, reasoning) for n in chosen]
    seen = {}
    for e in entries:
        if e.target in seen:
            raise ValueError(f"models {seen[e.target].id} and {e.id} both call {e.target}; a run keeps each reply under "
                             "its provider:model, so draw them in separate runs")
        seen[e.target] = e
    return entries


def grader_for_run(config, name=None, reasoning=None):
    """The grader a run calls: the one `name` points at, or the config's primary when no name is given; None when
    there is neither. One the config lists keeps its own settings unless `reasoning` is given, which gives it the
    settings `sampling` gives a provider:model grader, as --grader-reasoning always has."""
    if name is None:
        if config is None or not config.graders:
            return None
        if PRIMARY not in config.graders:
            raise ValueError(f"{config.path} has no grader called {PRIMARY}, the one a run calls when it names none; "
                             f"name one with --grader ({', '.join(config.graders)})")
        name = PRIMARY
    entry = _find(name, config, "grader")
    if entry is None:
        provider, model = client.parse_target(name)
        entry, reasoning = Grader(None, provider, model, vendor(provider, model)), reasoning or "off"
    if reasoning is not None:
        s = sampling(reasoning)
        entry = replace(entry, sampling=Sampling(s["temperature"], s["max_tokens"], s["thinking"], s["json_mode"]))
    return entry


def _find(name, config, kind):
    """The config's entry a name points at, or None for a provider:model it does not list. A name without a colon is
    the config's own name for the entry, and must be there."""
    entries = () if config is None else config.models if kind == "model" else tuple(config.graders.values())
    if ":" in name:
        return next((e for e in entries if e.target == name), None)
    named = {e.id if kind == "model" else e.name: e for e in entries}
    if name in named:
        return named[name]
    if config is None:
        raise ValueError(f"{kind} {name!r} is not written as provider:model, and no models config was given to look "
                         "the name up in (--models-config)")
    raise ValueError(f"{kind} {name!r} is not in {config.path} (its {kind}s: {', '.join(named) or 'none'}); write "
                     f"provider:model for a {kind} it does not list")


# ---------- what a call is given ----------

def reply_settings(model):
    """What bcb.client.chat is given for a model under test; None leaves that setting to the vendor."""
    s = model.sampling
    return {"temperature": s.temperature, "max_tokens": s.max_tokens, "thinking": s.reasoning}


def grader_settings(entry):
    """What bcb.client.chat is given for a grader, keyed and ordered as manifests have always recorded it."""
    s = entry.sampling
    return {"temperature": s.temperature, "max_tokens": s.max_tokens, "json_mode": s.json, "thinking": s.reasoning}


def messages(model, conversation):
    """The messages a model under test is sent: its system prompt, if its entry has one, then the conversation."""
    if model is None or model.system_prompt is None:
        return conversation
    return [{"role": "system", "content": model.system_prompt}] + conversation


# ---------- README rule 5 ----------

def vendor(provider, model):
    """The family a provider:model shows: for openrouter the part before '/', else the provider (bcb.generate.family)."""
    return model.split("/", 1)[0].lower() if provider == "openrouter" else provider


def families(entry):
    return models_config.families({"provider": entry.provider, "model": entry.model, "family": entry.family})


def same_family(graders, models):
    """One line for each grader and model under test that share a family; empty when none do."""
    out = []
    for g in graders:
        for m in models:
            shared = families(g) & families(m)
            if shared:
                who = g.target if g.name is None else f"{g.name} ({g.target})"
                out.append(f"grader {who} is in the {min(shared)} family, and so is model {m.target}; a grader never "
                           "grades its own family (README rule 5)")
    return out


# ---------- what the manifest records ----------

def config_record(config):
    """Which models config a run read, with its full hash; None when it read none."""
    if config is None:
        return None
    path = Path(config.path).resolve()
    return {"path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def model_record(model, config):
    """One model under test as the manifest records it: its entry, where it came from, and what it was sent."""
    listed = config is not None and any(m.target == model.target for m in config.models)
    return {"id": model.id, "family": model.family, "from": "models config" if listed else "command line",
            "sent": reply_settings(model), "system_prompt": model.system_prompt}


def reply_sampling(models):
    """The manifest's reply_sampling as runs have always written it, when every model was sent the same settings;
    None when they differ, since model_settings then records each one's."""
    said = [_in_words(m.sampling) for m in models]
    return said[0] if said and all(s == said[0] for s in said) else None


def _in_words(s):
    default = "vendor default"
    return {"temperature": default if s.temperature is None else s.temperature,
            "max_tokens": default if s.max_tokens is None else s.max_tokens,
            "reasoning": {True: "on", False: "off", None: default}[s.reasoning]}
