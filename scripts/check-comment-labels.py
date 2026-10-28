#!/usr/bin/env python3
"""Flag a summary that opens with a label, a labeled summary bullet, a labeled
reply, or an unlabeled new inline comment on any PR.

  scripts/check-comment-labels.py <payload.json> [--pr-author LOGIN] [--reviewer LOGIN]

The payload is the post-review JSON. A comment with in_reply_to is a reply.
A comment without it is a new inline comment. The review summary is payload
"body". It must not open with a label. A reply must not carry one.

**Requested:**, **Advisory:**, **Informational:**, and the legacy **Question:**
all count as a label on a summary, a reply, and a bullet. A label inside a
fenced script does not. A new inline comment may open only with the first
three. One that opens with **Question:** is flagged, the same as post-review.py
refuses it: the verdict is computed from Requested, Advisory, and Informational.

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

Every new inline comment needs a label, on your own pull request and on
someone else's. A comment whose "severity" maps to a label is not flagged:
post-review.py puts that label at the start. --pr-author and --reviewer are
still accepted, and are not used for this rule. The summary-opening check
and the labeled-reply check do not depend on whose pull request it is.

When pr-context.json sits beside the payload and it has an automated risk
rating, the summary prose must state that rating. No rating is a pass. The
message names the rating. A level that appears only inside a code fence, a
repro block, or the moved-outside-the-diff block does not count. Comment
text in that file is untrusted data. The same gate runs in post-review.py.
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
import finding_anchor  # noqa: E402


def problems(
    payload: dict,
    *,
    pr_author: str | None = None,
    reviewer: str | None = None,
) -> list[str]:
    # pr_author and reviewer stay in the signature for callers. Every new
    # inline comment needs a label, so whose pull request it is does not matter.
    del pr_author, reviewer
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
        span = comment_draft.opening_label_span(body)
        if span is not None:
            opener = body[span[0]:span[1]].strip()
            # post-review refuses a new comment that opens with **Question:**, and
            # the lint agrees. The post labels are the three the verdict is
            # computed from, and Question maps to none of them.
            if opener not in comment_draft.POST_LABELS:
                found.append(
                    f"comment {index}: opens with {opener}, which is not drafted; "
                    "use **Requested:**, **Advisory:**, or **Informational:**"
                )
            continue
        if finding_anchor.label_for(comment.get("severity")):
            continue
        found.append(
            f"comment {index}: unlabeled new inline comment; start it with "
            "**Requested:**, **Advisory:**, or **Informational:**, or set a severity"
        )
    return found


def risk_problem(payload: dict, payload_path: Path) -> str | None:
    """The summary gate, or None when there is no rating or no context file.

    A missing file, or a file that is not the context object, does not fail
    the draft. A detected rating missing from the summary prose does. A
    fence, a repro block, and the moved-outside-the-diff block are not prose.
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
