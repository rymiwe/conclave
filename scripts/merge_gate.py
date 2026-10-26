#!/usr/bin/env python3
"""Merge-gate helper: read the reviewer's answer, parse its one_way line, merge.

    merge_gate.py extract <gate.stream.jsonl> <gate.md>
    merge_gate.py parse <gate.md>
    merge_gate.py merge <pr_url> <out_dir>

The reviewer ends its answer with `one_way: yes` or `one_way: no`, and on yes
one `one_way_at: <file>:<line> <what>` line per step a git revert cannot undo.
Lines inside code fences are quotes and do not count. A line counts only when
its first token is `one_way:` or `one_way_at:`; prose that mentions one_way is
ignored, and a directive with a value other than yes/no is unparseable.

merge fails closed. It refuses (exit 3, nothing merged) when the verdict is
yes, missing, conflicting, or malformed, when gate-head.txt is missing, when
the PR has the `one-way` label, or when gh cannot read the labels. Otherwise
it runs `gh pr merge <pr> --squash --match-head-commit <reviewed sha>` and
returns gh's exit code. MERGE_GATE_GH overrides the gh binary.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

LABEL = "one-way"
REFUSED = 3
# A directive is a line whose first token is one_way: or one_way_at: (colon
# required). A garbled value after the colon is unparseable; prose is not a directive.
VERDICT = re.compile(r"^one_way\s*:(.*)$", re.I)
DIRECTIVE_AT = re.compile(r"^one_way_at\s*:", re.I)
ITEM = re.compile(r"^one_way_at\s*:\s*(\S+:\d+)\s*(.*)$", re.I)


def _clean(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^(?:[-*>]\s+)+", "", s)
    return s.replace("`", "").replace("**", "").strip()


def parse(text: str) -> dict:
    """{"one_way": "yes"|"no"|None, "items": [...], "error": str|None}."""
    values: list[str] = []
    items: list[dict] = []
    bad: list[str] = []
    fence = False
    for raw in text.splitlines():
        if raw.strip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        line = _clean(raw)
        m = ITEM.match(line)
        if m:
            items.append({"at": m.group(1), "what": m.group(2).strip()})
            continue
        if DIRECTIVE_AT.match(line):
            bad.append(line)
            continue
        m = VERDICT.match(line)
        if m:
            v = m.group(1).strip().lower()
            if v in ("yes", "no"):
                values.append(v)
            else:
                bad.append(line)
        # Anything else that only mentions one_way (prose, a bullet) is ignored.
    out: dict = {"one_way": None, "items": items, "error": None}
    if bad:
        out["error"] = "unparseable one_way line: " + "; ".join(repr(b) for b in bad)
    elif not values:
        out["error"] = "no one_way: yes|no line in the gate answer"
    elif len(set(values)) > 1:
        out["error"] = "conflicting one_way lines: " + ", ".join(values)
    elif values[0] == "no" and items:
        out["error"] = "one_way: no but one_way_at lines are listed"
    else:
        out["one_way"] = values[0]
    return out


def extract(stream: str, md: str) -> int:
    init = res = None
    try:
        lines = Path(stream).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        print(f"merge-gate: cannot read {stream}: {e}", file=sys.stderr)
        lines = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get("event") == "init" and init is None:
            init = e.get("init")
        if e.get("event") == "result":
            res = e.get("result")
    init = init if isinstance(init, dict) else {}
    res = res if isinstance(res, dict) else {}
    r = res.get("response") or ""
    Path(md).write_text(r, encoding="utf-8")
    print("init agent:", init.get("agent"), "| status:", res.get("status"),
          "| denied:", res.get("denied_actions"))
    print("response chars:", len(r))
    if res.get("status") != "SUCCESS" or res.get("denied_actions") or not r.strip():
        print("merge-gate: the reviewer did not return a clean answer", file=sys.stderr)
        return 1
    return 0


def pr_labels(gh: str, pr: str) -> tuple[list[str] | None, str]:
    try:
        p = subprocess.run([gh, "pr", "view", pr, "--json", "labels"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"gh pr view failed: {e}"
    if p.returncode != 0:
        return None, f"gh pr view exited {p.returncode}: {p.stderr.strip()[-300:]}"
    try:
        data = json.loads(p.stdout)
        return [str(l["name"]) for l in data["labels"]], ""
    except (ValueError, KeyError, TypeError) as e:
        return None, f"gh pr view returned no labels list: {e}"


def merge(pr: str, out_dir: str) -> int:
    gh = os.environ.get("MERGE_GATE_GH") or "/usr/local/bin/gh"
    out = Path(out_dir)
    reasons: list[str] = []
    items: list[dict] = []
    md = out / "gate.md"
    if not md.is_file():
        reasons.append(f"no gate answer at {md} (run merge-gate.sh review first)")
    else:
        v = parse(md.read_text(encoding="utf-8", errors="replace"))
        items = v["items"]
        if v["error"]:
            reasons.append(f"gate verdict is missing or unparseable: {v['error']}")
        elif v["one_way"] == "yes":
            reasons.append("gate verdict is one_way: yes"
                           + ("" if items else " (the reviewer listed no file:line)"))
    head = ""
    hf = out / "gate-head.txt"
    if hf.is_file():
        head = hf.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        reasons.append(f"no reviewed head sha in {hf}")
    labels, err = pr_labels(gh, pr)
    if labels is None:
        reasons.append(f"cannot read the PR labels, so the one-way label is unknown: {err}")
    elif any(l.lower() == LABEL for l in labels):
        reasons.append(f"the PR has the `{LABEL}` label")
    if reasons:
        print(f"merge-gate: REFUSED to merge {pr}", file=sys.stderr)
        for r in reasons:
            print(f"reason: {r}", file=sys.stderr)
        for it in items:
            print(f"  one-way step: {it['at']} {it['what']}".rstrip(), file=sys.stderr)
        print("A step that a git revert cannot undo, or a PR whose one-way status is "
              "unknown, needs Ryan's explicit yes in a separate PR. Nothing was merged.",
              file=sys.stderr)
        return REFUSED
    print(f"merge-gate: one_way: no, no `{LABEL}` label; squash-merging {pr} at {head}", flush=True)
    return subprocess.run([gh, "pr", "merge", pr, "--squash",
                           "--match-head-commit", head]).returncode


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "extract":
        return extract(argv[1], argv[2])
    if len(argv) == 2 and argv[0] == "parse":
        print(json.dumps(parse(Path(argv[1]).read_text(encoding="utf-8", errors="replace"))
                         if Path(argv[1]).is_file() else
                         {"one_way": None, "items": [], "error": f"no file {argv[1]}"}))
        return 0
    if len(argv) == 3 and argv[0] == "merge":
        return merge(argv[1], argv[2])
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
