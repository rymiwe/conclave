#!/usr/bin/env python3
"""Label lint: a labeled reply fails; an unlabeled new inline comment on someone else's PR fails."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import importlib.util

spec = importlib.util.spec_from_file_location("check_comment_labels", ROOT / "check-comment-labels.py")
assert spec and spec.loader
labels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(labels)


def check(cond: bool, label: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL {label}")
    print(f"ok {label}")


def run_cli(payload: dict, *extra: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "post.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(ROOT / "check-comment-labels.py"), str(path), *extra],
            capture_output=True,
            text=True,
        )


def main() -> None:
    other = {"pr_author": "author", "reviewer": "me"}
    labeled_reply = {**other, "comments": [{"in_reply_to": 9, "body": "**Requested:** still broken."}]}
    found = labels.problems(labeled_reply)
    check(any("labeled reply" in item for item in found), f"labeled reply flagged ({found})")
    cli = run_cli(labeled_reply)
    check(cli.returncode != 0 and "labeled reply" in cli.stdout, "cli flags a labeled reply")

    for token in ("**Advisory:**", "**Informational:**", "**Question:**"):
        payload = {**other, "comments": [{"in_reply_to": 9, "body": f"{token} why"}]}
        found = labels.problems(payload)
        check(any("labeled reply" in item for item in found), f"{token} on a reply is a label")

    unlabeled_inline = {**other, "comments": [
        {"finding": "f1", "path": "a.rb", "line": 1, "body": "size counts deleted items."}
    ]}
    found = labels.problems(unlabeled_inline)
    check(any("unlabeled new inline comment" in item for item in found), f"unlabeled inline flagged ({found})")

    labeled_inline = {**other, "comments": [
        {"finding": "f1", "path": "a.rb", "line": 1, "body": "**Requested:** size counts deleted items."}
    ]}
    check(labels.problems(labeled_inline) == [], "labeled new inline comment is ok")
    for token in ("**Advisory:**", "**Informational:**", "**Question:**"):
        payload = {**other, "comments": [{"body": f"{token} noted."}]}
        check(labels.problems(payload) == [], f"{token} counts as a label on a new inline comment")

    plain_reply = {**other, "comments": [{"in_reply_to": 9, "body": "The next build picks this up."}]}
    check(labels.problems(plain_reply) == [], "unlabeled reply is ok")

    fenced = {**other, "comments": [{
        "in_reply_to": 9,
        "body": "```\n**Requested:** inside\n```\n",
    }]}
    check(labels.problems(fenced) == [], "a label inside a fence is not a labeled reply")

    own = {"pr_author": "me", "reviewer": "me", "comments": [{"body": "no label"}]}
    check(labels.problems(own) == [], "your own PR is not flagged for a missing label")

    unknown = {"comments": [{"body": "no label"}]}
    found = labels.problems(unknown)
    check(any("cannot tell" in item for item in found), f"unknown author is not treated as your PR ({found})")

    cli_ok = run_cli(labeled_inline)
    check(cli_ok.returncode == 0 and cli_ok.stdout.endswith("ok\n"), f"cli accepts a labeled comment ({cli_ok.stdout!r})")
    print("ok comment labels")


if __name__ == "__main__":
    main()
