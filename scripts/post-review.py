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
    "comments": [
      {"finding": "f2", "path": "app/x.rb", "line": 54, "body": "...",
       "side": "RIGHT", "expect": "optional text that must be on that line"}
    ]
  }

A finding id is "f2" (the payload's ballot) or "<ballot>:f2".

Steps, in order. Any failure stops before the POST.
1. The PR head must equal commit_id (the verified head).
2. Each inline line must be a RIGHT-side line inside a pulls/<pr>/files hunk.
   A line outside the diff moves into the review body with a permalink.
   "expect", when given, must appear in that line's text. Outside the diff,
   the text is the file at commit_id.
3. Every body must pass scripts/check-comment-links.py.
4. The request goes to tmp/council/<id>/post-<pr>.request.json.
5. --dry-run stops here. Otherwise POST pulls/<pr>/reviews, write
   tmp/council/<id>/post-<pr>.pending.json with the review id, then read the
   review's comments back. The pending file is written before that read, so a
   failed read-back cannot erase the fact that the review was posted. Then
   write tmp/council/<id>/posted.json with the review id and url and each
   comment's url keyed by finding id.

A PR already in posted.json is refused. A pending receipt is checked against
GitHub, and a review with this commit and body is refused too, so a rerun
never posts twice.
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

GH = setting("GH", "gh")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
FINDING = re.compile(r"(?:([A-Za-z0-9_][A-Za-z0-9._-]*):)?(f\d+)")
EVENTS = ("COMMENT", "REQUEST_CHANGES")


def die(msg: str) -> None:
    raise SystemExit(f"post-review: {msg}")


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


def outside_block(repo: str, commit: str, moved: list[dict]) -> str:
    parts = ["**Outside this diff**"]
    for c in moved:
        url = f"https://github.com/{repo}/blob/{commit}/{quote(c['path'])}#L{c['line']}"
        parts.append(f"[`{c['path']}:{c['line']}`]({url})\n\n{c['body'].strip()}")
    return "\n\n".join(parts)


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
    if pending_path.is_file():
        try:
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            die(f"pending receipt {pending_path} is unreadable: {exc}")
        review_id = pending.get("review_id") if isinstance(pending, dict) else None
        if review_id is None:
            die(f"pending receipt {pending_path} has no review id")
        if review_on_github(repo, args.pr, review_id):
            die(f"review {review_id} already exists on GitHub; not posting again")
        pending_path.unlink()

    head = gh_json(["api", f"repos/{repo}/pulls/{args.pr}"])
    head_sha = (head or {}).get("head", {}).get("sha")
    if head_sha != commit:
        die(f"PR head is {head_sha}, payload commit_id is {commit}; re-verify the anchors")

    files = {f["filename"]: right_lines(f.get("patch") or "") for f in gh_pages(f"repos/{repo}/pulls/{args.pr}/files")}

    findings: dict[str, dict] = {}
    inline: list[dict] = []
    moved: list[dict] = []
    errors: list[str] = []
    blobs: dict[str, list[str] | None] = {}

    def blob_lines(path: str) -> list[str] | None:
        if path not in blobs:
            quoted = quote(path, safe="/")
            data = gh_json(["api", f"repos/{repo}/contents/{quoted}?ref={commit}"])
            blobs[path] = decode_blob(data)
        return blobs[path]

    for i, c in enumerate(comments):
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
        if in_diff is None:
            moved.append(c)
            findings[key] = {"ballot": ballot, "id": fid, "posted_as": "review body",
                             "path": c["path"], "line": c["line"], "reason": "outside the diff"}
        else:
            inline.append({"path": c["path"], "line": c["line"], "side": "RIGHT", "body": c["body"]})
            findings[key] = {"ballot": ballot, "id": fid, "posted_as": "inline",
                             "path": c["path"], "line": c["line"], "_body": c["body"]}
        if c.get("severity"):
            findings[key]["severity"] = c["severity"]
    for raw in payload.get("body_findings", []):
        ballot, fid = finding_key(raw, default_ballot)
        key = f"{ballot}:{fid}"
        if key in findings:
            die(f"body finding {key} is also an inline comment")
        findings[key] = {"ballot": ballot, "id": fid, "posted_as": "review body"}

    if moved:
        body = (body.rstrip() + "\n\n" + outside_block(repo, commit, moved)).strip()
    links = load_links()
    for label, text in [("body", body)] + [(f"comment {c['path']}:{c['line']}", c["body"]) for c in inline]:
        for p in links.problems(text):
            errors.append(f"{label}: {p} outside a markdown link")
        for line, span, say in links._words().word_hits(text):
            errors.append(f'{label}: line {line}: public comment contains "{span}"; say "{say}".')
    if errors:
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        die(f"{len(errors)} problem(s); nothing posted")

    request = {"commit_id": commit, "event": event, "body": body, "comments": inline}
    request_path = home / f"post-{args.pr}.request.json"
    request_path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    print(f"{repo}#{args.pr} at {commit[:12]}: {len(inline)} inline, {len(moved)} moved to the body "
          f"(outside the diff), {len(payload.get('body_findings', []))} body findings")
    for c in moved:
        print(f"  moved: {c['path']}:{c['line']} ({c['finding']})")
    print(f"request: {request_path}")
    if args.dry_run:
        print("dry run: nothing posted")
        return 0

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
