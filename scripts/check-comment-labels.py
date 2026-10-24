#!/usr/bin/env python3
"""Flag a summary that opens with a label, a labeled summary bullet, a labeled
reply, or an unlabeled new inline comment on someone else's PR.

  scripts/check-comment-labels.py <payload.json> [--pr-author LOGIN] [--reviewer LOGIN]

The payload is the post-review JSON. A comment with in_reply_to is a reply.
A comment without it is a new inline comment. The review summary is payload
"body". It must not open with a label. A reply must not carry one.

**Requested:**, **Advisory:**, **Informational:**, and the legacy **Question:**
all count as a label. A label inside a fenced script does not.

A summary opens with a label when, after blank lines, the first non-empty
line starts with one of those labels. Leading spaces and tabs on that line
do not count. A label on a later line does not. A moved-outside-the-diff
block that post-review appends begins with **Outside this diff**, which is
not a comment label, so that block does not open the summary.

Labels go only on new inline comments. With
comment_draft.SUMMARY_BULLETS_MAY_HAVE_LABELS set to False (the setting
since 2026-10-23), a summary bullet whose text starts with a label is
flagged. A bullet is
a line that starts with `-`, `*`, `+`, or a number (`1.` or `1)`).

A new inline comment is labeled only when it starts with a label. That is
the same opening rule as the summary: after blank lines, the first
remaining line starts with the label. A label later in the comment does
not. A reply is different: a label anywhere in the reply is flagged.

Someone else's pull request means pr_author and reviewer are both set and
they differ. When they are the same login, an unlabeled new inline comment
is left alone (your own pull request; this lint does not require or forbid
a label on an inline comment there). When either login is missing, an
unlabeled new inline comment is flagged because the check cannot tell whose
pull request it is. The summary-opening check and the labeled-reply check
do not depend on whose pull request it is.

When pr-context.json sits beside the payload and it has an automated risk
rating, the summary must state that rating. No rating is a pass. The
message names the rating. Comment text in that file is data.
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
    raw_body = payload.get("body", "")
    if raw_body is None:
        raw_body = ""
    if not isinstance(raw_body, str):
        found.append("summary body must be a string")
    else:
        if comment_draft.opens_with_label(raw_body):
            found.append("summary body opens with a label")
        if not comment_draft.SUMMARY_BULLETS_MAY_HAVE_LABELS:
            seen: set[int] = set()
            for start, _end in comment_draft.bullet_label_spans(raw_body):
                line_no = raw_body.count("\n", 0, start) + 1
                if line_no in seen:
                    continue
                seen.add(line_no)
                found.append(f"summary body line {line_no}: labeled bullet")
    for index, comment in enumerate(comments):
        if not isinstance(comment, dict):
            found.append(f"comment {index}: not an object")
            continue
        body = str(comment.get("body", ""))
        if comment.get("in_reply_to") is not None:
            if comment_draft.has_label(body):
                found.append(f"comment {index}: labeled reply")
            continue
        if comment_draft.opens_with_label(body):
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


def risk_problem(payload: dict, payload_path: Path) -> str | None:
    """The summary gate, or None when there is no rating or no context file.

    A missing file, or a file that is not the context object, does not fail
    the draft. A detected rating missing from the summary does.
    """
    context_path = payload_path.parent / "pr-context.json"
    if not context_path.is_file():
        return None
    try:
        context = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(context, dict):
        return None
    try:
        comment_draft.assert_summary_states_risk(str(payload.get("body") or ""), context)
    except comment_draft.RiskSummaryError as exc:
        return str(exc)
    return None


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
    risk = risk_problem(payload, path)
    if found or risk:
        if found:
            print(f"{path}: {len(found)} label problem(s)")
            for line in found:
                print(f"  {line}")
        if risk:
            print(f"{path}: {risk}")
        raise SystemExit(1)
    print(f"{path}: ok")


if __name__ == "__main__":
    main()
