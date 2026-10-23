#!/usr/bin/env python3
"""Flag a labeled reply, or an unlabeled new inline comment on someone else's PR.

  scripts/check-comment-labels.py <payload.json> [--pr-author LOGIN] [--reviewer LOGIN]

The payload is the post-review JSON. A comment with in_reply_to is a reply.
A comment without it is a new inline comment. The review body is not checked:
labels are allowed there, and a missing label on the body is not this lint.

**Requested:**, **Advisory:**, **Informational:**, and the legacy **Question:**
all count as a label. A label inside a fenced script does not.

Someone else's pull request means pr_author and reviewer are both set and
they differ. When they are the same login, an unlabeled new inline comment
is left alone (your own pull request; this lint does not require or forbid
a label there). When either login is missing, an unlabeled new inline comment
is flagged because the check cannot tell whose pull request it is.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import comment_draft  # noqa: E402


def others_pr(pr_author: str | None, reviewer: str | None) -> bool | None:
    """True when the pull request is someone else's, False when it is yours.

    None when either login is missing.
    """
    if not pr_author or not reviewer:
        return None
    return pr_author != reviewer


def problems(
    payload: dict,
    *,
    pr_author: str | None = None,
    reviewer: str | None = None,
) -> list[str]:
    author = pr_author if pr_author is not None else payload.get("pr_author")
    who = reviewer if reviewer is not None else payload.get("reviewer")
    author = str(author) if author else None
    who = str(who) if who else None
    whose = others_pr(author, who)
    comments = payload.get("comments") or []
    if not isinstance(comments, list):
        return ["comments must be a list"]
    found: list[str] = []
    for index, comment in enumerate(comments):
        if not isinstance(comment, dict):
            found.append(f"comment {index}: not an object")
            continue
        body = str(comment.get("body", ""))
        if comment.get("in_reply_to") is not None:
            if comment_draft.has_label(body):
                found.append(f"comment {index}: labeled reply")
            continue
        if comment_draft.has_label(body):
            continue
        if whose is False:
            continue
        if whose is True:
            found.append(f"comment {index}: unlabeled new inline comment")
        else:
            found.append(
                f"comment {index}: unlabeled new inline comment; "
                "cannot tell whether this PR is someone else's "
                "(set pr_author and reviewer)"
            )
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("payload")
    ap.add_argument("--pr-author")
    ap.add_argument("--reviewer")
    args = ap.parse_args()
    path = Path(args.payload)
    if not path.is_file():
        raise SystemExit(f"not a file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{path}: payload must be a JSON object")
    found = problems(payload, pr_author=args.pr_author, reviewer=args.reviewer)
    if found:
        print(f"{path}: {len(found)} label problem(s)")
        for line in found:
            print(f"  {line}")
        raise SystemExit(1)
    print(f"{path}: ok")


if __name__ == "__main__":
    main()
