#!/usr/bin/env python3
"""Label lint: a summary that opens with a label fails, a labeled reply fails,
and an unlabeled new inline comment on someone else's PR fails."""
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


def run_by_path(payload: dict) -> subprocess.CompletedProcess[str]:
    """The chair runs this script by path, not via the interpreter."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "post.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run(
            ["./scripts/check-comment-labels.py", str(path)],
            cwd=ROOT.parent,
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
    by_path = run_by_path(labeled_inline)
    check(by_path.returncode == 0 and by_path.stdout.endswith("ok\n"),
          f"scripts/check-comment-labels.py runs by path ({by_path.returncode} {by_path.stdout!r} {by_path.stderr!r})")

    buried = {**other, "comments": [{
        "finding": "f1", "path": "a.rb", "line": 1,
        "body": "size counts deleted items. as **Requested:** above",
    }]}
    found = labels.problems(buried)
    check(any("unlabeled new inline comment" in item for item in found),
          f"a label that does not open the comment is not a label ({found})")
    later = {**other, "comments": [{"body": "See above.\n**Requested:** fix."}]}
    check(any("unlabeled new inline comment" in item for item in labels.problems(later)),
          "a label on a later line does not open an inline comment")
    padded = {**other, "comments": [{"body": "\n\n  **Advisory:** noted."}]}
    check(labels.problems(padded) == [], "blank lines and indentation still open an inline comment")
    own_buried = {"pr_author": "me", "reviewer": "me", "comments": [
        {"body": "size counts deleted items. as **Requested:** above"}
    ]}
    check(labels.problems(own_buried) == [], "your own PR is not flagged when the label is not at the start")
    unknown_buried = {"comments": [{"body": "see **Requested:** later"}]}
    found = labels.problems(unknown_buried)
    check(any("cannot tell" in item for item in found),
          f"a buried label does not identify the pull request ({found})")
    buried_reply = {**other, "comments": [{"in_reply_to": 9, "body": "see **Requested:** later"}]}
    check(any("labeled reply" in item for item in labels.problems(buried_reply)),
          "a reply is labeled wherever the label sits")

    opening = {**other, "body": "\n\n  **Requested:** four notes.\n", "comments": []}
    found = labels.problems(opening)
    check(any("summary body opens with a label" in item for item in found), f"summary opening flagged ({found})")
    cli_open = run_cli(opening)
    check(cli_open.returncode != 0 and "summary body opens with a label" in cli_open.stdout,
          f"cli flags a summary that opens with a label ({cli_open.stdout!r})")
    for token in ("**Advisory:**", "**Informational:**", "**Question:**"):
        payload = {**other, "body": f"{token} notes\n", "comments": []}
        found = labels.problems(payload)
        check(any("summary body opens with a label" in item for item in found), f"{token} opens the summary")

    clean = {**other, "body": "Four notes on the widget change.\n", "comments": [
        {"finding": "f1", "path": "a.rb", "line": 1, "body": "**Requested:** size counts deleted items."}
    ]}
    check(labels.problems(clean) == [], "summary opening without a label is ok")
    mid = {**other, "body": "Four notes.\n\nSee **Requested:** later in the line.\n", "comments": []}
    check(labels.problems(mid) == [], "a label that does not start the first line is not an opening")

    bullets = {**other, "body": "Notes.\n\n- **Requested:** fix the count\n- **Question:** why\n", "comments": []}
    check(labels.problems(bullets) == [], "labeled summary bullets are ok")
    only_bullet = {**other, "body": "- **Advisory:** fix the count\n", "comments": []}
    check(labels.problems(only_bullet) == [], "a summary that is only a labeled bullet does not open with a label")

    moved = {
        **other,
        "comments": [],
        "body": (
            "Four notes on the widget change.\n\n"
            "**Outside this diff**\n\n"
            "[`app/widget.rb:40`](https://github.com/example/widgets/blob/abc/app/widget.rb#L40)\n\n"
            "**Question:** should the label follow the locale?\n\n"
            "[`lib/other.rb:1`](https://github.com/example/widgets/blob/abc/lib/other.rb#L1)\n\n"
            "**Advisory:** `call` has no nil guard.\n"
        ),
    }
    check(labels.problems(moved) == [], "a moved-outside-the-diff block is not an opening label")
    empty_moved = {
        **other,
        "comments": [],
        "body": (
            "**Outside this diff**\n\n"
            "[`app/widget.rb:40`](https://github.com/example/widgets/blob/abc/app/widget.rb#L40)\n\n"
            "**Requested:** should the label follow the locale?\n"
        ),
    }
    check(labels.problems(empty_moved) == [], "an empty summary plus a moved block does not open with a label")

    own_open = {"pr_author": "me", "reviewer": "me", "body": "**Informational:** notes\n", "comments": []}
    found = labels.problems(own_open)
    check(any("summary body opens with a label" in item for item in found),
          f"your own PR still flags a summary that opens with a label ({found})")

    fenced_bullet = {**other, "body": "Notes.\n\n```\n- **Requested:** inside\n```\n", "comments": []}
    saved = labels.comment_draft.SUMMARY_BULLETS_MAY_HAVE_LABELS
    labels.comment_draft.SUMMARY_BULLETS_MAY_HAVE_LABELS = False
    try:
        found = labels.problems(bullets)
        check(len(found) == 2 and all("labeled bullet" in item for item in found),
              f"labeled summary bullets are flagged when the constant is false ({found})")
        check(any("line 3" in item for item in found) and any("line 4" in item for item in found),
              f"bullet flags name the line ({found})")
        check(labels.problems(only_bullet) != [] and any("labeled bullet" in item for item in labels.problems(only_bullet)),
              "a lone labeled bullet is flagged when the constant is false")
        check(labels.problems(moved) == [], "a moved block is not a labeled bullet when the constant is false")
        check(labels.problems(empty_moved) == [],
              "a moved block on an empty summary is not a labeled bullet when the constant is false")
        check(labels.problems(fenced_bullet) == [], "a labeled bullet inside a fence is not flagged")
        found = labels.problems(opening)
        check(any("summary body opens with a label" in item for item in found),
              "a summary opening is still flagged when the constant is false")
        check(labels.problems(clean) == [], "a summary without a label stays ok when the constant is false")
    finally:
        labels.comment_draft.SUMMARY_BULLETS_MAY_HAVE_LABELS = saved
    check(labels.comment_draft.SUMMARY_BULLETS_MAY_HAVE_LABELS is True, "the bullet constant is restored")
    check(labels.problems(bullets) == [], "labeled bullets are ok again after the constant is restored")
    print("ok comment labels")


if __name__ == "__main__":
    main()
