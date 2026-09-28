"""Fail-fast errors and the canonical reason codes every step records.

`reason_code` strings are stable and machine facing: tables, the run manifest and the
disagreement report key off them. Change one only with a test for the new mapping.
"""

from __future__ import annotations

from enum import StrEnum


class ReasonCode(StrEnum):
    """Why a reply or a grade is not a usable verdict. Never counted as a model failure."""

    # step 2: collecting replies
    PROVIDER_ERROR = "provider_error"
    PROVIDER_REFUSAL = "provider_refusal"
    REPLY_TRUNCATED = "reply_truncated"
    REPLY_EMPTY = "reply_empty"
    # step 3: grading
    GRADER_PROVIDER_ERROR = "grader_provider_error"
    GRADER_REFUSAL = "grader_refusal"
    GRADER_TRUNCATED = "grader_truncated"
    GRADER_PARSE_FAILURE = "grader_parse_failure"
    GRADER_SCHEMA_VIOLATION = "grader_schema_violation"
    QUOTE_MISSING = "quote_missing"
    QUOTE_NOT_IN_REPLY = "quote_not_in_reply"
    NA_CODE_INVALID = "na_code_invalid"
    CANNOT_DETERMINE_NON_GATE = "cannot_determine_non_gate"


class BcbError(Exception):
    """Base class. The message is human; `reason_code` is the stable machine string."""

    reason_code = "bcb_error"


class SpecError(BcbError):
    reason_code = "spec_invalid"

    def __init__(self, source: str, problems: list[str]):
        self.problems = list(problems)
        listed = "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(f"{source}: {len(self.problems)} problem(s)\n{listed}")


class ConfigError(BcbError):
    reason_code = "config_invalid"


class DataError(BcbError):
    reason_code = "data_invalid"
