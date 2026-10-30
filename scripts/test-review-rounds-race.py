#!/usr/bin/env python3
"""Review rounds on one synthetic PR: concurrent ballots, and a rerun of an
earlier round after later rounds exist. No git and no gh: review_rounds.py only."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = str(ROOT / "scripts" / "review_rounds.py")
URL = "https://github.com/example/app/pull/7"
KEY = "github.com_example_app_7"
TRIALS = 25
H1, H2, H3, H4 = ("1" * 40, "2" * 40, "3" * 40, "4" * 40)


def fail(msg):
    print(f"FAIL {msg}", file=sys.stderr)
    sys.exit(1)


def argv(cmd, *rest):
    return [sys.executable, SCRIPT, cmd, *rest]


def run(cmd, *rest):
    return subprocess.run(argv(cmd, *rest), capture_output=True, text=True)


def plan(w, ballot, head, mode):
    return run("plan", "--rounds-dir", str(w / "rounds"), "--pr-url", URL, "--council-dir", str(w / "council"),
               "--ballot", ballot, "--head", head, "--mode", mode)


def prep(w, ballot):
    d = w / "council" / ballot
    d.mkdir(parents=True, exist_ok=True)
    (d / "files.txt").write_text("a.txt\n")
    return d


def record_argv(w, ballot, head, mode, base):
    d = prep(w, ballot)
    return argv("record", "--rounds-dir", str(w / "rounds"), "--pr-url", URL, "--ballot", ballot,
                "--ballot-dir", str(d), "--council-dir", str(w / "council"), "--mode", mode,
                "--base", base, "--head", head, "--files-file", str(d / "files.txt"))


def together(*cmds):
    """Start every command at once: each shell waits for its line on a shared pipe."""
    r, wr = os.pipe()
    procs = [subprocess.Popen(["sh", "-c", 'read x; exec "$@"', "sh", *c], stdin=os.dup(r),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for c in cmds]
    os.write(wr, b"\n" * len(cmds))
    os.close(wr)
    return [(p.communicate(), p.returncode) for p in procs]


def write_findings(w, ballot, fid, claim, resolved=()):
    doc = {"target": "pr 7", "resolved": list(resolved),
           "findings": [{"id": fid, "claim": claim, "file": "a.txt", "line": 1}]}
    (w / "council" / ballot / "findings.json").write_text(json.dumps(doc))


def log(w):
    return json.loads((w / "rounds" / f"{KEY}.json").read_text())


def race_pr(w):
    """Two --pr ballots both plan round 1, then record and snapshot at once."""
    for b in ("a", "b"):
        if plan(w, b, H1, "pr").stdout.strip() != "1 - 0":
            fail("both ballots plan round 1 on an empty log")
    res = together(record_argv(w, "a", H1, "pr", "0" * 40), record_argv(w, "b", H1, "pr", "0" * 40))
    if [rc for _, rc in res] != [0, 0]:
        fail(f"record: {res}")
    write_findings(w, "a", "f1", "finding from ballot a")
    write_findings(w, "b", "f1", "finding from ballot b")
    res = together(*[argv("snapshot", "--rounds-dir", str(w / "rounds"), "--ballot-dir", str(w / "council" / b))
                     for b in ("a", "b")])
    if [rc for _, rc in res] != [0, 0]:
        fail(f"snapshot: {res}")
    doc = log(w)
    numbers = sorted(r["round"] for r in doc["rounds"])
    kept = sorted(r["findings_doc"]["findings"][0]["claim"] for r in doc["rounds"] if "findings_doc" in r)
    if numbers != [1, 2] or kept != ["finding from ballot a", "finding from ballot b"]:
        fail(f"lost an entry: rounds {numbers}, findings {kept}")
    return doc


def race_delta(w):
    """Two --delta ballots plan from one head. One records; the other is refused."""
    prep(w, "r1")
    record = subprocess.run(record_argv(w, "r1", H1, "pr", "0" * 40), capture_output=True, text=True)
    if record.returncode:
        fail(record.stderr)
    for b, h in (("x", H2), ("y", H3)):
        if plan(w, b, h, "delta").stdout.strip() != f"2 {H1} 0":
            fail("both delta ballots plan round 2")
    res = together(record_argv(w, "x", H2, "delta", H1), record_argv(w, "y", H3, "delta", H1))
    codes = sorted(rc for _, rc in res)
    if codes != [0, 2]:
        fail(f"delta exit codes {codes}")
    refused = next(o[1] for o, rc in res if rc)
    if "another ballot recorded first" not in refused:
        fail(f"delta refusal: {refused}")
    doc = log(w)
    if [r["round"] for r in doc["rounds"]] != [1, 2] or doc["rounds"][1]["base"] != H1:
        fail(f"delta log: {doc}")
    return refused


def scenario_b(w):
    """Rounds 1 to 3, then ballot r1 reruns at its head and adds a finding."""
    rec = lambda *x: subprocess.run(record_argv(w, *x), capture_output=True, text=True, check=True)
    rec("r1", H1, "pr", "0" * 40)
    write_findings(w, "r1", "f1", "first finding of round 1")
    rec("r2", H2, "delta", H1)
    write_findings(w, "r2", "f1", "finding of round 2", resolved=["r1-f1"])
    rec("r3", H3, "delta", H2)
    write_findings(w, "r3", "f1", "finding of round 3")
    doc = json.loads((w / "council" / "r1" / "findings.json").read_text())
    doc["findings"].append({"id": "f2", "claim": "added by the round 1 rerun", "file": "a.txt", "line": 2})
    if plan(w, "r1", H1, "pr").stdout.strip() != "1 - 1":
        fail("rerun of round 1 is round 1")
    rec("r1", H1, "pr", "0" * 40)
    (w / "council" / "r1" / "findings.json").write_text(json.dumps(doc))
    p = plan(w, "r4", H4, "delta")
    if p.returncode != 3:
        fail(f"round 4 exit {p.returncode}: {p.stderr}")
    note = (w / "rounds" / f"{KEY}.park.md").read_text()
    for want in ("r1-f2", "r2-f1", "r3-f1"):
        if want not in note:
            fail(f"park note misses {want}:\n{note}")
    if "r1-f1" in note:
        fail(f"park note lists resolved r1-f1:\n{note}")
    return note


def seed(w, count, reservations=()):
    """A log with count recorded rounds and the given reservations."""
    (w / "rounds").mkdir()
    rounds = [{"round": n, "ballot": f"s{n}", "mode": "pr", "base": "0" * 40, "head": str(n) * 40}
              for n in range(1, count + 1)]
    (w / "rounds" / f"{KEY}.json").write_text(json.dumps(
        {"pr_url": URL, "rounds": rounds, "reservations": list(reservations)}))


def held(ballot, pid, age=0):
    return {"ballot": ballot, "head": H3, "owner_pid": pid, "reserved_at": int(time.time()) - age}


def dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def reservations():
    """Live reservations block; a dead owner, an old one, and a released one do not."""
    with tempfile.TemporaryDirectory() as t:
        w = Path(t)
        seed(w, 2, [held("live", os.getpid())])
        p = plan(w, "next", H4, "pr")
        if p.returncode != 3 or "refused round 4" not in p.stderr:
            fail(f"a live reservation does not block: {p.returncode} {p.stderr}")
        note = (w / "rounds" / f"{KEY}.park.md").read_text()
        if "live holds a reservation" not in note:
            fail(f"park note misses the holder:\n{note}")
    for name, stale in (("dead owner", held("gone", dead_pid())), ("expired", held("old", os.getpid(), 3600))):
        with tempfile.TemporaryDirectory() as t:
            w = Path(t)
            seed(w, 2, [stale])
            p = plan(w, "next", H4, "pr")
            if p.stdout.strip() != "3 - 0":
                fail(f"{name} reservation blocked: {p.returncode} {p.stderr}")
            if [r["ballot"] for r in log(w)["reservations"]] != ["next"]:
                fail(f"{name} reservation not dropped: {log(w)}")
    with tempfile.TemporaryDirectory() as t:
        w = Path(t)
        seed(w, 2)
        if plan(w, "a", H3, "pr").returncode:
            fail("plan a")
        rec = subprocess.run(record_argv(w, "a", H3, "pr", "0" * 40), capture_output=True, text=True)
        if rec.returncode or log(w)["reservations"]:
            fail(f"record keeps its reservation: {rec.stderr} {log(w)}")
    with tempfile.TemporaryDirectory() as t:
        w = Path(t)
        seed(w, 3, [held("late", os.getpid())])
        rec = subprocess.run(record_argv(w, "late", H4, "pr", "0" * 40), capture_output=True, text=True)
        if rec.returncode != 3 or not (w / "rounds" / f"{KEY}.park.md").is_file() or log(w)["reservations"]:
            fail(f"cap at record: {rec.returncode} {rec.stderr} {log(w)}")
    with tempfile.TemporaryDirectory() as t:
        w = Path(t)
        seed(w, 0, [held(b, os.getpid()) for b in ("a", "b", "c")])
        p = plan(w, "d", H4, "pr")
        if p.returncode != 3 or "with no round recorded" not in (w / "rounds" / f"{KEY}.park.md").read_text():
            fail(f"cap on an empty log: {p.returncode} {p.stderr}")


def main():
    last = None
    refused = None
    for _ in range(TRIALS):
        with tempfile.TemporaryDirectory() as t:
            last = race_pr(Path(t))
        with tempfile.TemporaryDirectory() as t:
            refused = race_delta(Path(t))
    print(f"--- {TRIALS} trials: two --pr ballots got distinct rounds and kept both findings")
    print(json.dumps(last, indent=2))
    print(f"--- {TRIALS} trials: one of two --delta ballots recorded; the other refused:")
    print(refused, end="")
    with tempfile.TemporaryDirectory() as t:
        print("--- park note after a round 1 rerun added r1-f2")
        print(scenario_b(Path(t)))
    reservations()
    print("--- reservations: live blocks; dead owner and expired do not; record releases; cap at record parks")
    print("test-review-rounds-race: all passed")


if __name__ == "__main__":
    main()
