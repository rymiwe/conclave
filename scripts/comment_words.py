#!/usr/bin/env python3
"""Words that must not appear in a public review comment.

The list lives here. Prompts name check-comment-links.py instead of
copying it. Vendor names stay in review_forbidden_word; this reads that
list and does not merge it into the phrases below.
"""
from __future__ import annotations

import re
from pathlib import Path

# Longer phrases first so "chair repro" wins over "chair".
PHRASES: tuple[tuple[str, str], ...] = (
    ("chair repro", "I reproduced this locally against a scripted upstream"),
    ("multi-vendor", "independent reviews"),
    ("conclave", "this review"),
    ("council", "this review"),
    ("ballot", "the set of reviews"),
    ("blinded", "unlabeled"),
    ("voter", "reviewer"),
    ("chair", "the person who ran this"),
    ("gate", "the decision"),
    ("leg", "reviewer"),
    ("Jev", "an outside reader"),
)

VENDOR_SAY = "name what happened, not the product"

_ROOT = Path(__file__).resolve().parent


def vendor_pattern() -> re.Pattern[str]:
    """The same two lists review_forbidden_word greps for."""
    text = (_ROOT / "review-kind.sh").read_text(encoding="utf-8")
    anycase = re.search(r"grep -Eio '\\b\(([^']+)\)\\b'", text)
    names = re.search(r"grep -Eo '\\b\(([^']+)\)\\b'", text)
    if anycase is None or names is None:
        raise RuntimeError("review_forbidden_word list is missing from review-kind.sh")
    return re.compile(
        r"(?i:\b(?:" + anycase.group(1) + r")\b)|\b(?:" + names.group(1) + r")\b"
    )


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(not (end <= a or start >= b) for a, b in taken)


def word_hits(text: str) -> list[tuple[int, str, str]]:
    """(line, matched text, plain replacement), longer phrases first."""
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, str, str]] = []
    for phrase, replacement in PHRASES:
        rx = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
        for match in rx.finditer(text):
            if _overlaps(match.span(), taken):
                continue
            taken.append(match.span())
            line = text.count("\n", 0, match.start()) + 1
            found.append((line, match.group(0), replacement))
    for match in vendor_pattern().finditer(text):
        if _overlaps(match.span(), taken):
            continue
        taken.append(match.span())
        line = text.count("\n", 0, match.start()) + 1
        found.append((line, match.group(0), VENDOR_SAY))
    found.sort(key=lambda item: (item[0], item[1]))
    return found


def word_problems(text: str) -> list[str]:
    return [
        f"line {line}: public comment contains \"{span}\"; say \"{say}\"."
        for line, span, say in word_hits(text)
    ]
