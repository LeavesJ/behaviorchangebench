"""The real adapters against a local fake endpoint: checks what we send and how we read the
response through the official SDKs, with no network and no keys."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bcb.providers import STOP_END, STOP_REFUSAL, STOP_TRUNCATED, make_provider, parse_model_config


class Fake:
    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.responses: list[dict] = []


@pytest.fixture()
def fake(monkeypatch):
    state = Fake()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            state.requests.append((self.path, body))
            data = json.dumps(state.responses.pop(0)).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("FAKE_KEY", "sk-test")
    state.url = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()


def _anthropic_message(text, stop="end_turn"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test",
            "content": [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": text}],
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 11, "output_tokens": 7}}


def test_anthropic_adapter(fake):
    cfg = parse_model_config({"slot": "M1", "family": "anthropic", "provider": "anthropic", "model_id": "claude-test",
                              "base_url": fake.url, "api_key_env": "FAKE_KEY", "max_output_tokens": 1234,
                              "max_retries": 0, "params": {"output_config": {"effort": "high"}}}, "t")
    prov = make_provider(cfg)
    fake.responses += [_anthropic_message("hello there"), _anthropic_message("cut", "max_tokens"),
                       _anthropic_message("", "refusal")]
    msgs = [{"role": "user", "content": "[Sat, Jan 31, 2026, 11:48 PM]\nhi"}]
    got = prov.complete(msgs, None)
    path, body = fake.requests[0]
    assert path == "/v1/messages"
    assert body == {"model": "claude-test", "max_tokens": 1234, "messages": msgs, "output_config": {"effort": "high"}}
    assert (got.text, got.stop, got.input_tokens, got.output_tokens) == ("hello there", STOP_END, 11, 7)
    assert got.content_types == ("thinking", "text")
    assert prov.complete(msgs, "sys").stop == STOP_TRUNCATED and fake.requests[1][1]["system"] == "sys"
    assert prov.complete(msgs, None).stop == STOP_REFUSAL


def _chat(text, finish="stop"):
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": "m-test",
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}}


def test_openai_compat_adapter(fake):
    cfg = parse_model_config({"slot": "M2", "family": "x", "provider": "openai_compat", "model_id": "m-test",
                              "base_url": fake.url + "/v1", "api_key_env": "FAKE_KEY", "max_retries": 0,
                              "token_limit_param": "max_completion_tokens", "max_output_tokens": 500}, "t")
    prov = make_provider(cfg)
    fake.responses += [_chat("ok then"), _chat("trunc", "length")]
    got = prov.complete([{"role": "user", "content": "hi"}], "be brief")
    path, body = fake.requests[0]
    assert path == "/v1/chat/completions"
    assert body == {"model": "m-test", "max_completion_tokens": 500,
                    "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]}
    assert (got.text, got.stop, got.input_tokens, got.output_tokens) == ("ok then", STOP_END, 9, 4)
    assert prov.complete([{"role": "user", "content": "hi"}], None).stop == STOP_TRUNCATED
