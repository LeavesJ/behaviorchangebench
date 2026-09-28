"""Stable machine interfaces. These strings and keys feed tables, dashboards and the report; a
change here must be deliberate, versioned, and reflected in this test."""

from __future__ import annotations

import pytest

from bcb.config import load_config
from bcb.errors import ConfigError, ReasonCode
from bcb.packets import LABEL_COLUMNS
from bcb.providers import parse_model_config
from conftest import REPO


def test_reason_codes_are_canonical():
    assert {c.value for c in ReasonCode} == {
        "provider_error", "provider_refusal", "reply_truncated", "reply_empty",
        "grader_provider_error", "grader_refusal", "grader_truncated", "grader_parse_failure",
        "grader_schema_violation", "quote_missing", "quote_not_in_reply", "na_code_invalid",
        "cannot_determine_non_gate",
    }
    assert all(c.value == c.value.lower() and " " not in c.value for c in ReasonCode)


def test_label_columns_are_stable():
    assert LABEL_COLUMNS == ("reply_id", "checkpoint", "item_id", "item_version", "double_coded",
                             "verdict", "quote", "reason", "unsure", "labeler", "labeled_at")


def test_model_config_fails_fast():
    base = {"slot": "M1", "family": "x", "provider": "openai_compat", "model_id": "m", "base_url": None,
            "api_key_env": "K", "token_limit_param": "max_tokens"}
    parse_model_config(base, "t")
    for change, message in (
        ({"model_id": "TK"}, "still TK"),
        ({"provider": "vertex"}, "not in"),
        ({"base_url": "TK"}, "base_url is still TK"),
        ({"api_key_env": None}, "api_key_env"),
        ({"token_limit_param": "tokens"}, "token_limit_param"),
        ({"params": {"messages": []}}, "may not set"),
        ({"temperature": 0}, "unknown keys"),
    ):
        with pytest.raises(ConfigError, match=message):
            parse_model_config({**base, **change}, "t")
    no_url = dict(base)
    del no_url["base_url"]
    with pytest.raises(ConfigError, match="explicit `base_url`"):
        parse_model_config(no_url, "t")


def test_real_config_refuses_until_models_are_pinned():
    cfg = load_config(REPO / "configs" / "run.vaping-v0.yaml")
    with pytest.raises(ConfigError, match="still TK"):
        cfg.models()
    with pytest.raises(ConfigError, match="still TK"):
        cfg.grader("primary")


def test_judge_family_never_under_test(tmp_path):
    raw = (REPO / "configs" / "run.smoke.yaml").read_text().replace("family: mock-judge", "family: mock-a")
    path = tmp_path / "run.yaml"
    path.write_text(raw)
    with pytest.raises(ConfigError, match="under test"):
        load_config(path).grader("primary")
