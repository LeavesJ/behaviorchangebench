"""Model access. One narrow interface, three implementations:

- `anthropic`      the official Anthropic SDK (Messages API)
- `openai_compat`  the official OpenAI SDK against any chat-completions endpoint named by an
                   explicit `base_url` (OpenAI itself, DeepSeek, open-weight hosts)
- `mock`           deterministic offline stand-ins for tests and smoke runs; never a real result

Every request parameter we send is recorded with the completion. There are no fallbacks: a
refusal, a truncation or an error comes back as such and the caller records it. Server-side
model fallbacks are deliberately not enabled, because a reply from a different model than the
one under test would corrupt the comparison.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from .errors import ConfigError
from .util import sha256_json, word_count

# Normalized stop reasons. The raw provider value is always recorded next to it.
STOP_END = "end"
STOP_TRUNCATED = "truncated"
STOP_REFUSAL = "refusal"
STOP_OTHER = "other"

PROVIDERS = ("anthropic", "openai_compat", "mock")
MOCK_BEHAVIORS = ("reply", "grader", "generator")
UNSET = "TK"  # the spec's marker for an open choice

Message = Mapping[str, str]


@dataclass(frozen=True)
class ModelConfig:
    slot: str
    family: str
    provider: str
    model_id: str
    base_url: str | None = None
    api_key_env: str | None = None
    max_output_tokens: int = 16000
    token_limit_param: str = "max_tokens"
    params: Mapping[str, Any] = field(default_factory=dict)
    timeout_s: float = 600.0
    max_retries: int = 2
    mock: str | None = None

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"

    def describe(self) -> dict[str, Any]:
        """What the run manifest and every row record about this model. Never a secret."""
        return {
            "slot": self.slot,
            "family": self.family,
            "provider": self.provider,
            "model_id": self.model_id,
            "base_url": self.base_url,
            "max_output_tokens": self.max_output_tokens,
            "token_limit_param": self.token_limit_param,
            "params": dict(self.params),
            "max_retries": self.max_retries,
            "mock": self.mock,
        }


_KNOWN_KEYS = {
    "slot", "family", "provider", "model_id", "base_url", "api_key_env", "max_output_tokens",
    "token_limit_param", "params", "timeout_s", "max_retries", "mock",
}


def parse_model_config(raw: Any, where: str, slot: str | None = None) -> ModelConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a mapping")
    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
    slot = raw.get("slot", slot)
    for key, value in (("slot", slot), ("family", raw.get("family")), ("provider", raw.get("provider")),
                       ("model_id", raw.get("model_id"))):
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{where}: `{key}` is required")
    if raw["model_id"] == UNSET:
        raise ConfigError(f"{where}: model_id is still {UNSET}; pin an exact model ID in the config")
    provider = raw["provider"]
    if provider not in PROVIDERS:
        raise ConfigError(f"{where}: provider {provider!r} not in {PROVIDERS}")
    if provider == "openai_compat":
        if "base_url" not in raw:
            raise ConfigError(f"{where}: openai_compat needs an explicit `base_url` (null means the SDK default)")
        if raw["base_url"] == UNSET:
            raise ConfigError(f"{where}: base_url is still {UNSET}")
        if not raw.get("api_key_env"):
            raise ConfigError(f"{where}: openai_compat needs `api_key_env`, the name of the env var holding the key")
        if raw.get("token_limit_param") not in ("max_tokens", "max_completion_tokens"):
            raise ConfigError(f"{where}: `token_limit_param` must be max_tokens or max_completion_tokens")
    if provider == "mock" and raw.get("mock") not in MOCK_BEHAVIORS:
        raise ConfigError(f"{where}: mock provider needs `mock` in {MOCK_BEHAVIORS}")
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise ConfigError(f"{where}: `params` must be a mapping")
    reserved = {"model", "messages", "system", "max_tokens", "max_completion_tokens", "stream"} & set(params)
    if reserved:
        raise ConfigError(f"{where}: `params` may not set {sorted(reserved)}")
    max_out = raw.get("max_output_tokens", 16000)
    if not isinstance(max_out, int) or max_out <= 0:
        raise ConfigError(f"{where}: `max_output_tokens` must be a positive integer")
    return ModelConfig(
        slot=slot,
        family=raw["family"],
        provider=provider,
        model_id=raw["model_id"],
        base_url=raw.get("base_url"),
        api_key_env=raw.get("api_key_env"),
        max_output_tokens=max_out,
        token_limit_param=raw.get("token_limit_param", "max_tokens"),
        params=params,
        timeout_s=float(raw.get("timeout_s", 600.0)),
        max_retries=int(raw.get("max_retries", 2)),
        mock=raw.get("mock"),
    )


@dataclass(frozen=True)
class Completion:
    text: str
    stop: str
    raw_stop: str | None
    stop_detail: str | None
    response_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_s: float
    content_types: tuple[str, ...]


class Provider(Protocol):
    config: ModelConfig

    def complete(self, messages: Sequence[Message], system: str | None) -> Completion: ...


def make_provider(config: ModelConfig) -> Provider:
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    if config.provider == "openai_compat":
        return OpenAICompatProvider(config)
    return MockProvider(config)


class AnthropicProvider:
    """Official SDK. Credentials: the env var named by `api_key_env` if set, otherwise the SDK's
    own resolution (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile)."""

    _STOPS = {"end_turn": STOP_END, "max_tokens": STOP_TRUNCATED,
              "model_context_window_exceeded": STOP_TRUNCATED, "refusal": STOP_REFUSAL}

    def __init__(self, config: ModelConfig):
        import anthropic

        self.config = config
        kwargs: dict[str, Any] = {"timeout": config.timeout_s, "max_retries": config.max_retries}
        if config.api_key_env:
            kwargs["api_key"] = _required_env(config.api_key_env, config.slot)
        if config.base_url:
            kwargs["base_url"] = config.base_url
        self._client = anthropic.Anthropic(**kwargs)

    def complete(self, messages: Sequence[Message], system: str | None) -> Completion:
        c = self.config
        request: dict[str, Any] = {"model": c.model_id, "max_tokens": c.max_output_tokens,
                                   "messages": [dict(m) for m in messages], **c.params}
        if system is not None:
            request["system"] = system
        t0 = time.monotonic()
        resp = self._client.messages.create(**request)
        latency = time.monotonic() - t0
        text = "".join(b.text for b in resp.content if b.type == "text")
        detail = None
        if resp.stop_reason == "refusal" and resp.stop_details is not None:
            detail = f"category={resp.stop_details.category}"
        return Completion(
            text=text,
            stop=self._STOPS.get(resp.stop_reason or "", STOP_OTHER),
            raw_stop=resp.stop_reason,
            stop_detail=detail,
            response_model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            latency_s=round(latency, 3),
            content_types=tuple(b.type for b in resp.content),
        )


class OpenAICompatProvider:
    _STOPS = {"stop": STOP_END, "length": STOP_TRUNCATED, "content_filter": STOP_REFUSAL}

    def __init__(self, config: ModelConfig):
        import openai

        self.config = config
        kwargs: dict[str, Any] = {"api_key": _required_env(config.api_key_env or "", config.slot),
                                  "timeout": config.timeout_s, "max_retries": config.max_retries}
        if config.base_url is not None:
            kwargs["base_url"] = config.base_url
        self._client = openai.OpenAI(**kwargs)

    def complete(self, messages: Sequence[Message], system: str | None) -> Completion:
        c = self.config
        msgs = ([{"role": "system", "content": system}] if system is not None else []) + [dict(m) for m in messages]
        request: dict[str, Any] = {"model": c.model_id, "messages": msgs,
                                   c.token_limit_param: c.max_output_tokens, **c.params}
        t0 = time.monotonic()
        resp = self._client.chat.completions.create(**request)
        latency = time.monotonic() - t0
        if not resp.choices:
            return Completion("", STOP_OTHER, None, "no choices returned", resp.model, None, None,
                              round(latency, 3), ())
        choice = resp.choices[0]
        refusal = getattr(choice.message, "refusal", None)
        stop = self._STOPS.get(choice.finish_reason or "", STOP_OTHER)
        if refusal:
            stop = STOP_REFUSAL
        usage = resp.usage
        return Completion(
            text=choice.message.content or "",
            stop=stop,
            raw_stop=choice.finish_reason,
            stop_detail=refusal,
            response_model=resp.model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            latency_s=round(latency, 3),
            content_types=("text",),
        )


class MockProvider:
    """Deterministic stand-ins keyed on a hash of the request. Output is plausible in shape only.

    - reply:     a short assistant turn that differs per checkpoint
    - grader:    a valid verdict JSON for the reply between <reply> tags (met with a real quote,
                 or not_met), so the grading contract is exercised end to end
    - generator: fills each requested turn: user turns keep their draft, assistant turns get
                 filler text at the minimum word count
    """

    def __init__(self, config: ModelConfig):
        self.config = config
        self._fn: Callable[[Sequence[Message], str | None], str] = {
            "reply": _mock_reply, "grader": _mock_grader, "generator": _mock_generator,
        }[config.mock or ""]

    def complete(self, messages: Sequence[Message], system: str | None) -> Completion:
        text = self._fn(messages, system)
        return Completion(text, STOP_END, "end_turn", None, self.config.model_id,
                          sum(word_count(m["content"]) for m in messages), word_count(text), 0.0, ("text",))


def _required_env(name: str, slot: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{slot}: environment variable {name} is not set")
    return value


def _mock_reply(messages: Sequence[Message], system: str | None) -> str:
    h = sha256_json([system, list(map(dict, messages))])[:10]
    return (
        f"That sounds like a lot to carry right now (ref {h}). It makes sense that part of you wants "
        "to change this and part of you is not sure yet. What feels most important to you about it?"
    )


_REPLY_BLOCK = re.compile(r"<reply>\n(.*?)\n</reply>", re.S)


def _mock_grader(messages: Sequence[Message], system: str | None) -> str:
    prompt = messages[-1]["content"]
    m = _REPLY_BLOCK.search(prompt)
    reply = m.group(1) if m else ""
    words = reply.split()
    if int(sha256_json(prompt)[:8], 16) % 2 == 0 and words:
        out = {"reasoning": "Mock grader: the reply opens with the quoted span.",
               "quote": " ".join(words[:6]), "verdict": "met", "na_code": None}
    else:
        out = {"reasoning": "Mock grader: treated as absent.", "quote": None, "verdict": "not_met", "na_code": None}
    return json.dumps(out)


def _mock_generator(messages: Sequence[Message], system: str | None) -> str:
    payload = json.loads(messages[-1]["content"])
    out = []
    for t in payload["this_session"]["turns"]:
        if not t.get("write"):
            continue
        if t["role"] == "user":
            out.append({"id": t["id"], "text": t["draft"], "facts": []})
        else:
            lo = t["words"][0]
            filler = ("That makes sense and thank you for telling me how this has been going for you. " * 20).split()
            out.append({"id": t["id"], "text": " ".join(filler[:lo]), "facts": []})
    return json.dumps({"turns": out})
