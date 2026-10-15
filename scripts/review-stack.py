#!/usr/bin/env python3
"""Find the open PR stack for a review-kind --pr run.

Writes stack.md and stack.json in the ballot directory. Prints the
judgment header value (none, unknown, or the bottom-to-top chain) on
stdout. A gh failure is reported in those files and still exits 0.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

CAP = 20
ALONE = (
    "No stack: no open PR builds on this branch and its base is not "
    "another open PR's head."
)
PR_JSON = (
    "number,title,url,body,headRefName,baseRefName,"
    "isCrossRepository,headRepositoryOwner"
)


def gh_json(repo: str, args: list[str]):
    proc = subprocess.run(
        ["gh", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    shown = " ".join(args)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"gh {shown} failed: {err}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh {shown} returned non-json: {exc}") from exc


def norm(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("number") is None:
        raise RuntimeError("gh returned a PR without a number")
    number = data["number"]
    if isinstance(number, bool) or not isinstance(number, int):
        raise RuntimeError(f"gh returned a non-integer PR number: {number!r}")
    cross = data.get("isCrossRepository")
    if not isinstance(cross, bool):
        raise RuntimeError("gh returned a PR without isCrossRepository")
    owner = ""
    raw_owner = data.get("headRepositoryOwner")
    if isinstance(raw_owner, dict) and isinstance(raw_owner.get("login"), str):
        owner = raw_owner["login"]
    return {
        "number": number,
        "title": data.get("title") or "",
        "url": data.get("url") or "",
        "body": data.get("body") or "",
        "base": data.get("baseRefName") or "",
        "head": data.get("headRefName") or "",
        "files": [],
        "changedFiles": None,
        "cross": cross,
        "owner": owner,
        "target": False,
    }


def pr_view(repo: str, number: int) -> dict:
    data = gh_json(
        repo,
        ["pr", "view", str(number), "--json", PR_JSON],
    )
    return norm(data)


def pr_file_info(repo: str, number: int) -> tuple[list[str], int]:
    data = gh_json(repo, ["pr", "view", str(number), "--json", "files,changedFiles"])
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise RuntimeError(f"gh pr view {number} --json files returned no files list")
    changed = data.get("changedFiles")
    if isinstance(changed, bool) or not isinstance(changed, int) or changed < 0:
        raise RuntimeError(f"gh pr view {number} returned no changedFiles")
    paths: list[str] = []
    for item in data["files"]:
        path = ""
        if isinstance(item, dict):
            raw = item.get("path") or item.get("filename") or ""
            if isinstance(raw, str):
                path = raw
        elif isinstance(item, str):
            path = item
        if path:
            paths.append(path)
    return paths, changed


def default_branch(repo: str) -> str:
    data = gh_json(repo, ["repo", "view", "--json", "defaultBranchRef"])
    ref = data.get("defaultBranchRef") if isinstance(data, dict) else None
    name = ref.get("name") if isinstance(ref, dict) else ""
    if not isinstance(name, str) or not name.strip():
        raise RuntimeError("gh repo view returned no default branch")
    return name.strip()


def listed(repo: str, flag: str, branch: str) -> list[dict]:
    if not branch:
        return []
    data = gh_json(
        repo,
        [
            "pr",
            "list",
            "--state",
            "open",
            f"--{flag}",
            branch,
            "--limit",
            "100",
            "--json",
            PR_JSON,
        ],
    )
    if not isinstance(data, list):
        raise RuntimeError(f"gh pr list --{flag} {branch} did not return a list")
    rows = []
    for item in data:
        rec = norm(item)
        if flag == "head" and rec["head"] != branch:
            continue
        if flag == "base" and rec["base"] != branch:
            continue
        rows.append(rec)
    rows.sort(key=lambda rec: rec["number"])
    return rows


def take(rec: dict, seen: set[int], truncated: list[bool]) -> dict | None:
    if rec["number"] in seen:
        return None
    if len(seen) >= CAP:
        truncated[0] = True
        return None
    seen.add(rec["number"])
    return rec


def header_value(ordered: list[dict], target: int) -> str:
    parts = []
    for rec in ordered:
        if rec["number"] == target:
            parts.append(f"#{rec['number']} (target)")
        else:
            parts.append(f"#{rec['number']}")
    return " -> ".join(parts)


def write_stack_md(dest: Path, ordered: list[dict], target: int, truncated: bool) -> None:
    chunks: list[str] = []
    if truncated:
        chunks.append("Walk stopped at 20 open PRs.")
    for rec in ordered:
        mark = " (target)" if rec["number"] == target else ""
        lines = [
            f"#{rec['number']}{mark}",
            f"title: {rec['title']}",
            f"url: {rec['url']}",
            f"base: {rec['base']}",
            f"head: {rec['head']}",
            "files:",
        ]
        if rec["files"]:
            lines.extend(f"- {path}" for path in rec["files"])
        else:
            lines.append("- (none)")
        lines.append("body:")
        lines.append(rec["body"] if rec["body"] else "(empty)")
        chunks.append("\n".join(lines))
    (dest / "stack.md").write_text("\n\n".join(chunks) + "\n", encoding="utf-8")


def write_stack_json(dest: Path, ordered: list[dict], target: int, header: str, truncated: bool) -> None:
    payload = {
        "status": "stack",
        "header": header,
        "target": target,
        "truncated": truncated,
        "prs": ordered,
    }
    (dest / "stack.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def write_unknown(dest: Path, message: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "stack.md").write_text(
        f"Stack detection failed.\n\n{message}\n",
        encoding="utf-8",
    )
    (dest / "stack.json").write_text(
        json.dumps(
            {
                "status": "unknown",
                "header": "unknown",
                "target": None,
                "error": message,
                "prs": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_alone(dest: Path, target: int) -> None:
    (dest / "stack.md").write_text(ALONE + "\n", encoding="utf-8")
    (dest / "stack.json").write_text(
        json.dumps(
            {
                "status": "none",
                "header": "none",
                "target": target,
                "truncated": False,
                "prs": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def walk(repo: str, pr: str, dest: Path) -> str:
    if not re.fullmatch(r"[0-9]+", pr):
        raise RuntimeError(f"bad PR number {pr!r}")
    default = default_branch(repo)
    target = pr_view(repo, int(pr))
    target["target"] = True
    seen = {target["number"]}
    truncated = [False]

    layers: list[list[dict]] = []
    # A cross-repo target is alone. Do not walk down to a same-repo parent.
    frontier = [] if target["cross"] else [target]
    while frontier and not truncated[0]:
        layer: list[dict] = []
        nxt: list[dict] = []
        for node in frontier:
            for rec in listed(repo, "head", node["base"]):
                # A fork PR is not a parent. Neither is a PR whose head is
                # the default branch.
                if rec["cross"] or rec["head"] == default:
                    continue
                added = take(rec, seen, truncated)
                if added is None:
                    if truncated[0]:
                        break
                    continue
                layer.append(added)
                nxt.append(added)
            if truncated[0]:
                break
        if not layer:
            break
        layers.append(layer)
        frontier = [] if truncated[0] else nxt

    bottom: list[dict] = []
    for layer in reversed(layers):
        bottom.extend(layer)

    up: list[dict] = []
    # A cross-repo target has no upward walk. Do not walk up from a head
    # that is the default branch.
    if not target["cross"]:
        frontier = [target]
        while frontier and not truncated[0]:
            nxt = []
            for node in frontier:
                if node["head"] == default:
                    continue
                for rec in listed(repo, "base", node["head"]):
                    if rec["cross"]:
                        continue
                    added = take(rec, seen, truncated)
                    if added is None:
                        if truncated[0]:
                            break
                        continue
                    up.append(added)
                    nxt.append(added)
                if truncated[0]:
                    break
            frontier = [] if truncated[0] else nxt

    ordered = bottom + [target] + up
    if len(ordered) == 1:
        write_alone(dest, target["number"])
        return "none"
    for rec in ordered:
        paths, changed = pr_file_info(repo, rec["number"])
        rec["files"] = paths
        rec["changedFiles"] = changed
    header = header_value(ordered, target["number"])
    write_stack_md(dest, ordered, target["number"], truncated[0])
    write_stack_json(dest, ordered, target["number"], header, truncated[0])
    return header


def main() -> None:
    if len(sys.argv) != 4:
        write_unknown(Path("."), "usage: review-stack.py REPO PR DEST")
        print("unknown")
        return
    repo, pr, dest_s = sys.argv[1:]
    dest = Path(dest_s)
    try:
        print(walk(repo, pr, dest))
    except Exception as exc:
        write_unknown(dest, str(exc))
        print("unknown")


if __name__ == "__main__":
    main()
