#!/usr/bin/env python3
"""Citation check: real tickets, SHAs, and file:line stay; the false positives do not."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def load():
    path = Path(__file__).resolve().parent / "check-comment-links.py"
    spec = importlib.util.spec_from_file_location("check_comment_links", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    mod = load()
    clear = [
        "The log line was defaced.",
        "The fixture id is deadbeef.",
        "Uses HTTP-2 per PEP-8.",
        "Connect to db.postgres:5432.",
        "Connect to db.internal:5432 and example.com:8080.",
        "UTF-8, SHA-256, and ISO-8601 are standard tokens.",
        "The count was 1234567890.",
        "Notation `V1I24MF2025-04-17` and V1E24MF2025-04-17 are prefixed dates.",
        "See [ABC-7003](https://example.test/ABC-7003) and [`app/widget.rb:3`](https://example.test/blob/main/app/widget.rb#L3).",
        "```\nabc1234\n```\n",
    ]
    for text in clear:
        found = mod.problems(text)
        if found:
            raise SystemExit(f"flagged {text!r}: {found}")
        print(f"clear {text!r}")
    flagged = [
        ("Fixed in abc1234.", "sha: abc1234"),
        ("See ABC-7003.", "ticket: ABC-7003"),
        ("See ENG2-12.", "ticket: ENG2-12"),
        ("See ABC-64.", "ticket: ABC-64"),
        ("See #6 and #123.", "issue: #6"),
        ("See app/widget.rb:3.", "file:line: app/widget.rb:3"),
        ("See main.rs:42.", "file:line: main.rs:42"),
        ("See Gemfile:3.", "file:line: Gemfile:3"),
        ("See Dockerfile:1.", "file:line: Dockerfile:1"),
        ("See scripts/foo.py:12.", "file:line: scripts/foo.py:12"),
        ("Fixed in a1b2c3d4e5f67890.", "sha: a1b2c3d4e5f67890"),
    ]
    for text, want in flagged:
        found = mod.problems(text)
        if not any(item.startswith(want) for item in found):
            raise SystemExit(f"wanted {want} in {text!r}, got {found}")
        print(f"flagged {want}")
    # Every pattern class still fires: ticket, issue, sha, file:line.
    kinds = {item.split(":", 1)[0] for text, _want in flagged for item in mod.problems(text)}
    for kind in ("ticket", "issue", "sha", "file"):
        if kind not in kinds:
            raise SystemExit(f"pattern class {kind} dropped; saw {sorted(kinds)}")
    print("ok comment links")


if __name__ == "__main__":
    main()
