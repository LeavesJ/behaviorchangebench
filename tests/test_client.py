import io
import json
import os
import unittest
from unittest import mock

from bcb import client


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def capture(target, **kw):
    """Call client.chat with the network replaced; return (request body, result)."""
    sent = {}

    def urlopen(req, timeout):
        sent["body"] = json.loads(req.data)
        sent["auth"] = req.get_header("Authorization")
        return FakeResponse(json.dumps({"model": "served-model", "choices": [{"message": {"content": "{}"}}],
                                        "usage": {"prompt_tokens": 1}}).encode())

    with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test-key", "OPENROUTER_API_KEY": "or-test-key"}), \
            mock.patch("urllib.request.urlopen", urlopen):
        result = client.chat(target, [{"role": "user", "content": "hi"}], **kw)
    return sent, result


class ChatTest(unittest.TestCase):
    def test_deepseek_thinking_is_off_unless_asked(self):
        # DeepSeek thinks by default: reasoning billed as output, temperature ignored
        sent, _ = capture("deepseek:deepseek-flash", temperature=0.0)
        self.assertEqual(sent["body"]["thinking"], {"type": "disabled"})
        self.assertEqual(sent["body"]["temperature"], 0.0)
        sent, _ = capture("deepseek:deepseek-flash", thinking=True)
        self.assertEqual(sent["body"]["thinking"], {"type": "enabled"})

    def test_other_providers_get_no_deepseek_field(self):
        sent, _ = capture("openrouter:some/model")
        self.assertNotIn("thinking", sent["body"])

    def test_json_mode_sets_response_format(self):
        sent, _ = capture("deepseek:deepseek-flash", json_mode=True)
        self.assertEqual(sent["body"]["response_format"], {"type": "json_object"})

    def test_result_records_the_served_model_and_never_the_key(self):
        sent, result = capture("deepseek:deepseek-flash")
        self.assertEqual(sent["auth"], "Bearer sk-test-key")
        self.assertEqual(result["model"], "served-model")
        self.assertNotIn("sk-test-key", json.dumps(result))

    def test_missing_key_is_a_call_error(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(client, "ROOT", client.ROOT / "nowhere"):
            with self.assertRaises(client.CallError):
                client.chat("deepseek:deepseek-flash", [])

    def test_bad_target(self):
        with self.assertRaises(ValueError):
            client.parse_target("deepseek-flash")


if __name__ == "__main__":
    unittest.main()
