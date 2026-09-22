"""Call an OpenAI-compatible chat endpoint (DeepSeek, OpenRouter, ...) with the standard library only.

Keys come from the environment, or from an untracked `.env` at the repository root. They are
never printed, logged or written into results.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

PROVIDERS = {
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
}
ROOT = Path(__file__).resolve().parent.parent


class CallError(RuntimeError):
    """The API call failed: network, HTTP status, or an empty completion."""


def _key(var: str) -> str:
    if os.environ.get(var):
        return os.environ[var]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == var:
                return value.strip().strip("\"'")
    raise CallError(f"{var} is not set (environment or {env.name}); see README 'Running the item tests'")


def parse_target(target: str) -> tuple[str, str]:
    """'deepseek:deepseek-flash' -> ('deepseek', 'deepseek-flash')."""
    provider, sep, model = target.partition(":")
    if not sep or provider not in PROVIDERS or not model:
        raise ValueError(f"expected provider:model with provider in {sorted(PROVIDERS)}, got {target!r}")
    return provider, model


def chat(target: str, messages: list[dict], *, temperature=0.0, max_tokens=1000, json_mode=False, thinking=False,
         timeout=120) -> dict:
    """One completion. Returns {'text', 'model', 'usage', 'latency_s'}; raises CallError."""
    provider, model = parse_target(target)
    base, var = PROVIDERS[provider]
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if provider == "deepseek":
        # thinking is on by default there, bills reasoning as output, and ignores temperature
        body["thinking"] = {"type": "enabled" if thinking else "disabled"}
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {_key(var)}", "Content-Type": "application/json"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise CallError(f"{provider} HTTP {e.code}: {e.read()[:300]!r}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise CallError(f"{provider} request failed: {e}") from e
    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    if not text.strip():
        raise CallError(f"{provider} returned an empty completion (finish_reason={choice.get('finish_reason')})")
    return {"text": text, "model": data.get("model", model), "usage": data.get("usage", {}),
            "latency_s": round(time.monotonic() - start, 2)}
