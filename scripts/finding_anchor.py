#!/usr/bin/env python3
"""Decide which pull request a finding's line belongs to.

The library functions do not call GitHub. The CLI does, and only at the edge:
a failed `gh` call marks every finding unverified and still exits 0.

  python3 scripts/finding_anchor.py <ballot-dir>

Reads findings.json, stack.json, and the pr: line in judgment.md. Writes
anchors.json and prints one line per finding. Line numbers come from each
PR's own diff. A line counted at the top of the stack is never reused as a
line on an earlier PR.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from conclave_env import setting  # noqa: E402

GH = setting("GH", "gh")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
PR_LINE = re.compile(r"pr:\s*https://github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)")

# severity text -> the label that opens a new inline comment.
# Keys are already normalized (lower case, spaces and underscores as hyphens).
SEVERITY_LABELS = {
    "blocking": "**Requested:**",
    "should-fix": "**Requested:**",
    "requested": "**Requested:**",
    "bug": "**Requested:**",
    "nit": "**Advisory:**",
    "usability": "**Advisory:**",
    "suggestion": "**Advisory:**",
    "advisory": "**Advisory:**",
    "fyi": "**Informational:**",
    "info": "**Informational:**",
    "informational": "**Informational:**",
}


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
        match = HUNK.match(raw)
        if match:
            new = int(match.group(1))
            continue
        if new is None or raw.startswith("\\"):
            continue
        if raw.startswith("-"):
            continue
        lines[new] = raw[1:] if raw[:1] in (" ", "+") else raw
        new += 1
    return lines


def _severity_key(severity: object) -> str:
    if not isinstance(severity, str):
        return ""
    text = severity.strip().lower()
    if not text:
        return ""
    return re.sub(r"[\s_]+", "-", text)


def label_for(severity: object) -> str | None:
    """The opening label for a severity, or None when it does not map."""
    return SEVERITY_LABELS.get(_severity_key(severity))


def cited_needle(cited_code: object) -> str | None:
    """First non-blank line of cited code, stripped. Blank or missing is None."""
    if not isinstance(cited_code, str):
        return None
    for line in cited_code.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _pr_number(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _index_patches(stack_patches: object) -> tuple[list[int], dict[int, dict]]:
    order: list[int] = []
    by_pr: dict[int, dict] = {}
    if not isinstance(stack_patches, list):
        return order, by_pr
    for item in stack_patches:
        if not isinstance(item, dict):
            continue
        number = _pr_number(item.get("number"))
        if number is None:
            continue
        files = item.get("files")
        by_pr[number] = files if isinstance(files, dict) else {}
        order.append(number)
    return order, by_pr


def _patch(by_pr: dict[int, dict], number: int, path: str | None) -> str | None:
    """Patch text when this PR's diff has path. None when the file is absent."""
    if path is None or number not in by_pr:
        return None
    files = by_pr[number]
    if path not in files:
        return None
    patch = files[path]
    return patch if isinstance(patch, str) else ""


def _line_with_needle(patch: str, needle: str) -> int | None:
    for line, text in sorted(right_lines(patch).items()):
        if needle in text:
            return line
    return None


def resolve(finding: object, stack_patches: object, target_pr: int) -> dict:
    """Where a finding posts, judged from each PR's own diff.

    stack_patches is bottom to top: [{"number", "files": {path: patch}}].
    post_as is inline, file, flag, or unverified. A match with no cited code
    sets weak. The line on an inline result is that PR's own right-side line.
    """
    raw = finding if isinstance(finding, dict) else {}
    path = raw.get("file")
    path = path.strip() if isinstance(path, str) and path.strip() else None
    line = raw.get("line")
    line = line if _pr_number(line) is not None and line >= 1 else None
    needle = cited_needle(raw.get("cited_code"))
    order, by_pr = _index_patches(stack_patches)

    def result(post_as: str, pr: int | None, at: int | None, reason: str, **extra: object) -> dict:
        out = {"post_as": post_as, "pr": pr, "path": path, "line": at, "reason": reason}
        out.update(extra)
        return out

    if not order:
        return result("unverified", target_pr, line, "patches unavailable")
    if path is None:
        return result("unverified", target_pr, None, "finding has no file")

    def later(number: int) -> bool:
        if target_pr not in order or number not in order:
            return False
        return order.index(number) > order.index(target_pr)

    target_patch = _patch(by_pr, target_pr, path)
    target_lines = right_lines(target_patch) if isinstance(target_patch, str) else {}

    # (a) The target PR's own diff contains this right-side line.
    if path and line is not None and line in target_lines:
        if needle is None:
            return result(
                "inline", target_pr, line, "line is in this PR's diff", weak=True
            )
        if needle in target_lines[line]:
            return result(
                "inline",
                target_pr,
                line,
                "line is in this PR's diff and the cited code matches",
            )

    # (b) The earliest PR in stack order whose diff contains the cited line.
    # Only a later PR is a re-anchor. An earlier PR's line is not this PR's line.
    if path and needle:
        for number in order:
            patch = _patch(by_pr, number, path)
            if patch is None:
                continue
            found = _line_with_needle(patch, needle)
            if found is None:
                continue
            if later(number):
                return result(
                    "inline",
                    number,
                    found,
                    f"re-anchored: first introduced in #{number}",
                )
            break

    # (c) This PR changes the file, and (b) found no later PR that first adds the code.
    if target_patch is not None:
        if line is None:
            return result("file", target_pr, None, "the finding has no line")
        if line in target_lines:
            return result("file", target_pr, None, f"line {line} does not hold the cited code")
        return result("file", target_pr, None, f"line {line} is not in this PR's diff")

    later_touch = [
        number for number in order
        if later(number) and _patch(by_pr, number, path) is not None
    ] if path else []

    # (d) Only later PRs change the file, and the line did not match.
    if later_touch and target_patch is None:
        names = ", ".join(f"#{number}" for number in later_touch)
        return result(
            "flag",
            None,
            None,
            f"{path} is changed only by later PRs ({names}) and the cited code was not matched",
        )

    seen = [number for number in order if _patch(by_pr, number, path) is not None]
    if not seen:
        return result("unverified", target_pr, line, "file is in no PR's diff")
    names = ", ".join(f"#{number}" for number in seen)
    return result(
        "unverified", target_pr, line, f"{path} is changed only by earlier PRs ({names})"
    )


LABEL_ADVICE = (
    "label: recommend bug or should-fix -> **Requested:**, nit or usability -> "
    "**Advisory:**, FYI -> **Informational:**; the label opens the line comment, "
    "never the summary or a reply"
)


def _place(path: object, line: object) -> str:
    if isinstance(path, str) and path and _pr_number(line) is not None:
        return f"{path}:{line}"
    return path if isinstance(path, str) and path else "the cited line"


def how_it_posts(finding: object, anchor: object = None) -> str:
    """One line on how a finding posts. decide.md and judgment.md both use it.

    With no anchor it states the default and the fallback. With an
    anchors.json entry it states that finding's resolved plan.
    """
    raw = finding if isinstance(finding, dict) else {}
    path = raw.get("file") if isinstance(raw.get("file"), str) and raw.get("file") else None
    if not isinstance(anchor, dict):
        plan = (
            f"inline review comment on {_place(path, raw.get('line'))} by default; "
            f"a file-level comment on {path or 'that file'} when that line is not in this PR's diff"
        )
        return f"{plan}. {LABEL_ADVICE}"
    post_as = anchor.get("post_as")
    pr = anchor.get("pr")
    where = _place(anchor.get("path") or path, anchor.get("line"))
    reason = str(anchor.get("reason") or "no reason was recorded")
    if post_as == "inline" and reason.startswith("re-anchored"):
        plan = f"re-anchored to #{pr} {where} ({reason}); not pinned to this PR"
    elif post_as == "inline":
        plan = f"inline on #{pr} {where}"
        if anchor.get("weak"):
            plan += " (line only; no cited code to match)"
    elif post_as == "file":
        plan = f"file-level comment on #{pr} {anchor.get('path') or path} ({reason})"
    elif post_as == "flag":
        plan = f"flag for the chair; not pinned to any PR ({reason})"
    else:
        plan = f"unverified because {reason}"
    return f"{plan}. {LABEL_ADVICE}"


def load_anchors(ballot: Path) -> dict[str, dict] | None:
    """anchors.json as finding id -> result. Missing or unreadable is None."""
    path = ballot / "anchors.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def repo_and_pr(ballot: Path) -> tuple[str | None, int | None]:
    """owner/name and pull number from the same pr: line as post-review."""
    judgment = ballot / "judgment.md"
    if not judgment.is_file():
        return None, None
    for line in judgment.read_text(encoding="utf-8").splitlines():
        match = PR_LINE.match(line)
        if match:
            return match.group(1), int(match.group(2))
    return None, None


def stack_numbers(ballot: Path, target: int) -> list[int]:
    """Stack PRs bottom to top. Alone, or status other than stack, is the target."""
    path = ballot / "stack.json"
    if not path.is_file():
        return [target]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [target]
    if not isinstance(data, dict) or data.get("status") != "stack":
        return [target]
    numbers: list[int] = []
    for pr in data.get("prs") or []:
        if not isinstance(pr, dict):
            continue
        number = _pr_number(pr.get("number"))
        if number is not None:
            numbers.append(number)
    if target not in numbers:
        numbers.append(target)
    return numbers or [target]


class GhError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def fetch_files(repo: str, number: int) -> dict[str, str]:
    """filename -> patch. Raises GhError. Does not retry."""
    proc = subprocess.run(
        [GH, "api", f"repos/{repo}/pulls/{number}/files", "--paginate"],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        raise GhError(detail)
    try:
        rows = json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        raise GhError(f"non-JSON from pulls/{number}/files: {exc}") from exc
    if not isinstance(rows, list):
        raise GhError(f"pulls/{number}/files: expected a list")
    files: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("filename"), str):
            continue
        patch = row.get("patch")
        files[row["filename"]] = patch if isinstance(patch, str) else ""
    return files


def _unverified(finding: dict, target: int | None, reason: str) -> dict:
    path = finding.get("file")
    path = path.strip() if isinstance(path, str) and path.strip() else None
    line = finding.get("line")
    line = line if _pr_number(line) is not None and line >= 1 else None
    return {
        "post_as": "unverified",
        "pr": target,
        "path": path,
        "line": line,
        "reason": reason,
    }


def _print_anchor(fid: str, anchor: dict) -> None:
    pr = anchor.get("pr")
    pr_text = f"#{pr}" if pr is not None else "-"
    path = anchor.get("path") or "-"
    line = anchor.get("line")
    place = f"{path}:{line}" if line else path
    print(f"{fid} {anchor.get('post_as')} {pr_text} {place} {anchor.get('reason')}")


def anchor_ballot(ballot: Path) -> dict[str, dict]:
    """Resolve every finding. A gh failure unverifies all of them."""
    findings_path = ballot / "findings.json"
    doc = json.loads(findings_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("findings"), list):
        raise SystemExit(f"{findings_path}: needs an object with a findings array")
    findings = [item for item in doc["findings"] if isinstance(item, dict)]
    repo, target = repo_and_pr(ballot)
    anchors: dict[str, dict] = {}
    if repo is None or target is None:
        reason = "no pr line in judgment.md"
        for finding in findings:
            fid = str(finding.get("id"))
            anchors[fid] = _unverified(finding, target, reason)
            _print_anchor(fid, anchors[fid])
        return anchors
    numbers = stack_numbers(ballot, target)
    patches: list[dict] = []
    try:
        for number in numbers:
            patches.append({"number": number, "files": fetch_files(repo, number)})
    except GhError as exc:
        reason = f"gh: {exc.message}"
        for finding in findings:
            fid = str(finding.get("id"))
            anchors[fid] = _unverified(finding, target, reason)
            _print_anchor(fid, anchors[fid])
        return anchors
    for finding in findings:
        fid = str(finding.get("id"))
        anchors[fid] = resolve(finding, patches, target)
        _print_anchor(fid, anchors[fid])
    return anchors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] in ("-h", "--help"):
        raise SystemExit("usage: finding_anchor.py <ballot-dir>")
    ballot = Path(args[0])
    if not ballot.is_dir():
        raise SystemExit(f"no directory {ballot}")
    findings_path = ballot / "findings.json"
    if not findings_path.is_file():
        raise SystemExit(f"missing {findings_path}")
    anchors = anchor_ballot(ballot)
    (ballot / "anchors.json").write_text(
        json.dumps(anchors, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
