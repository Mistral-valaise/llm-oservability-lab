"""Input guardrails: size limits, prompt-injection heuristics and PII redaction.

These are deliberately simple, explainable heuristics for a demo. They show
*where* such controls sit and how they become observable; they are not a
replacement for a dedicated safety model.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+(instructions|prompts|rules)",
        r"disregard\s+(the\s+)?(system|previous)\s+(prompt|instructions)",
        r"(reveal|print|show|leak)\s+(me\s+)?(your|the)\s+(system\s+prompt|instructions|hidden\s+prompt)",
        r"\byou\s+are\s+now\s+(dan|in\s+developer\s+mode)\b",
        r"\bjailbreak\b",
    )
)

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE = re.compile(r"(?<![\w+])\+?\d{1,3}[\s/-]?\(?\d{2,5}\)?[\s/-]?\d{3,5}[\s/-]?\d{2,6}\b")


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class RedactionResult:
    text: str
    counts: Counter[str] = field(default_factory=Counter)


def redact_pii(text: str) -> RedactionResult:
    """Replace e-mail, IBAN, payment card and phone numbers with placeholders."""
    counts: Counter[str] = Counter()

    def sub(pattern: re.Pattern[str], label: str, value: str, check=None) -> str:
        def repl(m: re.Match[str]) -> str:
            if check and not check(m.group(0)):
                return m.group(0)
            counts[label] += 1
            return f"[{label.upper()}]"

        return pattern.sub(repl, value)

    out = sub(_EMAIL, "email", text)
    out = sub(_IBAN, "iban", out)
    out = sub(_CARD, "card", out, check=lambda s: _luhn_ok(re.sub(r"\D", "", s)))
    out = sub(_PHONE, "phone", out, check=lambda s: len(re.sub(r"\D", "", s)) >= 8)
    return RedactionResult(out, counts)


class GuardrailViolation(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def check_prompt(text: str, max_chars: int) -> None:
    """Raise GuardrailViolation if the prompt must be rejected."""
    if len(text) > max_chars:
        raise GuardrailViolation("too_large", f"prompt exceeds {max_chars} characters")
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            raise GuardrailViolation("prompt_injection", "prompt matches a known injection pattern")
