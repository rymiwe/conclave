#!/usr/bin/env python3
"""Vendor named by Co-authored-by trailers on a pull request's commits.

vendor_from_commits takes the document from `gh pr view --json commits`
(author may be present and is ignored) and returns one vendor, "ambiguous",
or "". A trailer names a model through its display name (a known family, or
Claude) or through an @anthropic.com mailbox. A display name of Cursor or
Cursor Agent names no model. Several vendors, or a known vendor beside a
co-author who names no known family, are "ambiguous". The from-commits
command prints "unknown" and the names when the only trailers name someone
but no known family. grok and xai are the same vendor.

The PR author controls the trailers. They are a consistency check on the
builder vendor the caller passes, not proof of who wrote the code.
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
    ("claude", "anthropic"),
    ("opus", "anthropic"),
    ("astra", "openai"),
    ("grok", "xai"),
    ("muse", "meta"),
    ("argon", "google"),
    ("wonder", "meta"),
)

# A mailbox at exactly this domain names the vendor. A lookalike such as
# notanthropic.com or anthropic.com.evil.test does not.
DOMAINS = (("anthropic.com", "anthropic"),)

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


def _entries(doc: dict):
    """Yield (lowercase display name, lowercase mailbox) per Co-authored-by trailer."""
    for commit in doc.get("commits") or []:
        if not isinstance(commit, dict):
            continue
        text = (commit.get("messageHeadline") or "") + "\n" + (commit.get("messageBody") or "")
        for match in TRAILER.finditer(text):
            line = match.group(1).strip().lower()
            name, _, rest = line.partition("<")
            yield name.strip(), rest.split(">", 1)[0].strip()


def _vendors_of(name: str, mailbox: str) -> set[str]:
    """Vendors one trailer names, empty when it names none."""
    out = {vendor for fam, vendor in FAMILIES
           if re.search(r"(?<![a-z0-9])" + re.escape(fam) + r"(?![a-z0-9])", name)}
    for domain, vendor in DOMAINS:
        if mailbox.endswith("@" + domain):
            out.add(vendor)
    return out


def unmatched_names(doc: dict) -> list[str]:
    """Co-author display names that are not Cursor and name no known vendor."""
    out: set[str] = set()
    for name, mailbox in _entries(doc):
        if name in CURSOR_NAMES or not name:
            continue
        if not _vendors_of(name, mailbox):
            out.add(name)
    return sorted(out)


def vendors_named(doc: dict) -> set[str]:
    vendors: set[str] = set()
    for name, mailbox in _entries(doc):
        if name in CURSOR_NAMES:
            continue
        vendors |= _vendors_of(name, mailbox)
    return vendors


def vendor_from_commits(doc: dict) -> str:
    """One vendor, "ambiguous", or "" when none is named.

    A known vendor beside a co-author who names no known family is ambiguous:
    the trailers do not say who built the PR.
    """
    vendors = vendors_named(doc)
    if len(vendors) == 1 and not unmatched_names(doc):
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
    listed = sorted(vendors_named(doc)) + ["unrecognised co-author " + n for n in unmatched_names(doc)]
    return status + "\n" + ", ".join(listed) + "\n"


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
