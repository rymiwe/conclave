#!/usr/bin/env python3
"""Post one GitHub review from a payload file and record what went out.

Posting needs the human's go. No Conclave kind posts on its own, and no
script calls this one. Run it by hand, with --dry-run first.

  scripts/post-review.py <id> <pr> [--payload FILE] [--repo OWNER/NAME] [--dry-run]

<id> is a ballot or stack directory under tmp/council/. The payload defaults
to tmp/council/<id>/post-<pr>.json:

  {
    "repo": "owner/name",            optional; else --repo, else judgment.md pr:
    "ballot": "<ballot id>",         optional; plain finding ids resolve here (default <id>)
    "commit_id": "<40-hex head>",
    "event": "COMMENT",              COMMENT (default) or REQUEST_CHANGES
    "body": "summary",
    "body_findings": ["f7"],         findings carried in the summary body
    "pr_author": "octocat",          optional; else user.login on the pull request
    "reviewer": "me",                optional; else gh api user, and only when a
                                     new inline comment does not start with a label
    "comments": [
      {"finding": "f2", "path": "app/x.rb", "line": 54, "body": "...",
       "side": "RIGHT", "expect": "optional text that must be on that line"},
      {"in_reply_to": 12345, "body": "a reply has no label"}
    ]
  }

A finding id is "f2" (the payload's ballot) or "<ballot>:f2".

Steps, in order. Any failure stops before the POST.
1. The PR head must equal commit_id (the verified head).
2. Each inline line must be a RIGHT-side line inside a pulls/<pr>/files hunk.
   A line outside the diff moves into the review body with a permalink.
   "expect", when given, must appear in that line's text. Outside the diff,
   the text is the file at commit_id.
3. Every body must pass scripts/check-comment-links.py, and the payload must
   pass scripts/check-comment-labels.py. That check flags a summary that opens
   with a label, a labeled reply, and a new inline comment on someone
   else's pull request that does not start with a label. A comment with
   in_reply_to is a reply and is not sent as a new line comment. A
   repro/<finding-id>.<ext> file in that finding's ballot directory is
   attached to the new inline comment, or to the review body when the
   finding is in body_findings. A new inline comment or a reply longer than
   65536 characters is refused, and so is a comment whose prose leaves no
   room for its repro.
4. The request goes to tmp/council/<id>/post-<pr>.request.json.
5. --dry-run stops here. Otherwise POST pulls/<pr>/reviews, write
   tmp/council/<id>/post-<pr>.pending.json with the review id and the review
   request that was posted, then read the review's comments back. The pending
   file is written before that read, so a failed read-back cannot erase the
   fact that the review was posted. Then write tmp/council/<id>/posted.json
   with the review id and url and each comment's url keyed by finding id.

A PR already in posted.json is refused. A pending receipt is checked against
GitHub. A rerun that still has replies to send compares the new review
request with the one stored on the receipt. A different commit, body, or
inline comment is not posted and is not recorded as posted. Two replies to
the same comment are refused. A reply that already succeeded is not sent
again. A rerun checks that text before it sends any other reply, including
on a dry run, and stops if the text changed.
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

GH = setting("GH", "gh")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
FINDING = re.compile(r"(?:([A-Za-z0-9_][A-Za-z0-9._-]*):)?(f\d+)")
EVENTS = ("COMMENT", "REQUEST_CHANGES")


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


def split_lines(text: str) -> list[str]:
    """Split on newline only. A form feed stays inside the line."""
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return [part[:-1] if part.endswith("\r") else part for part in parts]


def right_lines(patch: str) -> dict[int, str]:
    """New-file line -> text for every context and added line in the patch."""
    lines: dict[int, str] = {}
    new = None
    for raw in split_lines(patch):
        m = HUNK.match(raw)
        if m:
            new = int(m.group(1))
            continue
        if new is None or raw.startswith("\\"):
            continue
        if raw.startswith("-"):
            continue
        lines[new] = raw[1:] if raw[:1] in (" ", "+") else raw
        new += 1
    return lines


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
    event = payload.get("event", "COMMENT")
    if event not in EVENTS:
        die(f"event must be one of {', '.join(EVENTS)}")
    body = str(payload.get("body", ""))
    comments = payload.get("comments", [])
    if not isinstance(comments, list):
        die("comments must be a list")

    posted_path = home / "posted.json"
    posted = json.loads(posted_path.read_text(encoding="utf-8")) if posted_path.is_file() else {}
    if str(args.pr) in posted.get("reviews", {}):
        die(f"PR {args.pr} is already in {posted_path}; remove that entry to post again")
    pending_path = home / f"post-{args.pr}.pending.json"
    payload_has_replies = any(
        isinstance(c, dict) and c.get("in_reply_to") is not None for c in comments
    )
    # A review that is already on GitHub is not posted again. Replies are the
    # exception: a failed reply POST leaves the pending receipt, and a rerun
    # sends the replies that are not listed there.
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
            if not payload_has_replies:
                die(f"review {review_id} already exists on GitHub; not posting again")
            resume = pending
        else:
            pending_path.unlink()

    head = gh_json(["api", f"repos/{repo}/pulls/{args.pr}"])
    head_sha = (head or {}).get("head", {}).get("sha")
    if head_sha != commit:
        die(f"PR head is {head_sha}, payload commit_id is {commit}; re-verify the anchors")

    files = {f["filename"]: right_lines(f.get("patch") or "") for f in gh_pages(f"repos/{repo}/pulls/{args.pr}/files")}

    findings: dict[str, dict] = {}
    inline: list[dict] = []
    moved: list[dict] = []
    replies: list[dict] = []
    errors: list[str] = []
    blobs: dict[str, list[str] | None] = {}

    def blob_lines(path: str) -> list[str] | None:
        if path not in blobs:
            quoted = quote(path, safe="/")
            data = gh_json(["api", f"repos/{repo}/contents/{quoted}?ref={commit}"])
            blobs[path] = decode_blob(data)
        return blobs[path]

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
        try:
            repro_path = comment_draft.find_repro(council / ballot, fid)
        except comment_draft.ReproError as exc:
            die(str(exc))
        if in_diff is None:
            moved_body = str(c["body"])
            if repro_path is not None:
                # Mark a hand-written block now, so the body budget below
                # counts it as the script, not as prose.
                moved_body = comment_draft.adopt_hand_block(
                    moved_body, repro_path.name, f"<!-- repro:{ballot}/{repro_path.name} -->"
                )
            moved.append({**c, "body": moved_body, "_ballot": ballot, "_fid": fid})
            findings[key] = {"ballot": ballot, "id": fid, "posted_as": "review body",
                             "path": c["path"], "line": c["line"], "reason": "outside the diff"}
        else:
            prose = str(c["body"])
            if repro_path is not None:
                # A hand-written repro block can still shrink; size the prose only.
                prose = comment_draft.prose_without_repro(comment_draft.adopt_hand_block(
                    prose, repro_path.name, f"<!-- repro:{ballot}/{repro_path.name} -->"
                ))
            refuse_over_limit(prose, f"inline comment {key} prose")
            text = attach_finding_repro(str(c["body"]), ballot, fid)
            if repro_path is not None:
                refuse_absent_repro(text, ballot, repro_path.name, f"inline comment {key}")
            refuse_over_limit(text, f"inline comment {key}")
            c["body"] = text
            inline.append({"path": c["path"], "line": c["line"], "side": "RIGHT", "body": c["body"]})
            findings[key] = {"ballot": ballot, "id": fid, "posted_as": "inline",
                             "path": c["path"], "line": c["line"], "_body": c["body"]}
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
    author = payload.get("pr_author") or ((head or {}).get("user") or {}).get("login")
    reviewer = payload.get("reviewer")
    needs_reviewer = any(
        isinstance(c, dict)
        and c.get("in_reply_to") is None
        and not comment_draft.opens_with_label(str(c.get("body", "")))
        for c in comments
    )
    if needs_reviewer and not reviewer:
        reviewer = (gh_json(["api", "user"]) or {}).get("login")
    # `body` here is the summary plus any moved-outside-the-diff block.
    # That block is appended, and it starts with **Outside this diff**, so
    # it cannot make the summary open with a comment label.
    for item in label_check.problems(
        {"body": body, "comments": comments}, pr_author=author, reviewer=reviewer
    ):
        errors.append(item)
    checked = [("body", body)]
    checked += [(f"comment {c['path']}:{c['line']}", c["body"]) for c in inline]
    checked += [(f"reply {r['in_reply_to']}", r["body"]) for r in replies]
    for label, text in checked:
        for p in links.problems(text):
            errors.append(f"{label}: {p} outside a markdown link")
        for line, span, say in links.word_hits_outside_fences(text):
            errors.append(f'{label}: line {line}: public comment contains "{span}"; say "{say}".')
    if errors:
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        die(f"{len(errors)} problem(s); nothing posted")

    request = {"commit_id": commit, "event": event, "body": body, "comments": inline}
    record = dict(request)
    if replies:
        record["replies"] = replies
    request_path = home / f"post-{args.pr}.request.json"
    request_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"{repo}#{args.pr} at {commit[:12]}: {len(inline)} inline, {len(moved)} moved to the body "
          f"(outside the diff), {len(payload.get('body_findings', []))} body findings, {len(replies)} replies")
    for c in moved:
        print(f"  moved: {c['path']}:{c['line']} ({c['finding']})")
    print(f"request: {request_path}")
    review_request = {"commit_id": commit, "event": event, "body": body, "comments": inline}
    if resume and resume.get("review_request") != review_request:
        die("pending review was posted from a different payload; not recording this payload as posted")
    sent: dict[int, str] = {}
    if resume:
        for item in resume.get("replies_posted") or []:
            if not isinstance(item, dict) or type(item.get("in_reply_to")) is not int:
                die(f"pending receipt {pending_path} replies_posted is unreadable")
            sent[item["in_reply_to"]] = str(item.get("body", ""))
        for reply in replies:
            posted_body = sent.get(reply["in_reply_to"])
            if posted_body is not None and posted_body != reply["body"]:
                die(f"reply to comment {reply['in_reply_to']} was already posted with different text")
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
        # The receipt lands before comment read-back. A 502 there must not allow a second POST.
        if review_id:
            write_receipt(pending_path, {
                "repo": repo,
                "pr": args.pr,
                "review_id": review_id,
                "review_url": review_url,
                "commit_id": commit,
                "state": review.get("state"),
                "payload": str(payload_path),
                "review_request": review_request,
                "replies_posted": [],
            })
        if not review_id or not review_url:
            die(f"POST returned no review id or url: {review!r}")
    back = gh_pages(f"repos/{repo}/pulls/{args.pr}/reviews/{review_id}/comments")
    unmatched: list[str] = []
    for key, f in findings.items():
        body_text = f.pop("_body", None)
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
        "findings": findings,
    }
    def remember_reply(reply_id: int, text: str) -> None:
        sent[reply_id] = text
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
        })

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
