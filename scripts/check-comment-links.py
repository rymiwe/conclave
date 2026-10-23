#!/usr/bin/env python3
"""Check that review-comment citations sit inside markdown links.

A drafted comment fails when a ticket id (APP-123), an issue reference
(#123), a bare SHA (7-40 hex digits with at least one a-f and one 0-9, so a
pure number or an all-letter hex word is not a SHA), or a file:line citation
is outside a markdown link.

Links removed before the search: [text](url), [text][ref], <https://...>
autolinks, and reference definitions ([1]: https://...). The citation may
be in the link text or the destination.

Standard tokens (UTF-8, SHA-256, ISO-8601) are not ticket ids.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

LINK_RE = re.compile(
    r"\[[^\[\]]*\]\([^)]*\)"
    r"|"
    r"\[[^\[\]]*\]\[[^\[\]]*\]"
    r"|"
    r"<https?://[^>\s]+>"
    r"|"
    r"\[[^\[\]\n]+\]:[ \t]*<?https?://[^>\s]+>?"
)

# CommonMark fence: 0-3 spaces, then three or more backticks or tildes.
# A backtick opener's info string may not contain a backtick. A closer uses
# the same character, is at least as long, and has only whitespace after it.
# An unclosed fence runs to the end of the text.
_FENCE_OPEN = re.compile(r"(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"(`+|~+)[ \t]*$")

# name:N is host:port only when the last label is an IP octet or one of these.
# Country codes that are also extensions (rs, py, md, sh, ...) are not listed,
# so main.rs:42 stays a file citation.
HOST_LABELS = frozenset(
    {
        "com",
        "net",
        "org",
        "io",
        "dev",
        "app",
        "local",
        "localhost",
        "internal",
        "test",
        "example",
        "postgres",
    }
)

# A letter, then letters or digits, a hyphen, then at least two digits.
# APP-123 and ENG2-12 match. HTTP-2 and PEP-8 do not. SHA-256 and
# ISO-8601 match that shape too, so they are allowlisted.
STANDARD_TOKENS = frozenset(
    {
        "ISO-8601",
        "SHA-1",
        "SHA-256",
        "SHA-384",
        "SHA-512",
        "UTF-8",
        "UTF-16",
        "UTF-32",
    }
)

# Ticket ids look like APP-123. Issue refs are #N, not a heading.
# An id followed by another -digit is a date tail, as in a prefixed date
# such as V1I24MF2025-04-17, not a ticket.
# A bare SHA must include a hex letter and a decimal digit, so a pure
# number and an all-letter word such as defaced or deadbeef are left alone.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ticket", re.compile(r"\b[A-Z][A-Z0-9]+-\d{2,}\b(?!-\d)")),
    ("issue", re.compile(r"(?<![A-Za-z0-9&])#\d+\b")),
    (
        "sha",
        re.compile(
            r"\b(?=[0-9a-fA-F]{7,40}\b)"
            r"(?=[0-9a-fA-F]*[a-fA-F])"
            r"(?=[0-9a-fA-F]*[0-9])"
            r"[0-9a-fA-F]{7,40}\b"
        ),
    ),
    ("file:line", re.compile(r"\b[\w./-]+\.[A-Za-z][A-Za-z0-9]{0,7}:\d+\b")),
    (
        "file:line",
        re.compile(r"\b(?:Gemfile|Rakefile|Dockerfile):\d+\b"),
    ),
)


def link_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in LINK_RE.finditer(text)]


def fence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    offset = 0
    open_at: int | None = None
    open_ch = ""
    open_len = 0
    for line in text.splitlines(keepends=True):
        body = line[:-1] if line.endswith("\n") else line
        if body.endswith("\r"):
            body = body[:-1]
        indent = len(body) - len(body.lstrip(" "))
        candidate = body[indent:] if indent <= 3 else ""
        if open_at is None:
            opened = _FENCE_OPEN.match(candidate) if indent <= 3 else None
            if opened and not (opened.group(1)[0] == "`" and "`" in opened.group(2)):
                open_at = offset
                open_ch = opened.group(1)[0]
                open_len = len(opened.group(1))
        else:
            closed = _FENCE_CLOSE.match(candidate) if indent <= 3 else None
            if (
                closed
                and closed.group(1)[0] == open_ch
                and len(closed.group(1)) >= open_len
            ):
                spans.append((open_at, offset + len(line)))
                open_at = None
        offset += len(line)
    if open_at is not None:
        spans.append((open_at, len(text)))
    return spans


def covered(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(a <= start and end <= b for a, b in spans)


def is_host_port(token: str) -> bool:
    """db.internal:5432 and example.com:8080 are not file:line citations.

    A dotted name:N is a file citation unless the last label is numeric
    (an IP) or an explicit host label. main.rs:42 is a file.
    """
    if "/" in token:
        return False
    m = re.fullmatch(r"[A-Za-z0-9_.-]+\.([A-Za-z0-9-]+):\d+", token)
    if not m:
        return False
    label = m.group(1).lower()
    return label.isdigit() or label in HOST_LABELS


def word_hits_outside_fences(text: str) -> list[tuple[int, str, str]]:
    """Word hits in prose. A fenced script is not the comment."""
    fences = fence_spans(text)
    found: list[tuple[int, str, str]] = []
    for line, span, say, start, end in _words().word_hit_spans(text):
        if covered((start, end), fences):
            continue
        found.append((line, span, say))
    return found


def problems(text: str) -> list[str]:
    links = link_spans(text)
    fences = fence_spans(text)
    found: list[str] = []
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            if kind == "ticket" and m.group(0) in STANDARD_TOKENS:
                continue
            if covered(m.span(), links) or covered(m.span(), fences):
                continue
            if kind == "file:line" and is_host_port(m.group(0)):
                continue
            line = text.count("\n", 0, m.start()) + 1
            found.append(f"{kind}: {m.group(0)} (line {line})")
    return found


def _words():
    path = Path(__file__).resolve().parent / "comment_words.py"
    spec = importlib.util.spec_from_file_location("comment_words", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        raise SystemExit(
            "usage: check-comment-links.py <comments.md>\n"
            "exit 0 when every ticket id, #N, bare SHA, and file:line "
            "sits inside a markdown link, and the comment uses no internal word"
        )
    path = Path(sys.argv[1])
    if not path.is_file():
        raise SystemExit(f"not a file: {path}")
    text = path.read_text(encoding="utf-8")
    found = problems(text)
    words = word_hits_outside_fences(text)
    if found or words:
        if found:
            print(f"{path}: {len(found)} citation(s) outside a markdown link")
            for line in found:
                print(f"  {line}")
        for line, span, say in words:
            print(f'{path}:{line}: public comment contains "{span}"; say "{say}".')
        raise SystemExit(1)
    print(f"{path}: ok")


if __name__ == "__main__":
    main()
