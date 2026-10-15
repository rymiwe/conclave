#!/usr/bin/env python3
"""Write evidence-index.md: one row per posted finding, linked to its evidence.

  scripts/evidence-index.py <id> [<id> ...] [--out FILE]

Each <id> is a stack or ballot directory under tmp/council/ that holds a
posted.json from scripts/post-review.py. For every posted finding the index
gives the comment link, severity when known, the claim, the evidence from
that ballot's findings.json, and the chair transcripts that ran the repro.

The file goes to tmp/council/<first id>/evidence-index.md unless --out says
otherwise. It stays under tmp/council/, which is gitignored: ballot content
never leaves the machine that ran the ballot.

Exit 0 when every posted finding was found in its findings.json, 1 when the
index has gaps (it is still written, with the gap named in the row).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cell(text: object, limit: int = 0) -> str:
    s = " ".join(str(text).split()).replace("|", "\\|")
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text.rstrip()}\n{ticks}"


def load_findings(council: Path, ballot: str, cache: dict, notes: dict | None = None) -> dict | None:
    if ballot not in cache:
        path = council / ballot / "findings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache[ballot] = None
            return None
        if not isinstance(data, dict):
            message = f"{ballot}/findings.json is not a JSON object"
            print(f"evidence-index: {message}", file=sys.stderr)
            if notes is not None:
                notes[ballot] = message
            cache[ballot] = None
            return None
        findings = data.get("findings", [])
        if not isinstance(findings, list):
            message = f"{ballot}/findings.json findings is not a list"
            print(f"evidence-index: {message}", file=sys.stderr)
            if notes is not None:
                notes[ballot] = message
            cache[ballot] = None
            return None
        mapped = {}
        for item in findings:
            if isinstance(item, dict):
                mapped[item.get("id")] = item
        cache[ballot] = mapped
    return cache[ballot]


def transcripts(council: Path, ballot: str) -> list[str]:
    hits = sorted(council.glob(f"*/logs/{ballot}-chair-*.jsonl"))
    hits += sorted((council / ballot).glob("*chair*.jsonl"))
    return [str(p.relative_to(council)) for p in hits]


def pr_sort(pr: str) -> tuple:
    return (0, int(pr)) if pr.isdigit() else (1, pr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--out")
    ap.add_argument("--council-dir", default=str(ROOT / "tmp" / "council"),
                    help="where ballot directories live (tests only)")
    args = ap.parse_args()
    council = Path(args.council_dir)

    rows: list[dict] = []
    for ident in args.ids:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", ident):
            raise SystemExit(f"evidence-index: unsafe id {ident!r}")
        path = council / ident / "posted.json"
        if not path.is_file():
            raise SystemExit(f"evidence-index: no {path}; post with scripts/post-review.py first")
        posted = json.loads(path.read_text(encoding="utf-8"))
        for pr, review in posted.get("reviews", {}).items():
            for key, f in review.get("findings", {}).items():
                rows.append({"pr": pr, "key": key, "review": review, **f})
    rows.sort(key=lambda r: (pr_sort(r["pr"]), r["ballot"], int(r["id"][1:])))

    cache: dict = {}
    notes: dict[str, str] = {}
    gaps = 0
    table = ["| PR | ballot | id | severity | posted as | link |", "|---|---|---|---|---|---|"]
    sections: list[str] = []
    for r in rows:
        found = load_findings(council, r["ballot"], cache, notes)
        f = (found or {}).get(r["id"])
        severity = r.get("severity") or (f or {}).get("severity") or "-"
        link = r.get("url") or r["review"].get("review_url", "")
        table.append(f"| {r['pr']} | {r['ballot']} | {r['id']} | {cell(severity)} | {r['posted_as']} | {link} |")
        lines = [f"## {r['pr']} · {r['ballot']} {r['id']} · {severity}", "",
                 f"- posted: {link} ({r['posted_as']})"]
        if r.get("reason"):
            lines.append(f"- note: {r['reason']}")
        if f is None:
            gaps += 1
            where = f"{r['ballot']}/findings.json"
            note = notes.get(r["ballot"])
            if note:
                lines.append(f"- gap: {note}")
            elif found is not None:
                lines.append(f"- gap: {r['id']} not found in {where}")
            else:
                lines.append(f"- gap: no readable {where}")
            sections.append("\n".join(lines) + "\n")
            continue
        if f.get("file"):
            lines.append(f"- code: `{f['file']}:{f.get('line', '?')}`")
        lines.append(f"- claim: {cell(f.get('claim', ''))}")
        lines.append(f"- repro: {'executed' if f.get('repro_executed') else 'not run'}")
        logs = transcripts(council, r["ballot"])
        lines.append(f"- transcripts: {', '.join(logs) if logs else 'none found'}")
        lines += ["", "Evidence:", "", fence(str(f.get("evidence", "")))]
        sections.append("\n".join(lines) + "\n")

    title = ", ".join(args.ids)
    executed = sum(1 for r in rows if (load_findings(council, r["ballot"], cache, notes) or {}).get(r["id"], {}).get("repro_executed"))
    out = "\n".join([
        f"# Evidence index: {title}", "",
        f"{len(rows)} posted findings, {executed} with an executed repro, {gaps} gap(s). "
        "Local only: ballot content stays on the machine that ran it.", "",
        *table, "", *sections,
    ])
    out_path = Path(args.out) if args.out else council / args.ids[0] / "evidence-index.md"
    out_path.write_text(out, encoding="utf-8")
    print(f"{out_path}: {len(rows)} rows, {gaps} gap(s)")
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
