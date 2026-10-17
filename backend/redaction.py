"""Redaction utilities for masking account-like and phone-like strings before snapshot storage."""

from __future__ import annotations

import re
from typing import Any


PHONE_TOKEN = "[REDACTED_PHONE]"
ACCOUNT_TOKEN = "[REDACTED_ACCOUNT]"

# Phone-like patterns:
# - International format: +44 7700 900123, +1-555-234-5678, +44 (0)20 7946 0921
# - UK parenthesized or spaced/hyphenated landline/mobile: (020) 7946 0921, 07700 900456, 020-7946-0192
# - North American format: (555) 234-5678, 555-234-5678
_PHONE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?<!\w)\+\d{1,3}[\s.-]?(?:\(0\)|\(?\d{2,4}\)?)[\s.-]?(?:\d{3,4}[\s.-]?\d{3,4}|\d{6})(?!\w)"
    ),
    re.compile(r"(?<!\w)\(0\d{2,4}\)[\s.-]?(?:\d{3,4}[\s.-]?\d{3,4}|\d{6})(?!\w)"),
    re.compile(r"\b0\d{2,4}[\s.-](?:\d{3,4}[\s.-]\d{3,4}|\d{6})\b"),
    re.compile(r"(?<!\w)\(\d{3}\)[\s.-]?\d{3}[\s.-]\d{4}(?!\w)"),
    re.compile(r"\b\d{3}-\d{3}-\d{4}\b"),
]

# Account-like patterns:
# - IBANs: e.g., GB29NWBK60161331926819
# - Labeled account references: e.g., ACCT-4491-8820-1134, Account #83920144
# - Multi-group 12-16 digit card/account numbers: 4491-8820-1134, 4532 0151 1283 0366
# - UK sort codes: 20-45-19
# - Contiguous 8-16 digit account numbers: 83920144, 00192837465
_ACCOUNT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    re.compile(r"\b\d{4}[\s-]\d{4}[\s-]\d{4}(?:[\s-]\d{4})?\b"),
    re.compile(r"\b\d{2}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{8,16}\b"),
]


def redact_text(text: str) -> str:
    """Mask phone-like strings and account-like numbers in free text."""
    if not text:
        return text

    redacted = text
    for pattern in _ACCOUNT_PATTERNS:
        redacted = pattern.sub(ACCOUNT_TOKEN, redacted)
    for pattern in _PHONE_PATTERNS:
        redacted = pattern.sub(PHONE_TOKEN, redacted)
    return redacted


def contains_unredacted_sensitive_patterns(text: str) -> bool:
    """Return True if any phone-like or account-like pattern remains in text."""
    for pattern in _PHONE_PATTERNS:
        if pattern.search(text):
            return True
    for pattern in _ACCOUNT_PATTERNS:
        if pattern.search(text):
            return True
    return False


def redact_note_for_snapshot(note_payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied note payload with free-text fields redacted for snapshot storage."""
    result: dict[str, Any] = {}
    for key, value in note_payload.items():
        if isinstance(value, str) and key in {"note_text", "text", "body", "comment", "note"}:
            result[key] = redact_text(value)
        elif isinstance(value, dict):
            result[key] = redact_note_for_snapshot(value)
        elif isinstance(value, list):
            result[key] = [
                redact_note_for_snapshot(item)
                if isinstance(item, dict)
                else (redact_text(item) if isinstance(item, str) else item)
                for item in value
            ]
        else:
            result[key] = value
    return result

