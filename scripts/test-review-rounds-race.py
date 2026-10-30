#!/usr/bin/env python3
"""Review rounds on one synthetic PR: concurrent ballots, stale ballots, and a
rerun of an earlier round after later rounds exist. No git and no gh: review_rounds.py only."""

import json
import os
import subprocess
import sys
import tempfile
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


def record_argv(w, ballot, head, mode, base, planned="-"):
    d = prep(w, ballot)
    return argv("record", "--rounds-dir", str(w / "rounds"), "--pr-url", URL, "--ballot", ballot,
                "--ballot-dir", str(d), "--council-dir", str(w / "council"), "--mode", mode,
                "--base", base, "--head", head, "--planned-latest", planned, "--files-file", str(d / "files.txt"))


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


def race(w, seeded, ballots, *record):
    """Both ballots passed plan. Exactly one records; the loser is refused and noted."""
    res = together(*record)
    codes = sorted(rc for _, rc in res)
    if codes != [0, 3]:
        fail(f"exit codes {codes}: {res}")
    refused = next(o[1] for o, rc in res if rc)
    doc = log(w)
    if len(doc["rounds"]) != seeded + 1:
        fail(f"recorded {len(doc['rounds']) - seeded}, want 1: {doc}")
    won = doc["rounds"][-1]
    lost = next(b for b in ballots if b != won["ballot"])
    want = f"round {won['round']} (ballot {won['ballot']}) reviewed head {won['head'][:12]}"
    for note in (w / "council" / lost / "round-refused.md", w / "rounds" / f"{KEY}.park.md"):
        if want not in note.read_text():
            fail(f"{note} misses the winner: {note.read_text()}")
    if (w / "council" / lost / "round.json").exists():
        fail("the loser wrote round.json")
    return refused


def race_pr(w):
    """Two --pr ballots on an empty log."""
    for b in ("a", "b"):
        if plan(w, b, H1, "pr").stdout.strip() != "1 - 0 -":
            fail("both ballots plan round 1 on an empty log")
    if (w / "rounds" / f"{KEY}.json").exists():
        fail("plan wrote the log")
    return race(w, 0, ("a", "b"), record_argv(w, "a", H1, "pr", "0" * 40), record_argv(w, "b", H1, "pr", "0" * 40))


def race_delta(w):
    """Two --delta ballots plan from one head."""
    record = subprocess.run(record_argv(w, "r1", H1, "pr", "0" * 40), capture_output=True, text=True)
    if record.returncode:
        fail(record.stderr)
    before = (w / "rounds" / f"{KEY}.json").read_bytes()
    for b, h in (("x", H2), ("y", H3)):
        if plan(w, b, h, "delta").stdout.strip() != f"2 {H1} 0 {H1}":
            fail("both delta ballots plan round 2")
    if (w / "rounds" / f"{KEY}.json").read_bytes() != before:
        fail("plan wrote the log")
    refused = race(w, 1, ("x", "y"), record_argv(w, "x", H2, "delta", H1, H1), record_argv(w, "y", H3, "delta", H1, H1))
    if log(w)["rounds"][1]["base"] != H1:
        fail(f"delta log: {log(w)}")
    return refused


def race_cap(w):
    """Two ballots plan round 3 with 2 recorded."""
    seed(w, 2)
    return race(w, 2, ("x", "y"), record_argv(w, "x", H3, "pr", "0" * 40, "2" * 40),
                record_argv(w, "y", H4, "pr", "0" * 40, "2" * 40))


def scenario_b(w):
    """Rounds 1 to 3, then ballot r1 reruns at its head and adds a finding."""
    rec = lambda *x: subprocess.run(record_argv(w, *x), capture_output=True, text=True, check=True)
    rec("r1", H1, "pr", "0" * 40)
    write_findings(w, "r1", "f1", "first finding of round 1")
    rec("r2", H2, "delta", H1, H1)
    write_findings(w, "r2", "f1", "finding of round 2", resolved=["r1-f1"])
    rec("r3", H3, "delta", H2, H2)
    write_findings(w, "r3", "f1", "finding of round 3")
    doc = json.loads((w / "council" / "r1" / "findings.json").read_text())
    doc["findings"].append({"id": "f2", "claim": "added by the round 1 rerun", "file": "a.txt", "line": 2})
    if plan(w, "r1", H1, "pr").stdout.strip() != f"1 - 1 {H3}":
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


def seed(w, count):
    """A log with count recorded rounds."""
    (w / "rounds").mkdir()
    rounds = [{"round": n, "ballot": f"s{n}", "mode": "pr", "base": "0" * 40, "head": str(n) * 40}
              for n in range(1, count + 1)]
    (w / "rounds" / f"{KEY}.json").write_text(json.dumps({"pr_url": URL, "rounds": rounds}))


def stale():
    """A ballot planned at round 2, then sat while round 2 landed: it cannot take
    round 3, however old it is. No clock or pid decides this."""
    with tempfile.TemporaryDirectory() as t:
        w = Path(t)
        seed(w, 2)
        before = log(w)
        rec = subprocess.run(record_argv(w, "stale", H4, "pr", "0" * 40, "1" * 40), capture_output=True, text=True)
        note = w / "council" / "stale" / "round-refused.md"
        if rec.returncode != 3 or log(w) != before or not note.is_file():
            fail(f"stale ballot: {rec.returncode} {rec.stderr} {log(w)}")
        print(f"--- stale record: exit {rec.returncode}; rounds still {len(log(w)['rounds'])}; note: {note.read_text()}", end="")
        p = run("plan", "--rounds-dir", str(w / "rounds"), "--pr-url", URL, "--ballot", "stale", "--head", H4,
                "--mode", "delta", "--planned-latest", H1)
        if p.returncode != 3 or "park note" not in p.stderr or log(w) != before:
            fail(f"stale recheck: {p.returncode} {p.stderr}")
        print(f"--- stale recheck before the legs: exit {p.returncode}: {p.stderr}", end="")


def main():
    refused = None
    for fn in (race_pr, race_delta, race_cap):
        for _ in range(TRIALS):
            with tempfile.TemporaryDirectory() as t:
                refused = fn(Path(t))
        print(f"--- {TRIALS} trials of {fn.__name__}: one ballot recorded; the other refused:")
        print(refused, end="")
    with tempfile.TemporaryDirectory() as t:
        print("--- park note after a round 1 rerun added r1-f2")
        print(scenario_b(Path(t)))
    stale()
    print("test-review-rounds-race: all passed")


if __name__ == "__main__":
    main()
