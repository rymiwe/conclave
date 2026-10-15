#!/usr/bin/env python3
"""Append one phase of a Conclave outcome record.

Astra's three parts happen at different times. A review line is written
later, by a person, so this file is append-only: one JSON line per phase,
joined by run_id. It does not call a vendor and does not read the usage cache.

  before   what the control plane passed in, plus the efforts the launches passed
  after    completed or failed, elapsed seconds; pool draw and builder rounds unknown.
           When the Muse shadow leg was armed, also whether its verdict
           agreed with the Decision. The verdict text is not stored.
  review   accepted, duplicates, and wrong findings per voter, and the decision

A missing lineup or host file leaves those fields "unknown". An explicit
JSON null stays null. An invalid review file writes nothing and exits 2.
"""

import argparse
import json
import os
import signal
import socket
import sys
import time

UNKNOWN = "unknown"


def _text(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    return UNKNOWN


def _load(path):
    """Return the JSON value, unwrapping a one-element list. None if unusable."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, list):
        if len(data) == 1 and isinstance(data[0], dict):
            return data[0]
        return None
    if isinstance(data, dict):
        return data
    return None


def _chair_reused(raw):
    if raw == "1":
        return True
    if raw == "0":
        return False
    return UNKNOWN


def _from_lineup(path):
    data = _load(path)
    if data is None:
        return {
            "builder": UNKNOWN,
            "legal_candidates": UNKNOWN,
            "lineup": UNKNOWN,
            "usage": UNKNOWN,
            "usage_age_s": UNKNOWN,
            "selection_reason": UNKNOWN,
        }
    lineup = data.get("lineup", UNKNOWN)
    builder = UNKNOWN
    if isinstance(lineup, dict) and "builder" in lineup:
        builder = lineup["builder"]
    age = data.get("usage_age_s", UNKNOWN)
    if age is None or isinstance(age, bool) or not isinstance(age, (int, float)):
        age = UNKNOWN
    reason = data.get("rule") or data.get("reason") or UNKNOWN
    if not isinstance(reason, str) or not reason.strip():
        reason = UNKNOWN
    return {
        "builder": builder,
        "legal_candidates": data.get("legal_candidates", UNKNOWN),
        "lineup": lineup,
        "usage": data.get("usage", UNKNOWN),
        "usage_age_s": age,
        "selection_reason": reason,
    }


def _opening_lineup(path, run_id):
    """The before line for this run, if one is already in the file."""
    if not path or not run_id or not os.path.exists(path):
        return None
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return None
    try:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if (isinstance(row, dict) and row.get("phase") == "before"
                    and row.get("run_id") == run_id):
                return row
    finally:
        fh.close()
    return None


def _host(path):
    data = _load(path)
    if data is None:
        return UNKNOWN
    return data


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _review(path):
    """Return (review dict, None) or (None, error). A bad file must not append."""
    if not path:
        return None, "review file required"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"review file unreadable: {exc.__class__.__name__}"
    if not isinstance(data, dict):
        return None, "review file is not an object"
    voters = data.get("voters")
    decision = data.get("decision")
    if not isinstance(voters, list) or not voters:
        return None, "review voters must be a non-empty list"
    if not isinstance(decision, str) or not decision.strip():
        return None, "review decision must be a non-empty string"
    cleaned = []
    for voter in voters:
        if not isinstance(voter, dict):
            return None, "review voter is not an object"
        family = voter.get("family")
        accepted = _count(voter.get("accepted"))
        duplicates = _count(voter.get("duplicates"))
        wrong = _count(voter.get("wrong"))
        if not isinstance(family, str) or not family.strip():
            return None, "review voter family is empty"
        if accepted is None or duplicates is None or wrong is None:
            return None, "review counts must be integers >= 0"
        cleaned.append({
            "family": family.strip(),
            "accepted": accepted,
            "duplicates": duplicates,
            "wrong": wrong,
        })
    return {"voters": cleaned, "decision": decision.strip()}, None


def _voter(name, model, effort, effort_null=False):
    # A blank effort means the launch passed no flag. A passed value is stored.
    del effort_null
    row = {"name": name, "model": _text(model)}
    if not isinstance(effort, str) or not effort.strip():
        row["effort"] = None
    else:
        row["effort"] = effort.strip()
    return row


def _ran(args):
    names = [part.strip() for part in (args.voters or "").split(",") if part.strip()]
    rows = []
    for name in names:
        if name == "claude":
            rows.append(_voter(name, args.ran_claude_model, args.ran_claude_effort))
        elif name == "codex":
            rows.append(_voter(name, args.ran_codex_model, args.ran_codex_effort))
        elif name == "grok":
            rows.append(_voter(name, args.ran_grok_model, args.ran_grok_effort))
        elif name == "opus":
            # Blank means that launch passed no effort flag. A passed value is stored.
            rows.append(_voter(name, args.ran_opus_model or "opus", args.ran_opus_effort, effort_null=True))
        elif name == "muse":
            rows.append(_voter(name, args.ran_muse_model, args.ran_muse_effort))
        else:
            rows.append(_voter(name, "", ""))
    return {"kind": _text(args.kind), "difficulty": _text(args.difficulty), "voters": rows}


_DECISIONS = ("implement", "implement with changes", "no automatic decision")


def _muse_shadow(args):
    """Agreement of the shadow verdict with the Decision. None if not armed."""
    status = (args.muse_status or "").strip()
    if not status:
        return None
    if status not in ("ran", "skipped"):
        return "bad"
    decision = (args.muse_decision or "").strip()
    final = (args.final_decision or "").strip()
    agreed = None
    if status == "ran" and decision in _DECISIONS and final in _DECISIONS:
        agreed = decision == final
    model = args.muse_model if isinstance(args.muse_model, str) else ""
    return {
        "status": status,
        "model": _text(model),
        "agreed": agreed,
    }


def _elapsed(started):
    if started is None or started == "":
        return None
    try:
        start = int(started)
    except (TypeError, ValueError):
        return None
    delta = int(time.time()) - start
    if delta < 0:
        return None
    return delta


def build(args):
    if not args.run_id:
        return None, "run-id is required"
    fields = _from_lineup(args.lineup_file)
    if args.phase == "after":
        earlier = _opening_lineup(args.path, args.run_id)
        if earlier is not None:
            for key in ("builder", "legal_candidates", "lineup", "usage",
                        "usage_age_s", "selection_reason"):
                if key in earlier:
                    fields[key] = earlier[key]
    row = {
        "v": 1,
        "phase": args.phase,
        "run_id": args.run_id,
        "task_id": _text(args.task_id),
        "ts": int(time.time()),
        "machine": socket.gethostname(),
        "kind": _text(args.kind),
        "difficulty": _text(args.difficulty),
        "builder": fields["builder"],
        "host_identity": _host(args.host_file),
        "legal_candidates": fields["legal_candidates"],
        "lineup": fields["lineup"],
        "chair_reused": _chair_reused(args.chair_reused),
        "usage": fields["usage"],
        "usage_age_s": fields["usage_age_s"],
        "selection_reason": fields["selection_reason"],
        "status": None,
        "elapsed_s": None,
        "builder_rounds": None,
        "pool_draw": None,
        "review": None,
        "ran": None,
    }
    if args.phase == "before":
        row["ran"] = _ran(args)
        return row, None
    if args.phase == "after":
        if args.status not in ("completed", "failed"):
            return None, "after status must be completed or failed"
        shadow = _muse_shadow(args)
        if shadow == "bad":
            return None, "muse status must be ran or skipped"
        if shadow is not None:
            row["muse_shadow"] = shadow
        row["status"] = args.status
        row["elapsed_s"] = _elapsed(args.started)
        row["builder_rounds"] = UNKNOWN
        row["pool_draw"] = UNKNOWN
        row["ran"] = _ran(args)
        return row, None
    if args.phase == "review":
        review, err = _review(args.review_file)
        if err:
            return None, err
        row["review"] = review
        return row, None
    return None, f"unknown phase {args.phase}"


def append(path, row):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    previous = None
    try:
        # A size cap delivers SIGXFSZ. Ignore it so a short write can roll back.
        previous = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        start = os.lseek(fd, 0, os.SEEK_END)
        view = memoryview(payload)
        sent = 0
        try:
            while sent < len(view):
                n = os.write(fd, view[sent:])
                if not n:
                    raise OSError("short write")
                sent += n
        except OSError:
            try:
                os.ftruncate(fd, start)
            except OSError:
                pass
            raise
    finally:
        if previous is not None:
            signal.signal(signal.SIGXFSZ, previous)
        os.close(fd)


def main(argv):
    parser = argparse.ArgumentParser(description="Append one Conclave outcome phase.")
    parser.add_argument("--phase", required=True, choices=("before", "after", "review"))
    parser.add_argument("--path", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--kind", default="")
    parser.add_argument("--difficulty", default="")
    parser.add_argument("--started", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--chair-reused", default="")
    parser.add_argument("--lineup-file", default="")
    parser.add_argument("--host-file", default="")
    parser.add_argument("--review-file", default="")
    parser.add_argument("--voters", default="")
    parser.add_argument("--ran-claude-model", default="")
    parser.add_argument("--ran-claude-effort", default="")
    parser.add_argument("--ran-codex-model", default="")
    parser.add_argument("--ran-codex-effort", default="")
    parser.add_argument("--ran-grok-model", default="")
    parser.add_argument("--ran-grok-effort", default="")
    parser.add_argument("--ran-opus-model", default="")
    parser.add_argument("--ran-opus-effort", default="")
    parser.add_argument("--ran-muse-model", default="")
    parser.add_argument("--ran-muse-effort", default="")
    parser.add_argument("--muse-status", default="")
    parser.add_argument("--muse-model", default="")
    parser.add_argument("--muse-decision", default="")
    parser.add_argument("--final-decision", default="")
    args = parser.parse_args(argv)
    row, err = build(args)
    if err:
        print(err, file=sys.stderr)
        return 2
    try:
        append(args.path, row)
    except OSError as exc:
        print(f"outcome write failed: {exc.__class__.__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
