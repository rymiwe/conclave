#!/usr/bin/env python3
"""Post one GitHub review from a payload file and record what went out.

Posting needs the human's go. No Conclave kind posts on its own, and no
script calls this one. Run it by hand, with --dry-run first.

  scripts/post-review.py <id> <pr> [--payload FILE] [--repo OWNER/NAME] [--dry-run]
                         [--human-verdict APPROVE|REQUEST_CHANGES|COMMENT]

<id> is a ballot or stack directory under tmp/council/. The payload defaults
to tmp/council/<id>/post-<pr>.json:

  {
    "repo": "owner/name",            optional; else --repo, else judgment.md pr:
    "ballot": "<ballot id>",         optional; plain finding ids resolve here (default <id>)
    "commit_id": "<40-hex head>",
    "event": "COMMENT",              only with no labeled finding: COMMENT (default)
                                     or REQUEST_CHANGES
    "body": "summary",               a short opener: at most 600 characters,
                                     6 non-empty lines, and no table
    "body_findings": ["f7"],         findings carried in the summary body
    "voters_finished": 2,            optional; used only when the ballot has no
                                     blind/key.txt
    "comments": [
      {"finding": "f2", "path": "app/x.rb", "line": 54, "body": "...",
       "severity": "should-fix",     optional; maps to the label when the body has none
       "side": "RIGHT", "expect": "optional text that must be on that line"},
      {"in_reply_to": 12345, "body": "a reply has no label"}
    ]
  }

"pr_author" and "reviewer" are still read and are not needed: every new
comment needs a label, on any pull request.

A finding id is "f2" (the payload's ballot) or "<ballot>:f2".

Steps, in order. Any failure stops before the POST.
1. The PR head must equal commit_id (the verified head).
2. Each line is checked against this PR's pulls/<pr>/files hunks, never the
   stack tip. A RIGHT-side line in a hunk is an inline comment. A line
   outside the diff whose file is in the diff is a file-level comment
   (subject_type file). A file in no diff moves into the review body with
   a permalink, without its label. "expect", when given, must appear in
   that line's text. Outside the diff, the text is the file at commit_id.
   When the ballot has anchors.json, or stack.json with status stack, a
   line that does not hold its cited text on this PR is resolved with
   scripts/finding_anchor.py against each stack PR's own diff. Code first
   added in a later PR is refused with "re-anchor to #N path:line". A flag
   result is refused too.
3. Every new inline and file-level comment opens with **Requested:**,
   **Advisory:**, or **Informational:**. With no label, the label that
   "severity" maps to is put first. With no label and no mapped severity,
   or a label that disagrees with the severity, the run is refused.
4. The verdict follows the labels. Any Requested finding is
   REQUEST_CHANGES. Labeled findings with none Requested wait on the human:
   a real post needs --human-verdict. APPROVE is accepted there only when
   2 or more voters finished (blind/key.txt, else voters_finished). APPROVE
   is never chosen here. With no labeled finding the payload event is
   kept, COMMENT by default, as before.
5. Every body must pass scripts/check-comment-links.py, and the payload must
   pass scripts/check-comment-labels.py. That check flags a summary that opens
   with a label, a labeled reply, and a new inline comment that does not
   start with a label. When the ballot's
   pr-context.json has an automated risk rating, the summary prose must state
   it. A level only inside a code fence, a repro block, or the
   moved-outside-the-diff block does not count. The same gate runs in
   scripts/check-comment-labels.py.
   A comment with
   in_reply_to is a reply and is not sent as a new line comment. A
   repro/<finding-id>.<ext> file in that finding's ballot directory is
   attached to the new inline or file-level comment, or to the review body
   when the finding is in body_findings. A new inline or file-level comment
   or a reply longer than 65536 characters is refused, and so is a comment
   whose prose leaves no room for its repro.
6. The request goes to tmp/council/<id>/post-<pr>.request.json. It also
   holds the plan (one row per finding: finding, ballot, post_as, path,
   line, label), plan_verdict, file_comments, and replies. None of those
   is sent to GitHub. The dry run prints one plan line per finding and the
   verdict line.
7. --dry-run stops here. Otherwise POST pulls/<pr>/reviews, write
   tmp/council/<id>/post-<pr>.pending.json with the review id and the review
   request that was posted, then read the review's comments back. The pending
   file is written before that read, so a failed read-back cannot erase the
   fact that the review was posted. Each file-level comment is then POSTed to
   pulls/<pr>/comments and listed on the receipt as file_comments_posted.
   Then write tmp/council/<id>/posted.json with the review id and url and
   each comment's url keyed by finding id.

A PR already in posted.json is refused. A pending receipt is checked against
GitHub. A rerun that still has replies or file-level comments to send
compares the new review request with the one stored on the receipt. A
different commit, body, or inline comment is not posted and is not recorded
as posted. Two replies to the same comment are refused. A reply or
file-level comment that already succeeded is not sent again. A rerun checks
that text before it sends anything else, including on a dry run, and stops
if the text changed.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from conclave_env import setting  # noqa: E402
import comment_draft  # noqa: E402
import finding_anchor  # noqa: E402
from finding_anchor import right_lines, split_lines  # noqa: E402

GH = setting("GH", "gh")
FINDING = re.compile(r"(?:([A-Za-z0-9_][A-Za-z0-9._-]*):)?(f\d+)")
# Payload events. Unchanged from before the verdict rule: a payload with no
# labeled finding still uses its own event, COMMENT by default. APPROVE is
# never a payload event. Only --human-verdict can choose it.
EVENTS = ("COMMENT", "REQUEST_CHANGES")
HUMAN_VERDICTS = ("APPROVE", "REQUEST_CHANGES", "COMMENT")
# The labels a new inline or file-level comment may open with.
POST_LABELS = comment_draft.POST_LABELS
REQUESTED = "**Requested:**"
APPROVE_MIN_VOTERS = 2


def die(msg: str) -> None:
    raise SystemExit(f"post-review: {msg}")


def refuse_over_limit(text: str, where: str) -> None:
    """GitHub rejects a review comment body longer than 65536 characters."""
    limit = comment_draft.GITHUB_COMMENT_LIMIT
    if len(text) > limit:
        die(f"{where} is {len(text)} characters; GitHub's limit is {limit}")


def refuse_absent_repro(text: str, ballot: str, filename: str, where: str) -> None:
    """Die when a repro file did not land in text.

    attach_repro adopts a hand-written block by inserting its marker, so a
    missing marker here means the block was left off: the text already
    leaves no room for even a truncated block under GitHub's limit.
    """
    marker = f"<!-- repro:{ballot}/{filename} -->"
    if marker in text:
        return
    die(
        f"{where} has no room for repro {ballot}/{filename}: "
        f"the text leaves no room for even a truncated block under "
        f"GitHub's {comment_draft.GITHUB_COMMENT_LIMIT}-character limit"
    )


def load_label_check():
    spec = importlib.util.spec_from_file_location(
        "check_comment_labels", ROOT / "scripts" / "check-comment-labels.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_judge():
    spec = importlib.util.spec_from_file_location(
        "jev_judge_for_post", ROOT / "scripts" / "jev-judge.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def voters_finished(ballot_dir: Path, payload: dict) -> tuple[int, str | None]:
    """Legs that finished, and where the count came from. None means unknown.

    The ballot's blind/key.txt lists one row per report that finished and was
    blinded. jev-judge.py reads the same file for the convergence columns, so
    this reuses its reader. With no key file, the payload's voters_finished
    is used. With neither, the count is 0.
    """
    if (ballot_dir / "blind" / "key.txt").is_file():
        try:
            return len(load_judge().load_leg_labels(ballot_dir)), f"{ballot_dir.name}/blind/key.txt"
        except (OSError, ValueError):
            pass
    raw = payload.get("voters_finished")
    if type(raw) is int and raw >= 0:
        return raw, "the payload voters_finished"
    return 0, None


def labeled_body(comment: dict) -> tuple[str, str | None, str | None]:
    """(body, label, problem) for a new inline or file-level comment.

    A body with no label takes the label its severity maps to. With no
    label and no mapped severity, or a label that disagrees with the
    mapped severity, problem says why and nothing is changed.
    """
    body = str(comment["body"])
    severity = comment.get("severity")
    mapped = finding_anchor.label_for(severity)
    span = comment_draft.opening_label_span(body)
    have = body[span[0]:span[1]].strip() if span else None
    if have is None:
        if mapped is None:
            return body, None, (
                "new comment has no label and no severity that maps to one; "
                "start it with **Requested:**, **Advisory:**, or **Informational:**, "
                "or add a severity"
            )
        return f"{mapped} {body.lstrip()}", mapped, None
    if have not in POST_LABELS:
        return body, None, (
            f"opens with {have}, which is not drafted; use "
            "**Requested:**, **Advisory:**, or **Informational:**"
        )
    if mapped is not None and have != mapped:
        return body, None, f"label {have} disagrees with severity {severity!r}, which maps to {mapped}"
    return body, have, None


def load_links():
    spec = importlib.util.spec_from_file_location(
        "check_comment_links", ROOT / "scripts" / "check-comment-links.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gh_run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([GH, *args], input=stdin, text=True, capture_output=True)


def gh_json(args: list[str], stdin: str | None = None):
    proc = gh_run(args, stdin)
    if proc.returncode != 0:
        die(f"gh {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        die(f"gh {' '.join(args)} returned non-JSON: {exc}")


def write_receipt(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def same_id(left: object, right: object) -> bool:
    if left is None or right is None:
        return False
    try:
        return int(str(left)) == int(str(right))
    except ValueError:
        return left == right


def review_on_github(repo: str, pr: int, review_id: object) -> bool:
    """True when this review id is still on the pull request."""
    path = f"repos/{repo}/pulls/{pr}/reviews/{review_id}"
    proc = gh_run(["api", path])
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        if "404" in err or "Not Found" in err:
            return False
        die(f"gh api {path} failed: {err}")
    try:
        data = json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        die(f"gh api {path} returned non-JSON: {exc}")
    return isinstance(data, dict) and same_id(data.get("id"), review_id)


def gh_pages(path: str) -> list:
    out: list = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        rows = gh_json(["api", f"{path}{sep}per_page=100&page={page}"])
        if not isinstance(rows, list):
            die(f"{path}: expected a list")
        out.extend(rows)
        if len(rows) < 100:
            return out
        page += 1


def repo_from_ballot(ballot: Path) -> str | None:
    judgment = ballot / "judgment.md"
    if not judgment.is_file():
        return None
    for line in judgment.read_text(encoding="utf-8").splitlines():
        m = re.match(r"pr:\s*https://github\.com/([^/\s]+/[^/\s]+)/pull/\d+", line)
        if m:
            return m.group(1)
    return None


def finding_key(raw: str, default_ballot: str) -> tuple[str, str]:
    m = FINDING.fullmatch(str(raw))
    if not m:
        die(f"bad finding id {raw!r}: use f<N> or <ballot>:f<N>")
    return (m.group(1) or default_ballot, m.group(2))


def risk_context(home: Path) -> dict | None:
    """The ballot's fetched pull-request context, or None when it is absent.

    A missing or unreadable file does not fail posting. The summary gate
    runs only when the file is a JSON object.
    """
    path = home / "pr-context.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def decode_blob(data: object) -> list[str] | None:
    """File lines from a GitHub contents response. None when it is not a utf-8 file."""
    if not isinstance(data, dict) or data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        return None
    try:
        raw = base64.b64decode(data["content"])
        text = raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return split_lines(text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id")
    ap.add_argument("pr", type=int)
    ap.add_argument("--payload")
    ap.add_argument("--repo")
    ap.add_argument("--council-dir", default=str(ROOT / "tmp" / "council"),
                    help="where ballot directories live (tests only)")
    ap.add_argument("--dry-run", action="store_true",
                    help="every check and the request file, but no POST")
    ap.add_argument("--human-verdict", choices=HUMAN_VERDICTS,
                    help="the human's event when findings exist and none is Requested")
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", args.id):
        die(f"unsafe id {args.id!r}")
    council = Path(args.council_dir)
    home = council / args.id
    if not home.is_dir():
        die(f"no directory {home}")
    payload_path = Path(args.payload) if args.payload else home / f"post-{args.pr}.json"
    if not payload_path.is_file():
        die(f"no payload {payload_path}")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    default_ballot = str(payload.get("ballot") or args.id)
    repo = args.repo or payload.get("repo") or repo_from_ballot(council / default_ballot)
    if not repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        die("no repo: pass --repo, set payload repo, or keep a pr: line in judgment.md")
    commit = str(payload.get("commit_id", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        die("commit_id must be a 40-hex SHA")
    payload_event = payload.get("event")
    if payload_event is not None and payload_event not in EVENTS:
        die(f"event must be one of {', '.join(EVENTS)}; APPROVE is only a --human-verdict")
    body = str(payload.get("body", ""))
    summary_text = body
    comments = payload.get("comments", [])
    if not isinstance(comments, list):
        die("comments must be a list")

    posted_path = home / "posted.json"
    posted = json.loads(posted_path.read_text(encoding="utf-8")) if posted_path.is_file() else {}
    if str(args.pr) in posted.get("reviews", {}):
        die(f"PR {args.pr} is already in {posted_path}; remove that entry to post again")
    pending_path = home / f"post-{args.pr}.pending.json"
    # A review that is already on GitHub is not posted again. Replies and
    # file-level comments are the exception: a failed POST leaves the pending
    # receipt, and a rerun sends the ones that are not listed there. Whether
    # a comment is file-level is known only after the diff is read, so the
    # refusal waits until then.
    resume: dict | None = None
    if pending_path.is_file():
        try:
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            die(f"pending receipt {pending_path} is unreadable: {exc}")
        review_id = pending.get("review_id") if isinstance(pending, dict) else None
        if review_id is None:
            die(f"pending receipt {pending_path} has no review id")
        if review_on_github(repo, args.pr, review_id):
            resume = pending
        else:
            pending_path.unlink()

    head = gh_json(["api", f"repos/{repo}/pulls/{args.pr}"])
    head_sha = (head or {}).get("head", {}).get("sha")
    if head_sha != commit:
        die(f"PR head is {head_sha}, payload commit_id is {commit}; re-verify the anchors")

    file_rows = gh_pages(f"repos/{repo}/pulls/{args.pr}/files")
    patches_here = {f["filename"]: f.get("patch") or "" for f in file_rows}
    files = {name: right_lines(patch) for name, patch in patches_here.items()}

    findings: dict[str, dict] = {}
    inline: list[dict] = []
    file_comments: list[dict] = []
    moved: list[dict] = []
    replies: list[dict] = []
    plan: list[dict] = []
    body_bound: set[int] = set()
    errors: list[str] = []
    # The summary is a short opener. Every finding posts as its own comment.
    errors.extend(comment_draft.summary_opener_problems(summary_text))
    blobs: dict[str, list[str] | None] = {}

    def blob_lines(path: str, *, soft: bool = False) -> list[str] | None:
        """The file at commit_id. soft: a failed read is None, not a stop."""
        if path not in blobs:
            quoted = quote(path, safe="/")
            api = ["api", f"repos/{repo}/contents/{quoted}?ref={commit}"]
            if soft:
                proc = gh_run(api)
                try:
                    data = json.loads(proc.stdout or "null") if proc.returncode == 0 else None
                except json.JSONDecodeError:
                    data = None
                return decode_blob(data)
            blobs[path] = decode_blob(gh_json(api))
        return blobs[path]

    # A stacked ballot, or one the chair anchored, checks a line that fails
    # on this PR against the other PRs' own diffs. The stack tip's line
    # number is never used on this PR.
    stack_dir = next(
        (d for d in (council / default_ballot, home)
         if (d / "anchors.json").is_file() or finding_anchor.stack_numbers(d, args.pr) != [args.pr]),
        None,
    )
    for d in (council / default_ballot, home):
        unplaced = finding_anchor.stack_without_target(d, args.pr)
        if unplaced:
            print(
                f"post-review: warning: {unplaced}; lines are checked against #{args.pr} only "
                "and a line outside its diff posts as a file-level comment",
                file=sys.stderr,
            )
    stack_cache: list[dict] = []

    def stack_patches() -> list[dict]:
        if stack_cache or stack_dir is None:
            return stack_cache
        for number in finding_anchor.stack_numbers(stack_dir, args.pr):
            if number == args.pr:
                stack_cache.append({"number": number, "files": patches_here})
                continue
            try:
                stack_cache.append({"number": number, "files": finding_anchor.fetch_files(repo, number)})
            except finding_anchor.GhError as exc:
                die(f"cannot read pulls/{number}/files to check the stack: {exc.message}")
        return stack_cache

    cited: dict[str, dict[str, str | None]] = {}

    def cited_code(ballot: str, fid: str) -> str | None:
        """First cited line for a finding from its ballot's findings.json."""
        if ballot not in cited:
            cited[ballot] = {}
            path = council / ballot / "findings.json"
            try:
                doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            except (OSError, json.JSONDecodeError):
                doc = {}
            for item in (doc.get("findings") if isinstance(doc, dict) else None) or []:
                if isinstance(item, dict):
                    cited[ballot][str(item.get("id"))] = finding_anchor.cited_needle(item.get("cited_code"))
        return cited[ballot].get(fid)

    def attach_finding_repro(
        text: str, ballot: str, fid: str, *, limit: int = comment_draft.GITHUB_COMMENT_LIMIT
    ) -> str:
        try:
            path = comment_draft.find_repro(council / ballot, fid)
        except comment_draft.ReproError as exc:
            die(str(exc))
        if path is None:
            return text
        try:
            return comment_draft.attach_repro(text, path, limit=limit, key=f"{ballot}/{path.name}")
        except UnicodeDecodeError as exc:
            die(f"repro {path} is not utf-8: {exc}")

    for i, c in enumerate(comments):
        if not isinstance(c, dict):
            die(f"comment {i}: not an object")
        if c.get("in_reply_to") is not None:
            reply_id = c["in_reply_to"]
            if type(reply_id) is not int or reply_id < 1:
                die(f"comment {i}: in_reply_to must be a positive integer")
            if not isinstance(c.get("body"), str):
                die(f"comment {i}: missing body")
            if any(r["in_reply_to"] == reply_id for r in replies):
                die(f"comment {i}: two replies to comment {reply_id}")
            refuse_over_limit(c["body"], f"reply to comment {reply_id}")
            replies.append({"in_reply_to": reply_id, "body": c["body"]})
            continue
        for field in ("finding", "path", "line", "body"):
            if field not in c:
                die(f"comment {i}: missing {field}")
        if not isinstance(c["line"], int) or c["line"] < 1:
            die(f"comment {i}: line must be a positive integer")
        if c.get("side", "RIGHT") != "RIGHT":
            die(f"comment {i}: side must be RIGHT")
        ballot, fid = finding_key(c["finding"], default_ballot)
        key = f"{ballot}:{fid}"
        if key in findings:
            die(f"comment {i}: finding {key} appears twice")
        in_diff = files.get(c["path"], {}).get(c["line"])
        text = in_diff
        if text is None and c.get("expect"):
            lines = blob_lines(c["path"])
            if lines is not None and 1 <= c["line"] <= len(lines):
                text = lines[c["line"] - 1]
        if c.get("expect") and (text is None or c["expect"] not in text):
            errors.append(f"comment {i} ({key}): {c['path']}:{c['line']} reads {text!r}, expected {c['expect']!r}")
        where = f"comment {i} ({key}) {c['path']}:{c['line']}"
        needle = c.get("expect") or (cited_code(ballot, fid) if stack_dir is not None else None)

        def holds(line_text: str | None) -> bool:
            """expect is a substring check. Cited code is a whole line, as the resolver reads it."""
            if not needle or line_text is None:
                return False
            return needle in line_text if c.get("expect") else line_text.strip() == needle

        # In a stack the line number may be the stack tip's. A line in this
        # PR's diff is trusted only when it holds the cited text, or when
        # there is no cited text to check.
        if in_diff is not None and (needle is None or holds(in_diff)):
            post_as = "inline"
        else:
            if stack_dir is not None:
                if text is None and needle:
                    lines = blob_lines(c["path"], soft=True)
                    if lines is not None and 1 <= c["line"] <= len(lines):
                        text = lines[c["line"] - 1]
                if not holds(text):
                    anchor = finding_anchor.resolve(
                        {"file": c["path"], "line": c["line"], "cited_code": needle},
                        stack_patches(), args.pr,
                    )
                    if anchor["post_as"] == "inline" and anchor["pr"] != args.pr:
                        errors.append(
                            f"{where}: re-anchor to #{anchor['pr']} {anchor['path']}:{anchor['line']}; "
                            f"not pinned to #{args.pr} ({anchor['reason']})"
                        )
                        continue
                    if anchor["post_as"] == "flag":
                        errors.append(f"{where}: flag for the chair; not pinned to #{args.pr} ({anchor['reason']})")
                        continue
            # A file in this PR's diff takes a file-level comment. A file in
            # no diff cannot, so that comment joins the review body.
            post_as = "file" if c["path"] in files else "review body"
        label = None
        if post_as in ("inline", "file"):
            new_body, label, problem = labeled_body(c)
            if problem:
                errors.append(f"{where}: {problem}")
                continue
            c["body"] = new_body
        plan.append({"finding": fid, "ballot": ballot, "post_as": post_as, "path": c["path"],
                     "line": None if post_as == "file" else c["line"], "label": label})
        try:
            repro_path = comment_draft.find_repro(council / ballot, fid)
        except comment_draft.ReproError as exc:
            die(str(exc))
        if post_as == "review body":
            # A finding in the review body carries no label and is not counted for the verdict.
            moved_body = comment_draft.strip_opening_label(str(c["body"]))
            if repro_path is not None:
                # Mark a hand-written block now, so the body budget below
                # counts it as the script, not as prose.
                moved_body = comment_draft.adopt_hand_block(
                    moved_body, repro_path.name, f"<!-- repro:{ballot}/{repro_path.name} -->"
                )
            moved.append({**c, "body": moved_body, "_ballot": ballot, "_fid": fid})
            body_bound.add(id(c))
            findings[key] = {"ballot": ballot, "id": fid, "posted_as": "review body",
                             "path": c["path"], "line": c["line"], "reason": "outside the diff"}
        else:
            kind = "inline comment" if post_as == "inline" else "file-level comment"
            prose = str(c["body"])
            if repro_path is not None:
                # A hand-written repro block can still shrink; size the prose only.
                prose = comment_draft.prose_without_repro(comment_draft.adopt_hand_block(
                    prose, repro_path.name, f"<!-- repro:{ballot}/{repro_path.name} -->"
                ))
            refuse_over_limit(prose, f"{kind} {key} prose")
            text = attach_finding_repro(str(c["body"]), ballot, fid)
            if repro_path is not None:
                refuse_absent_repro(text, ballot, repro_path.name, f"{kind} {key}")
            refuse_over_limit(text, f"{kind} {key}")
            c["body"] = text
            findings[key] = {"ballot": ballot, "id": fid, "posted_as": post_as,
                             "path": c["path"], "line": c["line"], "_body": c["body"]}
            if post_as == "inline":
                inline.append({"path": c["path"], "line": c["line"], "side": "RIGHT", "body": c["body"]})
            else:
                file_comments.append({"key": key, "path": c["path"], "body": c["body"]})
                findings[key]["reason"] = "line not in this PR's diff"
        if c.get("severity"):
            findings[key]["severity"] = c["severity"]
    body_findings = []
    for raw in payload.get("body_findings", []):
        ballot, fid = finding_key(raw, default_ballot)
        key = f"{ballot}:{fid}"
        if key in findings:
            die(f"body finding {key} is also an inline comment")
        findings[key] = {"ballot": ballot, "id": fid, "posted_as": "review body"}
        body_findings.append((ballot, fid))
        plan.append({"finding": fid, "ballot": ballot, "post_as": "review body",
                     "path": None, "line": None, "label": None})
    if resume and not replies and not file_comments:
        die(f"review {resume.get('review_id')} already exists on GitHub; not posting again")

    repro_findings: list[tuple[str, str, Path]] = []
    for ballot, fid in body_findings:
        try:
            path = comment_draft.find_repro(council / ballot, fid)
        except comment_draft.ReproError as exc:
            die(str(exc))
        if path is not None:
            repro_findings.append((ballot, fid, path))
            # Mark a hand-written body-finding block now, so it is counted as
            # that finding's script and not as summary prose.
            body = comment_draft.adopt_hand_block(body, path.name, f"<!-- repro:{ballot}/{path.name} -->")
    def reserve_repro(path: Path, ballot: str) -> int:
        try:
            script = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            die(f"repro {path} is not utf-8: {exc}")
        total = len(script.encode("utf-8"))
        return 2 + comment_draft.repro_floor(path.name, key=f"{ballot}/{path.name}", total=total)

    body_script_floor = sum(reserve_repro(path, ballot) for ballot, _fid, path in repro_findings)

    # A line outside the diff joins the review body. Reserve each later
    # comment's prose, permalink, and the smallest truncated script, not the
    # script it already carries. Also reserve every body-finding script.
    # A large attachment can then shrink instead of pushing the review over
    # the limit or dropping the next script.
    if moved:
        def moved_link(c: dict) -> str:
            url = f"https://github.com/{repo}/blob/{commit}/{quote(c['path'])}#L{c['line']}"
            return f"[`{c['path']}:{c['line']}`]({url})"

        def script_floor(c: dict) -> int:
            try:
                path = comment_draft.find_repro(council / c["_ballot"], c["_fid"])
            except comment_draft.ReproError as exc:
                die(str(exc))
            if path is None:
                return 0
            return reserve_repro(path, c["_ballot"])

        prose_cost: list[int] = []
        for index, c in enumerate(moved):
            header = "**Outside this diff**\n\n" if index == 0 else ""
            prose = comment_draft.prose_without_repro(str(c["body"])).strip()
            prose_cost.append(len(header) + len(moved_link(c)) + 2 + len(prose) + script_floor(c))
        # Body-finding blocks are rendered again below, into the room the
        # moved comments leave. Their floor is already in body_script_floor.
        assembled = comment_draft.prose_without_repro(body).rstrip() if repro_findings else body.rstrip()
        for index, c in enumerate(moved):
            header = "**Outside this diff**\n\n" if index == 0 else ""
            link = moved_link(c)
            sep = "\n\n" if assembled else ""
            later = body_script_floor + sum(2 + prose_cost[j] for j in range(index + 1, len(moved)))
            prefix = f"{assembled}{sep}{header}{link}\n\n"
            room = comment_draft.GITHUB_COMMENT_LIMIT - len(prefix) - later
            piece = attach_finding_repro(
                str(c["body"]), c["_ballot"], c["_fid"], limit=max(room, 0)
            ).strip()
            try:
                repro_path = comment_draft.find_repro(council / c["_ballot"], c["_fid"])
            except comment_draft.ReproError as exc:
                die(str(exc))
            if repro_path is not None:
                refuse_absent_repro(piece, c["_ballot"], repro_path.name, "review body")
            if len(prefix) + len(piece) + later > comment_draft.GITHUB_COMMENT_LIMIT:
                die(
                    f"review body is {len(prefix) + len(piece) + later} characters; "
                    f"GitHub's limit is {comment_draft.GITHUB_COMMENT_LIMIT}"
                )
            c["body"] = piece
            assembled = prefix + piece
        body = assembled
    for index, (ballot, fid, path) in enumerate(repro_findings):
        later = 0
        for ballot2, _fid2, path2 in repro_findings[index + 1:]:
            later += reserve_repro(path2, ballot2)
        room = comment_draft.GITHUB_COMMENT_LIMIT - later
        body = attach_finding_repro(body, ballot, fid, limit=max(room, 0))
        refuse_absent_repro(body, ballot, path.name, "review body")
    if len(body) > comment_draft.GITHUB_COMMENT_LIMIT:
        die(f"review body is {len(body)} characters; GitHub's limit is {comment_draft.GITHUB_COMMENT_LIMIT}")
    links = load_links()
    label_check = load_label_check()
    # `body` here is the summary plus any moved-outside-the-diff block.
    # That block is appended, and it starts with **Outside this diff**, so
    # it cannot make the summary open with a comment label.
    # The risk gate reads summary prose: fences, repro blocks, and that
    # appended block do not state the rating. summary_text is the summary
    # before those blocks are appended; the gate strips them either way.
    # A comment that went into the review body is not a new comment, so the
    # label rule for new comments does not apply to it.
    labeled_comments = [c for c in comments if not (isinstance(c, dict) and id(c) in body_bound)]
    for item in label_check.problems({"body": body, "comments": labeled_comments}):
        errors.append(item)
    context = risk_context(home)
    if context is not None:
        try:
            comment_draft.assert_summary_states_risk(summary_text, context)
        except comment_draft.RiskSummaryError as exc:
            errors.append(str(exc))
    checked = [("body", body)]
    checked += [(f"comment {c['path']}:{c['line']}", c["body"]) for c in inline]
    checked += [(f"file-level comment {c['path']}", c["body"]) for c in file_comments]
    checked += [(f"reply {r['in_reply_to']}", r["body"]) for r in replies]
    for label, text in checked:
        for p in links.problems(text):
            errors.append(f"{label}: {p} outside a markdown link")
        for line, span, say in links.word_hits_outside_fences(text):
            errors.append(f'{label}: line {line}: public comment contains "{span}"; say "{say}".')

    # The verdict follows the labels. Any Requested finding requests
    # changes. With labeled findings and none Requested, the event is the
    # human's: APPROVE is only offered, with 2 or more voters finished, and
    # is never chosen here. COMMENT is never the default when findings exist.
    labeled = [p for p in plan if p["label"]]
    requested = sum(1 for p in labeled if p["label"] == REQUESTED)
    human = args.human_verdict
    event: str | None = None
    if not labeled:
        # Zero labeled findings (summary only, replies only, or only findings
        # in the review body): the behavior before this rule is kept. The
        # payload event goes out, COMMENT by default.
        event = payload_event or "COMMENT"
        plan_verdict = f"verdict: {event} (no labeled findings; the payload event, COMMENT by default)"
        if human:
            errors.append(f"--human-verdict {human}: there is no labeled finding to decide; leave it off")
    elif requested:
        event = "REQUEST_CHANGES"
        plan_verdict = f"verdict: REQUEST_CHANGES ({requested} Requested)"
        if payload_event not in (None, "REQUEST_CHANGES"):
            errors.append(f"payload event {payload_event} conflicts with {requested} Requested finding(s); "
                          "the event is REQUEST_CHANGES")
        if human not in (None, "REQUEST_CHANGES"):
            errors.append(f"--human-verdict {human} conflicts with {requested} Requested finding(s); "
                          "the event is REQUEST_CHANGES")
    else:
        finished, source = voters_finished(council / default_ballot, payload)
        offered = finished >= APPROVE_MIN_VOTERS
        why = f"{len(labeled)} labeled finding(s), none Requested, so the human picks the event"
        counted = f"counted from {source}" if source else "the voter count is unknown and is taken as 0"
        if offered:
            plan_verdict = f"verdict: pending human — APPROVE offered ({finished} voters finished, {counted}; {why})"
        else:
            plan_verdict = (f"verdict: pending human — no approve offer (only {finished} voter(s) finished; "
                            f"need {APPROVE_MIN_VOTERS}; {counted}; {why})")
        if payload_event is not None:
            errors.append(f"payload event {payload_event} with no Requested finding: the event is the human's; "
                          "drop it and pass --human-verdict")
        if human == "APPROVE" and not offered:
            errors.append(f"--human-verdict APPROVE refused: the approve offer was withheld "
                          f"(only {finished} voter(s) finished; need {APPROVE_MIN_VOTERS}; {counted})")
        elif human:
            event = human
            plan_verdict += f"; the human chose {human}"
        elif not args.dry_run:
            errors.append(f"{plan_verdict}. A real post needs --human-verdict "
                          f"{'|'.join(HUMAN_VERDICTS)}")
    if errors:
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        die(f"{len(errors)} problem(s); nothing posted")

    # The GitHub request is commit_id, event, body, and comments only. The
    # plan, the verdict text, file-level comments, and replies stay in the record.
    request = {"commit_id": commit, "event": event, "body": body, "comments": inline}
    record = {"commit_id": commit, **({"event": event} if event else {}), "body": body, "comments": inline}
    if replies:
        record["replies"] = replies
    if file_comments:
        record["file_comments"] = [{"path": c["path"], "body": c["body"]} for c in file_comments]
    record["plan"] = plan
    record["plan_verdict"] = plan_verdict
    request_path = home / f"post-{args.pr}.request.json"
    request_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"{repo}#{args.pr} at {commit[:12]}: {len(inline)} inline, {len(file_comments)} file-level, "
          f"{len(moved)} moved to the body (outside the diff), "
          f"{len(payload.get('body_findings', []))} body findings, {len(replies)} replies")
    for c in moved:
        print(f"  moved: {c['path']}:{c['line']} ({c['finding']})")
    for p in plan:
        place = p["path"] or "-"
        if p["line"]:
            place += f":{p['line']}"
        print(f"  plan: {p['ballot']}:{p['finding']} {p['post_as']} {place} {p['label'] or 'no label'}")
    print(plan_verdict)
    print(f"request: {request_path}")
    review_request = request
    if resume:
        stored = resume.get("review_request") or {}
        # A dry run that waits on the human has no event yet; compare the rest.
        compare = review_request if event else {**review_request, "event": stored.get("event")}
        if stored != compare:
            die("pending review was posted from a different payload; not recording this payload as posted")
    sent: dict[int, str] = {}
    files_sent: dict[str, dict] = {}
    if resume:
        for item in resume.get("replies_posted") or []:
            if not isinstance(item, dict) or type(item.get("in_reply_to")) is not int:
                die(f"pending receipt {pending_path} replies_posted is unreadable")
            sent[item["in_reply_to"]] = str(item.get("body", ""))
        for item in resume.get("file_comments_posted") or []:
            if not isinstance(item, dict) or not isinstance(item.get("finding"), str):
                die(f"pending receipt {pending_path} file_comments_posted is unreadable")
            files_sent[item["finding"]] = item
        for reply in replies:
            posted_body = sent.get(reply["in_reply_to"])
            if posted_body is not None and posted_body != reply["body"]:
                die(f"reply to comment {reply['in_reply_to']} was already posted with different text")
        for fc in file_comments:
            done = files_sent.get(fc["key"])
            if done is not None and (done.get("path") != fc["path"] or done.get("body") != fc["body"]):
                die(f"file-level comment {fc['key']} on {fc['path']} was already posted with different text")
    if args.dry_run:
        print("dry run: nothing posted")
        return 0

    if resume:
        review_id = resume["review_id"]
        review_url = resume.get("review_url")
        if not review_url:
            die(f"pending receipt {pending_path} has no review url")
        review = {"id": review_id, "html_url": review_url, "state": resume.get("state")}
    else:
        # Commit and body alone are not an identity: an empty body matches other reviewers.
        login = (gh_json(["api", "user"]) or {}).get("login")
        if not login:
            die("gh api user returned no login")
        for rev in gh_pages(f"repos/{repo}/pulls/{args.pr}/reviews"):
            if (isinstance(rev, dict) and rev.get("commit_id") == commit and rev.get("body") == body
                    and (rev.get("user") or {}).get("login") == login and rev.get("id")):
                die(f"review {rev['id']} already exists on GitHub; not posting again")

        review = gh_json(["api", "-X", "POST", f"repos/{repo}/pulls/{args.pr}/reviews", "--input", "-"],
                         stdin=json.dumps(request))
        review_id = review.get("id")
        review_url = review.get("html_url")

    def write_pending() -> None:
        write_receipt(pending_path, {
            "repo": repo,
            "pr": args.pr,
            "review_id": review_id,
            "review_url": review_url,
            "commit_id": commit,
            "state": review.get("state"),
            "payload": str(payload_path),
            "review_request": review_request,
            "replies_posted": [
                {"in_reply_to": i, "body": sent[i]} for i in sorted(sent)
            ],
            "file_comments_posted": [files_sent[k] for k in sorted(files_sent)],
        })

    if not resume:
        # The receipt lands before comment read-back. A 502 there must not allow a second POST.
        if review_id:
            write_pending()
        if not review_id or not review_url:
            die(f"POST returned no review id or url: {review!r}")
    back = gh_pages(f"repos/{repo}/pulls/{args.pr}/reviews/{review_id}/comments")
    unmatched: list[str] = []
    for key, f in findings.items():
        body_text = f.pop("_body", None)
        if f["posted_as"] == "file":
            continue
        if f["posted_as"] != "inline":
            f["url"] = review_url
            continue
        hit = next((r for r in back if r.get("path") == f["path"] and r.get("body") == body_text
                    and r.get("line", r.get("original_line")) == f["line"]), None)
        if hit is None:
            hit = next((r for r in back if r.get("path") == f["path"] and r.get("body") == body_text), None)
        if hit is None:
            unmatched.append(key)
            f["url"] = review_url
        else:
            back.remove(hit)
            f["url"] = hit.get("html_url")
            f["comment_id"] = hit.get("id")

    posted.setdefault("repo", repo)
    posted.setdefault("reviews", {})[str(args.pr)] = {
        "review_id": review_id,
        "review_url": review_url,
        "state": review.get("state"),
        "commit_id": commit,
        "payload": str(payload_path),
        "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "plan_verdict": plan_verdict,
        "findings": findings,
    }
    def remember_reply(reply_id: int, text: str) -> None:
        sent[reply_id] = text
        write_pending()

    # The reviews endpoint has no file-level form. Each file-level comment is
    # its own POST after the review, recorded on the receipt as it lands.
    for fc in file_comments:
        if fc["key"] not in files_sent:
            made = gh_json(
                ["api", "-X", "POST", f"repos/{repo}/pulls/{args.pr}/comments", "--input", "-"],
                stdin=json.dumps({"body": fc["body"], "commit_id": commit, "path": fc["path"],
                                  "subject_type": "file"}),
            ) or {}
            files_sent[fc["key"]] = {"finding": fc["key"], "path": fc["path"], "body": fc["body"],
                                     "url": made.get("html_url"), "comment_id": made.get("id")}
            write_pending()
        done = files_sent[fc["key"]]
        findings[fc["key"]]["url"] = done.get("url") or review_url
        if done.get("comment_id") is not None:
            findings[fc["key"]]["comment_id"] = done["comment_id"]

    for reply in replies:
        if reply["in_reply_to"] in sent:
            continue
        gh_json(
            ["api", "-X", "POST",
             f"repos/{repo}/pulls/{args.pr}/comments/{reply['in_reply_to']}/replies",
             "--input", "-"],
            stdin=json.dumps({"body": reply["body"]}),
        )
        remember_reply(reply["in_reply_to"], reply["body"])
    write_receipt(posted_path, posted)
    pending_path.unlink(missing_ok=True)
    print(f"posted: {review_url}")
    print(f"record: {posted_path}")
    if unmatched:
        print(f"read-back did not find {len(unmatched)} inline comment(s): {', '.join(unmatched)}; "
              "their url is the review url. Read the review back by hand.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
