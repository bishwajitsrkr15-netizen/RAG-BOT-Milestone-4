"""Deterministic privacy and intent checks for FAQ questions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    FACTUAL = "factual"
    ADVICE = "advice"
    PERFORMANCE = "performance"
    PERSONAL_DATA = "personal_data"


@dataclass(frozen=True)
class GuardResult:
    intent: Intent
    reason: str | None = None


SENSITIVE_PATTERNS = (
    re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:\d[\s-]?){11}\d(?!\d)"),
    re.compile(r"\b\d{4,8}\s*(?:is\s*)?(?:otp|one[- ]time password)\b", re.IGNORECASE),
    re.compile(r"\b(?:otp|one[- ]time password)\s*(?:is|:|=)?\s*\d{4,8}\b", re.IGNORECASE),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"),
    re.compile(
        r"\b(?:account|a/c)\s*(?:number|no\.?|#)?\s*[:=-]?\s*\d{6,20}\b",
        re.IGNORECASE,
    ),
)

PERFORMANCE_TERMS = (
    "return",
    "returns",
    "cagr",
    "annualized",
    "annualised",
    "outperform",
    "performance comparison",
    "compare performance",
    "best performing",
)

ADVICE_PATTERNS = (
    re.compile(r"\bshould\s+i\b", re.IGNORECASE),
    re.compile(r"\b(?:buy|sell|hold)\s+(?:this|the|a|an|hdfc)\b", re.IGNORECASE),
    re.compile(r"\b(?:recommend|recommendation|advise|advice)\b", re.IGNORECASE),
    re.compile(r"\b(?:which|what)\s+(?:fund|scheme)\s+(?:should|must)\s+i\b", re.IGNORECASE),
    re.compile(r"\b(?:best|suitable)\s+(?:fund|scheme|portfolio)\s+for\s+me\b", re.IGNORECASE),
    re.compile(r"\bmy\s+portfolio\b", re.IGNORECASE),
)


def inspect_question(question: str) -> GuardResult:
    if not isinstance(question, str) or not question.strip():
        return GuardResult(Intent.FACTUAL, "empty_question")
    if any(pattern.search(question) for pattern in SENSITIVE_PATTERNS):
        return GuardResult(Intent.PERSONAL_DATA, "sensitive_identifier_detected")

    lowered = question.casefold()
    if any(term in lowered for term in PERFORMANCE_TERMS):
        return GuardResult(Intent.PERFORMANCE, "performance_request")
    if any(pattern.search(question) for pattern in ADVICE_PATTERNS):
        return GuardResult(Intent.ADVICE, "investment_advice_request")
    return GuardResult(Intent.FACTUAL)
