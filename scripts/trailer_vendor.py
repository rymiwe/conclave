#!/usr/bin/env python3
"""Vendor named by Co-authored-by trailers on a pull request's commits.

vendor_from_commits takes the document from `gh pr view --json commits`
(author may be present and is ignored) and returns one vendor, "ambiguous",
or "". The from-commits command prints "unknown" and the names when a trailer
names someone but no known family (Claude Code's default "Claude", say). A
display name of Cursor or Cursor Agent names no model. grok and xai are the
same vendor.
"""
from __future__ import annotations

import json
import re
import sys

# wonder-full and wonder both map to meta. The boundary keeps a shorter
# family from matching inside a longer token such as opus4.
FAMILIES = (
    ("wonder-full", "meta"),
    ("fable", "anthropic"),
    ("opus", "anthropic"),
    ("astra", "openai"),
    ("grok", "xai"),
    ("muse", "meta"),
    ("argon", "google"),
    ("wonder", "meta"),
)

TRAILER = re.compile(r"(?im)^[ \t]*co-authored-by:[ \t]*(.+)$")
CURSOR_NAMES = ("cursor", "cursor agent")


def normalize_vendor(value: object) -> str:
    """Lowercase vendor token. grok is xai."""
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    if text == "grok":
        return "xai"
    return text


def trailers(doc: dict):
    """Yield the lowercase display name of each Co-authored-by trailer."""
    for commit in doc.get("commits") or []:
        if not isinstance(commit, dict):
            continue
        text = (commit.get("messageHeadline") or "") + "\n" + (commit.get("messageBody") or "")
        for match in TRAILER.finditer(text):
            yield match.group(1).strip().split("<", 1)[0].strip().lower()


def unmatched_names(doc: dict) -> list[str]:
    """Co-author display names that are not Cursor and name no known family."""
    out: set[str] = set()
    for name in trailers(doc):
        if name in CURSOR_NAMES or not name:
            continue
        if not any(re.search(r"(?<![a-z0-9])" + re.escape(fam) + r"(?![a-z0-9])", name)
                   for fam, _ in FAMILIES):
            out.add(name)
    return sorted(out)


def vendors_named(doc: dict) -> set[str]:
    vendors: set[str] = set()
    for commit in doc.get("commits") or []:
        if not isinstance(commit, dict):
            continue
        text = (commit.get("messageHeadline") or "") + "\n" + (commit.get("messageBody") or "")
        for match in TRAILER.finditer(text):
            line = match.group(1).strip()
            name = line.split("<", 1)[0].strip().lower()
            if name in CURSOR_NAMES:
                continue
            for fam, vendor in FAMILIES:
                if re.search(r"(?<![a-z0-9])" + re.escape(fam) + r"(?![a-z0-9])", name):
                    vendors.add(vendor)
    return vendors


def vendor_from_commits(doc: dict) -> str:
    """One vendor, "ambiguous" when several are named, or "" when none are."""
    vendors = vendors_named(doc)
    if len(vendors) == 1:
        return next(iter(vendors))
    if vendors:
        return "ambiguous"
    return ""


def _emit(doc: dict) -> str:
    status = vendor_from_commits(doc)
    if not status:
        names = unmatched_names(doc)
        # A trailer that names someone, but no family the council knows.
        return "unknown\n" + ", ".join(names) + "\n" if names else ""
    listed = ", ".join(sorted(vendors_named(doc)))
    return status + "\n" + listed + "\n"


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "normalize":
        print(normalize_vendor(argv[1]))
        return 0
    if argv == ["from-commits"]:
        try:
            doc = json.loads(sys.stdin.read())
        except ValueError:
            return 1
        if not isinstance(doc, dict):
            return 1
        sys.stdout.write(_emit(doc))
        return 0
    print("usage: trailer_vendor.py normalize <token> | from-commits", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
