"""One `chat()` call for every model this eval talks to.

A model is named as `provider:model`, for example `claude:claude-opus-5-5`, `ollama:gemma4:12b`
or `deepseek:deepseek-flash`. Everything after the first colon is the model name, so Ollama tags
with their own colon work.

Keys come from the environment or an untracked `.env` at the repository root, and are never
printed or written into results.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = ("claude", "ollama", "deepseek")
FAMILY = {"claude": "anthropic", "ollama": "ollama-model", "deepseek": "deepseek"}


class CallError(RuntimeError):
    """The call failed: network, HTTP status, refusal, or an empty completion."""


def parse_target(target: str) -> tuple[str, str]:
    provider, sep, model = target.partition(":")
    if not sep or provider not in PROVIDERS or not model:
        raise ValueError(f"expected provider:model with provider in {PROVIDERS}, got {target!r}")
    return provider, model


def family(target: str) -> str:
    """The model family, for README rule 5 (the grader is never the family under test)."""
    provider, model = parse_target(target)
    if provider == "ollama":
        # Ollama serves many families; the tag's first word is close enough (gemma4, qwen3.5, ...)
        return model.split(":")[0].rstrip("0123456789.")
    return FAMILY[provider]


def _key(var: str) -> str:
    if os.environ.get(var):
        return os.environ[var]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == var:
                return value.strip().strip("\"'")
    raise CallError(f"{var} is not set (environment or .env at the repo root)")


def chat(target: str, messages: list[dict], *, system: str | None = None, json_mode: bool = False,
         max_tokens: int = 16000, effort: str | None = None) -> dict:
    """One completion. Returns {'text', 'model', 'usage', 'latency_s', 'settings'}; raises CallError."""
    provider, model = parse_target(target)
    start = time.monotonic()
    if provider == "claude":
        out = _claude(model, messages, system, max_tokens, effort)
    elif provider == "ollama":
        out = _ollama(model, messages, system, json_mode)
    else:
        out = _deepseek(model, messages, system, json_mode, max_tokens)
    out["latency_s"] = round(time.monotonic() - start, 2)
    if not out["text"].strip():
        raise CallError(f"{target} returned an empty completion ({out.get('stop_reason')})")
    return out


def _claude(model: str, messages: list[dict], system: str | None, max_tokens: int, effort: str | None) -> dict:
    import anthropic

    # No refusal fallback on purpose: a fallback would answer with a different model, and every
    # result must carry the model that actually wrote it. A refusal is recorded as a failed call.
    kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system
    if effort:
        kwargs["output_config"] = {"effort": effort}
    try:
        api_key = _key("ANTHROPIC_API_KEY")
    except CallError:
        api_key = None  # let the SDK try its other credential sources (ANTHROPIC_AUTH_TOKEN, `ant auth login`)
    try:
        resp = anthropic.Anthropic(api_key=api_key).messages.create(**kwargs)
    except TypeError as e:  # the SDK found no credentials at all
        raise CallError("no Anthropic credentials: set ANTHROPIC_API_KEY (environment or .env at the repo root)") from e
    except anthropic.AuthenticationError as e:
        raise CallError("Anthropic rejected the credentials: set ANTHROPIC_API_KEY (environment or .env)") from e
    except anthropic.APIStatusError as e:
        raise CallError(f"Anthropic HTTP {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise CallError(f"could not reach the Anthropic API: {e}") from e
    if resp.stop_reason == "refusal":
        raise CallError(f"{model} refused (stop_details={resp.stop_details})")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return {"text": text, "model": resp.model, "stop_reason": resp.stop_reason,
            "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
            "settings": {"max_tokens": max_tokens, "effort": effort or "api default", "system_prompt": bool(system)}}


def _post(url: str, body: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise CallError(f"HTTP {e.code} from {url}: {e.read()[:300]!r}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise CallError(f"request to {url} failed: {e}") from e


def _with_system(messages: list[dict], system: str | None) -> list[dict]:
    return ([{"role": "system", "content": system}] if system else []) + messages


def _ollama(model: str, messages: list[dict], system: str | None, json_mode: bool) -> dict:
    # Gemma 4 has a thinking mode that is much slower on a laptop; it is off here. A small context
    # window (the model's default is 256K) also keeps memory and load time down.
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith("http"):
        host = "http://" + host
    options = {"temperature": 0, "num_ctx": int(os.environ.get("MI_EVAL_NUM_CTX", "16384"))}
    body = {"model": model, "messages": _with_system(messages, system), "stream": False, "think": False,
            "keep_alive": "15m", "options": options}
    if json_mode:
        body["format"] = "json"
    data = _post(f"{host}/api/chat", body, {}, timeout=600)
    return {"text": (data.get("message") or {}).get("content") or "", "model": data.get("model", model),
            "stop_reason": data.get("done_reason"),
            "usage": {"input_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count")},
            "settings": {**options, "think": False, "format": body.get("format")}}


def _deepseek(model: str, messages: list[dict], system: str | None, json_mode: bool, max_tokens: int) -> dict:
    # DeepSeek's thinking mode is on by default there, bills reasoning as output and ignores
    # temperature; it is off here, as in the team's item-test runs.
    body = {"model": model, "messages": _with_system(messages, system), "temperature": 0,
            "max_tokens": min(max_tokens, 8000), "thinking": {"type": "disabled"}}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = _post("https://api.deepseek.com/chat/completions", body,
                 {"Authorization": f"Bearer {_key('DEEPSEEK_API_KEY')}"}, timeout=180)
    choice = (data.get("choices") or [{}])[0]
    usage = data.get("usage") or {}
    return {"text": (choice.get("message") or {}).get("content") or "", "model": data.get("model", model),
            "stop_reason": choice.get("finish_reason"),
            "usage": {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")},
            "settings": {"temperature": 0, "thinking": "disabled", "json_mode": json_mode}}
