#!/usr/bin/env python3
"""Per-PR review rounds for kind review: the round log, the delta base, and the cap.

One JSON log per pull request under the rounds dir (CONCLAVE_ROUNDS_DIR,
default tmp/council/rounds/). A round is one (ballot id, reviewed head) pair that
completed: at least one reviewer returned a ballot. A rerun of the same ballot at
the same head is the same round. Only completed rounds count against the cap or
move the delta base, so a launch that failed outright changes nothing.

  plan     --rounds-dir D --pr-url U --ballot B --head H --mode pr|delta [--council-dir C]
           [--planned-latest H|-] [--dry-run]
           prints "<round> <delta base or -> <rerun 0|1> <latest recorded head or ->".
           An advisory check: it never writes the log or holds a slot, and record
           assigns the final number. It refuses a new round past the cap. With
           --planned-latest it also refuses when the latest recorded head moved.
           Both refusals exit 3 and write <key>.park.md (not on --dry-run).
  record   --rounds-dir D --pr-url U --ballot B --ballot-dir DIR --council-dir C
           --mode M --base SHA --head SHA --planned-latest H|- --files-file F
           appends the round and writes DIR/round.json and DIR/carried-findings.md.
           A new round past the cap, or one whose planned latest head is no longer
           the latest, records nothing, exits 3, and writes <key>.park.md and
           DIR/round-refused.md, which name the winning round and its head.
  snapshot --rounds-dir D --ballot-dir DIR
           copies DIR/findings.json into that round's log entry. A fresh run of a
           ballot deletes findings.json, so conclave.sh calls this first.

Record and snapshot hold <key>.lock for their whole read-modify-write of the log,
so two ballots on one pull request cannot lose an entry. Plan only reads and takes
no lock: the log is replaced atomically.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MAX_ROUNDS = 3
MODES = ("pr", "delta")


class Refused(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def pr_key(url: str) -> str:
    m = re.match(r"^(?:[a-z]+://)?(?:www\.)?([^/]+)/([^/]+)/([^/]+)/pull/(\d+)", url.strip().lower())
    if not m:
        raise Refused(2, f"review rounds: not a pull URL: {url!r}")
    return "_".join(m.groups())


def log_path(rounds_dir: Path, url: str) -> Path:
    return rounds_dir / f"{pr_key(url)}.json"


def park_path(rounds_dir: Path, url: str) -> Path:
    return rounds_dir / f"{pr_key(url)}.park.md"


@contextlib.contextmanager
def pr_lock(rounds_dir: Path, url: str):
    rounds_dir.mkdir(parents=True, exist_ok=True)
    with open(rounds_dir / f"{pr_key(url)}.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def load_log(path: Path, url: str) -> dict:
    if not path.exists():
        return {"pr_url": url, "rounds": []}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("rounds"), list):
        raise Refused(2, f"review rounds: {path} has no rounds list")
    return doc


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_json(path: Path, doc: dict) -> None:
    write_atomic(path, json.dumps(doc, indent=2) + "\n")


def read_json(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def round_findings(entry: dict, council_dir: Path | None) -> dict | None:
    """The round's findings.json.

    The ballot dir's file wins while that ballot's round.json still names this
    round, so findings the chair revises after a rerun count. Once the ballot id
    moves on to a later round, the log snapshot is the only copy.
    """
    if council_dir is not None:
        ballot = council_dir / str(entry.get("ballot", ""))
        meta = read_json(ballot / "round.json")
        if meta and meta.get("round") == entry["round"] and meta.get("head") == entry["head"]:
            doc = read_json(ballot / "findings.json")
            if doc is not None:
                return doc
    doc = entry.get("findings_doc")
    return doc if isinstance(doc, dict) else None


def find_round(rounds: list[dict], ballot: str, head: str) -> dict | None:
    return next((r for r in rounds if r["ballot"] == ballot and r["head"] == head), None)


def open_findings(rounds: list[dict], council_dir: Path | None) -> list[dict]:
    """Findings still open after the given rounds, in order.

    Replays each round's current findings.json, so a rerun of an earlier round
    counts. Each round adds its own findings and drops the ids it lists under
    "resolved". A round's own ids get an r<N>- prefix so they stay unique across
    rounds.
    """
    still: list[dict] = []
    for entry in rounds:
        doc = round_findings(entry, council_dir) or {}
        resolved = {str(x) for x in doc.get("resolved") or []}
        own = []
        for f in doc.get("findings") or []:
            if not isinstance(f, dict):
                continue
            own.append({
                "id": f"r{entry['round']}-{f.get('id', '?')}",
                "round": entry["round"],
                "claim": f.get("claim", ""),
                "file": f.get("file", ""),
                "line": f.get("line"),
            })
        still = [f for f in still + own if f["id"] not in resolved]
    return still


def findings_md(findings: list[dict]) -> str:
    if not findings:
        return "(none)\n"
    lines = []
    for f in findings:
        where = f.get("file") or "?"
        if f.get("line") is not None:
            where = f"{where}:{f['line']}"
        lines.append(f"- {f['id']} (round {f.get('round', '?')}, {where}): {f.get('claim', '')}")
    return "\n".join(lines) + "\n"


def park_note(doc: dict, reason: str, council_dir: Path | None) -> str:
    last = doc["rounds"][-1]
    still = open_findings(doc["rounds"], council_dir)
    rows = "\n".join(
        f"| {r['round']} | {r['ballot']} | {r['mode']} | {r['base'][:12]}..{r['head'][:12]} |"
        for r in doc["rounds"]
    )
    known = "" if round_findings(last, council_dir) is not None else (
        f"\nRound {last['round']} has no findings.json yet, so its own findings are not listed.\n")
    return (
        f"# Parked: {doc['pr_url']}\n\n"
        f"Stopped: {reason} "
        f"{len(still)} finding(s) are still open after round {last['round']}.\n\n"
        f"A human decides what happens next. Nothing was posted.\n\n"
        f"## Unresolved findings\n\n{findings_md(still)}{known}\n"
        f"## Rounds\n\n| round | ballot | mode | reviewed |\n|---|---|---|---|\n{rows}\n"
    )


def refusal(a, rounds: list[dict]) -> str | None:
    """Why this ballot may not take a new round, or None."""
    last = rounds[-1] if rounds else None
    won = last and f"round {last['round']} (ballot {last['ballot']}) reviewed head {last['head'][:12]}"
    if len(rounds) >= MAX_ROUNDS:
        return (f"refused round {len(rounds) + 1} (ballot {a.ballot}, head {a.head[:12]}): "
                f"the cap is {MAX_ROUNDS} rounds per pull request, and {won}.")
    latest = last["head"] if last else "-"
    if a.planned_latest is not None and a.planned_latest != latest:
        return (f"refused ballot {a.ballot} (head {a.head[:12]}): it planned from "
                f"{a.planned_latest[:12]}, but {won} first; rerun with a new ballot id.")
    return None


def refuse(a, doc: dict, reason: str, council_dir: Path | None, dry_run: bool = False) -> None:
    msg = f"review rounds: {reason} ({a.pr_url})"
    if dry_run:
        raise Refused(3, msg + " (dry run: no park note written)")
    park = park_path(Path(a.rounds_dir), a.pr_url)
    write_atomic(park, park_note(doc, reason, council_dir))
    raise Refused(3, f"{msg}; park note: {park}")


def cmd_plan(a) -> str:
    rounds_dir = Path(a.rounds_dir)
    council_dir = Path(a.council_dir) if a.council_dir else None
    doc = load_log(log_path(rounds_dir, a.pr_url), a.pr_url)
    rounds = doc["rounds"]
    last = rounds[-1] if rounds else None
    latest = last["head"] if last else "-"
    same = find_round(rounds, a.ballot, a.head)
    if same:
        if same["mode"] != a.mode:
            raise Refused(2, f"review rounds: ballot {a.ballot} already reviewed head {a.head[:12]} "
                             f"as mode {same['mode']}; use a new ballot id")
        base = same["base"] if a.mode == "delta" else "-"
        return f"{same['round']} {base} 1 {latest}"
    if a.mode == "delta" and a.planned_latest is None:
        if not last:
            raise Refused(2, f"review rounds: --delta needs an earlier round on {a.pr_url}; run --pr first")
        if last["head"] == a.head:
            raise Refused(2, f"review rounds: nothing to review; round {last['round']} already reviewed head {a.head[:12]}")
    reason = refusal(a, rounds)
    if reason:
        refuse(a, doc, reason, council_dir, a.dry_run)
    base = latest if a.mode == "delta" else "-"
    return f"{len(rounds) + 1} {base} 0 {latest}"


def cmd_record(a) -> str:
    with pr_lock(Path(a.rounds_dir), a.pr_url):
        return record_round(a)


def record_round(a) -> str:
    rounds_dir = Path(a.rounds_dir)
    council_dir = Path(a.council_dir)
    path = log_path(rounds_dir, a.pr_url)
    doc = load_log(path, a.pr_url)
    rounds = doc["rounds"]
    files = [ln for ln in Path(a.files_file).read_text(encoding="utf-8").splitlines() if ln]
    ballot_dir = Path(a.ballot_dir)
    entry = find_round(rounds, a.ballot, a.head)
    if entry is None:
        reason = refusal(a, rounds)
        if reason:
            write_atomic(ballot_dir / "round-refused.md", f"Not recorded: {reason}\n")
            refuse(a, doc, reason, council_dir)
        entry = {
            "round": len(rounds) + 1,
            "ballot": a.ballot,
            "mode": a.mode,
            "base": a.base,
            "head": a.head,
            "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        rounds.append(entry)
    entry["files"] = files
    entry["carried_open"] = open_findings(rounds[:entry["round"] - 1], council_dir)
    # Keep each round's findings in the log: the replay outlives the ballot dir.
    for r in rounds:
        kept = round_findings(r, council_dir)
        if kept is not None:
            r["findings_doc"] = kept
    write_json(path, doc)
    write_json(ballot_dir / "round.json", {
        "pr_url": a.pr_url,
        "round": entry["round"],
        "max_rounds": MAX_ROUNDS,
        "mode": entry["mode"],
        "base": entry["base"],
        "head": entry["head"],
        "files": files,
        "log": str(path),
        "carried_open": entry["carried_open"],
    })
    write_atomic(ballot_dir / "carried-findings.md", (
        f"# Open findings carried into round {entry['round']}\n\n"
        "Chair context only. The reviewer brief does not list them. In this round's\n"
        "findings.json, list the ids this round shows fixed or dismissed under\n"
        "\"resolved\". The rest carry into the next round.\n\n"
        + findings_md(entry["carried_open"])))
    return f"round {entry['round']} of {MAX_ROUNDS}: {path}"


def cmd_snapshot(a) -> str:
    ballot_dir = Path(a.ballot_dir)
    rj, fj = ballot_dir / "round.json", ballot_dir / "findings.json"
    if not rj.is_file() or not fj.is_file():
        return ""
    meta = json.loads(rj.read_text(encoding="utf-8"))
    rounds_dir = Path(a.rounds_dir)
    with pr_lock(rounds_dir, meta["pr_url"]):
        findings = read_json(fj)
        if findings is None:
            return ""
        path = log_path(rounds_dir, meta["pr_url"])
        doc = load_log(path, meta["pr_url"])
        for entry in doc["rounds"]:
            if entry["round"] == meta["round"] and entry["ballot"] == ballot_dir.name:
                entry["findings_doc"] = findings
                write_json(path, doc)
                return f"kept round {entry['round']} findings in {path}"
    return ""


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="review_rounds.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    plan = sub.add_parser("plan")
    rec = sub.add_parser("record")
    snap = sub.add_parser("snapshot")
    for s in (plan, rec, snap):
        s.add_argument("--rounds-dir", required=True)
    for s in (plan, rec):
        s.add_argument("--pr-url", required=True)
        s.add_argument("--ballot", required=True)
        s.add_argument("--head", required=True)
        s.add_argument("--mode", required=True, choices=MODES)
    plan.add_argument("--council-dir")
    plan.add_argument("--dry-run", action="store_true")
    plan.add_argument("--planned-latest")
    rec.add_argument("--council-dir", required=True)
    rec.add_argument("--ballot-dir", required=True)
    rec.add_argument("--base", required=True)
    rec.add_argument("--planned-latest", required=True)
    rec.add_argument("--files-file", required=True)
    snap.add_argument("--ballot-dir", required=True)
    a = p.parse_args(argv)
    try:
        out = {"plan": cmd_plan, "record": cmd_record, "snapshot": cmd_snapshot}[a.cmd](a)
    except Refused as e:
        print(str(e), file=sys.stderr)
        return e.code
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
