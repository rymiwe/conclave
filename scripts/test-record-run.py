#!/usr/bin/env python3
"""Outcome record: temp path only. The real runs.jsonl must stay untouched."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "record-run.py"
COUNCIL = ROOT / "scripts" / "council.sh"
REAL = Path.home() / ".local/state/seat-usage/runs.jsonl"

passed = 0
failed = 0


def check(cond, name):
    global passed, failed
    if cond:
        passed += 1
        print(f"ok {name}")
    else:
        failed += 1
        print(f"FAIL {name}")


def stamp(path):
    if not path.exists():
        return None
    st = path.stat()
    return (st.st_mtime_ns, st.st_size)


def run_py(args):
    return subprocess.run(
        ["python3", str(SCRIPT), *args],
        text=True, capture_output=True,
    )


def lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def parsed_lines(path):
    rows = []
    bad = 0
    if not path.exists():
        return rows, bad
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    return rows, bad


def jsonl_intact(path):
    if not path.exists() or path.stat().st_size == 0:
        return True
    _, bad = parsed_lines(path)
    return bad == 0


def efforts_of(path):
    row = lines(path)[0]
    return {v["name"]: v["effort"] for v in row["ran"]["voters"]}


def drive(tmp, path, body):
    shell = tmp / "drive-extra.sh"
    shell.write_text("#!/bin/bash\nset -eu\n" + body)
    return subprocess.run(["bash", str(shell)], text=True, capture_output=True)


def main():
    before = stamp(REAL)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        path = tmp / "runs.jsonl"
        lineup = tmp / "lineup.json"
        host = tmp / "host.json"
        review = tmp / "review.json"
        bad = tmp / "bad.json"
        lineup.write_text(json.dumps([{
            "role": "lineup",
            "rule": "xAI chairs and builds: Fable and Astra vote",
            "reason": "xAI chairs and builds: Fable and Astra vote",
            "lineup": {
                "builder": {"family": "grok", "seat": "cursor", "effort": "high"},
                "chair": {"family": "grok", "seat": "cursor"},
                "voters": [{"family": "fable", "effort": "high"}],
            },
            "legal_candidates": [{"rule": "xAI chairs and builds: Fable and Astra vote"}],
            "usage": {"cursor_models": {"state": "under", "used_pct": 10}},
            "usage_age_s": 12,
        }]))
        host.write_text(json.dumps({
            "suggestion": "grokbot", "selects": False, "never": "grok-build",
        }))
        common = [
            "--path", str(path),
            "--run-id", "task-1-100",
            "--task-id", "task-1",
            "--kind", "design",
            "--difficulty", "high",
            "--chair-reused", "1",
            "--lineup-file", str(lineup),
            "--host-file", str(host),
            "--voters", "claude,codex",
            "--ran-claude-model", "fable",
            "--ran-claude-effort", "high",
            "--ran-codex-model", "gpt-6-astra",
            "--ran-codex-effort", "medium",
        ]
        before_rc = run_py(["--phase", "before", *common])
        check(before_rc.returncode == 0, "before exits 0")
        got = lines(path)
        check(len(got) == 1 and got[0]["phase"] == "before", "before appends one line")
        row = got[0]
        check(row["lineup"]["builder"]["family"] == "grok", "before keeps the lineup builder")
        check(row["legal_candidates"][0]["rule"].startswith("xAI"), "before keeps legal candidates")
        check(row["usage_age_s"] == 12, "before keeps usage age")
        check(row["selection_reason"].startswith("xAI"), "before keeps the selection reason")
        check(row["host_identity"]["selects"] is False, "before keeps the host suggestion")
        check(row["chair_reused"] is True, "chair reused 1 is true")
        check(row["builder_rounds"] is None and row["pool_draw"] is None, "before has no pool draw")
        check(row["status"] is None and row["review"] is None, "before has no status or review")
        efforts = {v["name"]: v["effort"] for v in row["ran"]["voters"]}
        check(efforts == {"claude": "high", "codex": "medium"}, "ran records resolved efforts, not a fixed high")
        check("machine" in row and "host" not in row, "hostname field is machine")

        after_rc = run_py(["--phase", "after", "--status", "failed", "--started", "100", *common])
        check(after_rc.returncode == 0, "after exits 0")
        got = lines(path)
        check(len(got) == 2 and got[1]["phase"] == "after", "after appends a second line")
        check(got[0]["phase"] == "before", "after does not rewrite the before line")
        check(got[1]["run_id"] == got[0]["run_id"] == "task-1-100", "phases share run_id")
        check(got[1]["status"] == "failed", "after records failed")
        check(got[1]["pool_draw"] == "unknown" and got[1]["builder_rounds"] == "unknown",
              "after pool draw and builder rounds stay unknown")
        check(isinstance(got[1]["elapsed_s"], int) and got[1]["elapsed_s"] >= 0,
              "after records elapsed seconds")

        bad.write_text("{not json")
        size = path.stat().st_size
        bad_rc = run_py(["--phase", "review", "--path", str(path), "--run-id", "task-1-100",
                         "--review-file", str(bad)])
        check(bad_rc.returncode == 2, "invalid review exits 2")
        check(path.stat().st_size == size, "invalid review writes nothing")

        review.write_text(json.dumps({
            "decision": "rework",
            "voters": [
                {"family": "fable", "accepted": 1, "duplicates": 0, "wrong": 2},
                {"family": "astra", "accepted": True, "duplicates": 0, "wrong": 0},
            ],
        }))
        bool_rc = run_py(["--phase", "review", "--path", str(path), "--run-id", "task-1-100",
                          "--review-file", str(review)])
        check(bool_rc.returncode == 2 and path.stat().st_size == size, "a bool count is not an integer")

        review.write_text(json.dumps({
            "decision": "rework",
            "voters": [
                {"family": "fable", "accepted": 1, "duplicates": 0, "wrong": 2},
                {"family": "astra", "accepted": 2, "duplicates": 1, "wrong": 0},
            ],
        }))
        rev_rc = run_py(["--phase", "review", "--path", str(path), "--run-id", "task-1-100",
                         "--task-id", "task-1", "--review-file", str(review)])
        check(rev_rc.returncode == 0, "review exits 0")
        got = lines(path)
        check(len(got) == 3 and got[2]["phase"] == "review", "review appends a third line")
        check(got[2]["review"]["decision"] == "rework", "review keeps the decision")
        check(got[2]["review"]["voters"][0]["wrong"] == 2, "review keeps wrong findings")
        check(got[2]["pool_draw"] is None, "review does not invent a pool draw")

        missing = run_py(["--phase", "before", "--path", str(path), "--run-id", "no-files",
                          "--lineup-file", str(tmp / "missing.json")])
        check(missing.returncode == 0, "missing lineup file still writes")
        last = lines(path)[-1]
        check(last["lineup"] == "unknown" and last["usage"] == "unknown",
              "a missing lineup file is unknown, not a guessed lineup")
        check(last["host_identity"] == "unknown" and last["chair_reused"] == "unknown",
              "missing host and chair reuse stay unknown")

        ask = tmp / "ask.json"
        ask.write_text(json.dumps({"lineup": None, "reason": "no clean voter pair: ask Ryan",
                                    "legal_candidates": [], "usage_age_s": None}))
        ask_path = tmp / "ask-runs.jsonl"
        ask_rc = run_py(["--phase", "before", "--path", str(ask_path), "--run-id", "ask-1",
                         "--lineup-file", str(ask), "--chair-reused", "yes"])
        check(ask_rc.returncode == 0, "ask lineup exits 0")
        asked = lines(ask_path)[0]
        check(asked["lineup"] is None, "an explicit null lineup stays null")
        check(asked["selection_reason"].startswith("no clean"), "ask reason is kept")
        check(asked["usage_age_s"] == "unknown", "null usage age is unknown")
        check(asked["chair_reused"] == "unknown", "chair reused accepts only 0 or 1")

        none_id = run_py(["--phase", "before", "--path", str(path), "--run-id", ""])
        check(none_id.returncode == 2, "empty run-id exits 2")

        # Bash: the trap appends after, subshells do not, and the exit status stays.
        shell = tmp / "drive.sh"
        shell.write_text(f"""#!/bin/bash
set -eu
export COUNCIL_SOURCE_ONLY=1
export SEAT_USAGE_RUNS={path}
export COUNCIL_LINEUP_JSON={lineup}
export COUNCIL_CHAIR_REUSED=0
. {COUNCIL}
ID=task-2
KIND=design
DIFFICULTY=med
VOTERS=claude,codex,opus
CLAUDE_MODEL_ALIAS=fable
CLAUDE_EFFORT=high
CODEX_MODEL=gpt-6-astra
CODEX_EFFORT=medium
GROK_NATIVE_MODEL=grok-4.7
GROK_EFFORT=low
: > {path}
council_begin_run
( exit 3 ) || true
sleep 0 &
wait
phases=$(python3 -c 'import json,sys; print(",".join(json.loads(l)["phase"] for l in open(sys.argv[1]) if l.strip()))' {path})
[ "$phases" = "before" ]
set +e
council_record_review {bad}
rev=$?
set -e
[ "$rev" = 2 ]
exit 7
""")
        proc = subprocess.run(["bash", str(shell)], text=True, capture_output=True)
        check(proc.returncode == 7, f"trap keeps exit 7 (got {proc.returncode})")
        got = lines(path)
        phases = [row["phase"] for row in got]
        check(phases == ["before", "after"], f"parent writes before and after, subshell writes nothing ({phases})")
        check(got[1]["status"] == "failed", "exit 7 is recorded as failed")
        check(got[1]["chair_reused"] is False, "COUNCIL_CHAIR_REUSED=0 is false")
        opus = [v for v in got[0]["ran"]["voters"] if v["name"] == "opus"]
        check(len(opus) == 1 and opus[0]["effort"] is None, "opus effort is null because the launch passes no flag")
        grok = [v for v in got[0]["ran"]["voters"] if v["name"] == "grok"]
        check(grok == [], "a voter that was not named is not recorded")
        codex = [v for v in got[0]["ran"]["voters"] if v["name"] == "codex"][0]
        check(codex["effort"] == "medium", "bash ran effort is the resolved medium, not high")

        # --help and a sourced exit must not create a runs file anywhere we can see.
        quiet = tmp / "quiet.jsonl"
        home = tmp / "home"
        home.mkdir()
        help_proc = subprocess.run(
            ["bash", str(COUNCIL), "--help"],
            text=True, capture_output=True,
            env={**os.environ, "HOME": str(home), "SEAT_USAGE_RUNS": str(quiet)},
        )
        check(help_proc.returncode == 0, "council --help exits 0")
        check(not quiet.exists(), "--help writes no outcome file")
        default_under_home = home / ".local/state/seat-usage/runs.jsonl"
        check(not default_under_home.exists(), "--help does not create the default runs file")

        sourced = subprocess.run(
            ["bash", "-c", f"export COUNCIL_SOURCE_ONLY=1; . {COUNCIL}; exit 4"],
            text=True, capture_output=True,
            env={**os.environ, "HOME": str(home), "SEAT_USAGE_RUNS": str(quiet)},
        )
        check(sourced.returncode == 4, f"sourced council exits 4 (got {sourced.returncode})")
        check(not quiet.exists() and not default_under_home.exists(),
              "sourcing council.sh writes no outcome file")

        # f1: eight starts together must not share one run id.
        id_path = tmp / "ids.jsonl"
        starter = tmp / "start-one.sh"
        starter.write_text(f"""#!/bin/bash
export COUNCIL_SOURCE_ONLY=1
export SEAT_USAGE_RUNS={id_path}
. {COUNCIL}
ID=same-task
council_begin_run
""")
        procs = [subprocess.Popen(["bash", str(starter)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(8)]
        codes = [p.wait() for p in procs]
        check(codes == [0] * 8, f"eight parallel starts exit 0 ({codes})")
        id_rows, id_bad = parsed_lines(id_path)
        id_set = {row.get("run_id") for row in id_rows}
        check(id_bad == 0 and len(id_rows) == 16 and len(id_set) == 8,
              f"eight starts write sixteen lines and eight ids (bad={id_bad} lines={len(id_rows)} ids={len(id_set)})")

        # f2: a size cap must not leave a partial line and report success.
        cap_path = tmp / "capped.jsonl"
        fat = tmp / "fat.json"
        fat.write_text(json.dumps({"rule": "x" * 800, "lineup": None}))
        cap = subprocess.run(
            ["prlimit", "--fsize=400", "python3", str(SCRIPT),
             "--phase", "before", "--path", str(cap_path),
             "--run-id", "cap-1", "--task-id", "cap", "--kind", "design",
             "--voters", "claude", "--ran-claude-model", "fable",
             "--ran-claude-effort", "high", "--lineup-file", str(fat)],
            text=True, capture_output=True,
        )
        cap_size = cap_path.stat().st_size if cap_path.exists() else 0
        check(cap.returncode != 0 and jsonl_intact(cap_path),
              f"short write fails and leaves no partial line (rc={cap.returncode} size={cap_size} err={cap.stderr.strip()})")

        # f7: the after line keeps the before lineup when the file changes.
        lineup_b = tmp / "lineup-b.json"
        host_b = tmp / "host-b.json"
        lineup_b.write_text(json.dumps({
            "rule": "other lineup",
            "lineup": {"builder": {"family": "opus", "seat": "claude", "effort": "low"}},
            "legal_candidates": [{"rule": "other"}],
            "usage": {"cursor_models": {"state": "capped"}},
            "usage_age_s": 99,
        }))
        host_b.write_text(json.dumps({"suggestion": "opus", "selects": False}))
        kept = tmp / "kept.jsonl"
        keep_before = run_py(["--phase", "before", "--path", str(kept), "--run-id", "keep-1",
                              "--lineup-file", str(lineup), "--host-file", str(host)])
        keep_after = run_py(["--phase", "after", "--status", "completed", "--started", "100",
                             "--path", str(kept), "--run-id", "keep-1",
                             "--lineup-file", str(lineup_b), "--host-file", str(host_b)])
        check(keep_before.returncode == 0 and keep_after.returncode == 0, "keep-lineup writes both phases")
        kept_rows = lines(kept)
        check(kept_rows[1]["builder"] == kept_rows[0]["builder"]
              and kept_rows[1]["selection_reason"] == kept_rows[0]["selection_reason"]
              and kept_rows[1]["usage_age_s"] == kept_rows[0]["usage_age_s"],
              f"after keeps the before lineup ({kept_rows[1]['builder']}, {kept_rows[1]['selection_reason']})")
        check(kept_rows[1]["host_identity"]["suggestion"] == "opus",
              "after still reads the host file it was given")
        lone = tmp / "lone.jsonl"
        lone_rc = run_py(["--phase", "after", "--status", "completed", "--started", "100",
                          "--path", str(lone), "--run-id", "no-before",
                          "--lineup-file", str(lineup_b)])
        check(lone_rc.returncode == 0 and lines(lone)[0]["builder"]["family"] == "opus",
              "after with no before line keeps the file it was given")

        # f4 and f5: record the effort the launch passes, by kind.
        review_path = tmp / "review-kind.jsonl"
        review_drive = drive(tmp, review_path, f"""
export COUNCIL_SOURCE_ONLY=1
export SEAT_USAGE_RUNS={review_path}
. {COUNCIL}
ID=review-task
KIND=review
VOTERS=claude,codex,opus,grok
CLAUDE_MODEL_ALIAS=fable
CLAUDE_EFFORT=low
CODEX_MODEL=gpt-6-astra
CODEX_EFFORT=medium
GROK_NATIVE_MODEL=grok-4.7
GROK_EFFORT=low
council_begin_run
exit 0
""")
        check(review_drive.returncode == 0, f"review record exits 0 ({review_drive.returncode} {review_drive.stderr})")
        review_efforts = efforts_of(review_path)
        check(review_efforts == {"claude": "high", "codex": "medium", "opus": "high", "grok": "high"},
              f"review records launch efforts ({review_efforts})")

        opus_seat = tmp / "opus-seat.jsonl"
        opus_drive = drive(tmp, opus_seat, f"""
export COUNCIL_SOURCE_ONLY=1
export SEAT_USAGE_RUNS={opus_seat}
. {COUNCIL}
ID=opus-seat
KIND=review
CLAUDE_SKU=opus
VOTERS=claude
CLAUDE_MODEL_ALIAS=opus
CLAUDE_EFFORT=high
council_begin_run
exit 0
""")
        opus_efforts = efforts_of(opus_seat) if opus_drive.returncode == 0 and opus_seat.exists() else None
        check(opus_drive.returncode == 0 and opus_efforts == {"claude": "high"},
              f"review opus seat records high, not the host knob ({opus_drive.returncode} {opus_efforts})")

        audit_path = tmp / "audit-kind.jsonl"
        audit_drive = drive(tmp, audit_path, f"""
export COUNCIL_SOURCE_ONLY=1
export SEAT_USAGE_RUNS={audit_path}
. {COUNCIL}
ID=audit-task
KIND=audit
VOTERS=claude,codex,opus,grok
CLAUDE_MODEL_ALIAS=fable
CLAUDE_EFFORT=low
CODEX_MODEL=gpt-6-astra
CODEX_EFFORT=low
GROK_NATIVE_MODEL=grok-4.7
GROK_EFFORT=low
council_begin_run
exit 0
""")
        check(audit_drive.returncode == 0, f"audit record exits 0 ({audit_drive.returncode})")
        audit_efforts = efforts_of(audit_path)
        check(audit_efforts == {"claude": "high", "codex": "high", "opus": "high", "grok": "high"},
              f"audit records the high launches ({audit_efforts})")

        # f6: a voter whose program is missing is not recorded.
        fake_bin = tmp / "bin"
        fake_bin.mkdir()
        for name in ("claude", "codex"):
            prog = fake_bin / name
            prog.write_text("#!/bin/sh\nexit 0\n")
            prog.chmod(0o755)
        hidden = tmp / "hidden.jsonl"
        hidden_drive = subprocess.run(
            ["bash", "-c", f"""
set -eu
export COUNCIL_SOURCE_ONLY=1
export SEAT_USAGE_RUNS={hidden}
export PATH={fake_bin}:/usr/bin:/bin
unset CODEX
. {COUNCIL}
ID=hidden-grok
KIND=design
VOTERS=claude,codex,grok
CLAUDE_MODEL_ALIAS=fable
CLAUDE_EFFORT=high
CODEX_MODEL=gpt-6-astra
CODEX_EFFORT=medium
GROK_NATIVE_MODEL=grok-4.7
GROK_EFFORT=high
council_begin_run
exit 0
"""],
            text=True, capture_output=True,
        )
        hidden_names = [v["name"] for v in lines(hidden)[0]["ran"]["voters"]] if hidden.exists() else []
        check(hidden_drive.returncode == 0 and hidden_names == ["claude", "codex"],
              f"a missing grok is not recorded ({hidden_names})")

        # f3: the start prints the run id, and a later command appends the review.
        shown = tmp / "shown.jsonl"
        show_drive = drive(tmp, shown, f"""
export COUNCIL_SOURCE_ONLY=1
export SEAT_USAGE_RUNS={shown}
. {COUNCIL}
ID=shown-task
KIND=review
VOTERS=claude
CLAUDE_MODEL_ALIAS=fable
CLAUDE_EFFORT=high
council_begin_run
exit 0
""")
        shown_id = ""
        for line in show_drive.stdout.splitlines():
            if line.startswith("run_id: "):
                shown_id = line.split(" ", 1)[1].strip()
        check(show_drive.returncode == 0 and shown_id and shown_id == lines(shown)[0]["run_id"],
              f"begin prints the run id ({shown_id!r})")
        review_cmd = subprocess.run(
            ["bash", str(COUNCIL), "--record-review", "--run-id", shown_id or "missing",
             "--review-file", str(review), "--task-id", "shown-task", "--kind", "review"],
            text=True, capture_output=True,
            env={**os.environ, "SEAT_USAGE_RUNS": str(shown), "HOME": str(home)},
        )
        review_rows = lines(shown) if shown.exists() else []
        check(review_cmd.returncode == 0 and len(review_rows) == 3 and review_rows[2]["phase"] == "review"
              and review_rows[2]["run_id"] == shown_id,
              f"record-review appends the review phase (rc={review_cmd.returncode} phases={[r['phase'] for r in review_rows]} err={review_cmd.stderr.strip()})")
        bad_cmd = subprocess.run(
            ["bash", str(COUNCIL), "--record-review", "--run-id", shown_id or "missing",
             "--review-file", str(bad)],
            text=True, capture_output=True,
            env={**os.environ, "SEAT_USAGE_RUNS": str(tmp / "bad-review.jsonl"), "HOME": str(home)},
        )
        check(bad_cmd.returncode == 2, f"record-review returns the writer status ({bad_cmd.returncode})")
        help_text = subprocess.run(["bash", str(COUNCIL), "--help"], text=True, capture_output=True)
        check("--record-review" in help_text.stdout, "usage names --record-review")

        # f10: an inherited in-progress mark must not drop the run.
        inherited = tmp / "inherited.jsonl"
        inherited_drive = drive(tmp, inherited, f"""
export COUNCIL_SOURCE_ONLY=1
export SEAT_USAGE_RUNS={inherited}
export RUN_OUTCOME=1
export OUTCOME_AFTER_DONE=1
unset RUN_ID
. {COUNCIL}
ID=inherited-task
KIND=design
VOTERS=claude
CLAUDE_MODEL_ALIAS=fable
CLAUDE_EFFORT=high
council_begin_run
exit 0
""")
        inherited_rows, inherited_bad = parsed_lines(inherited)
        inherited_phases = [row["phase"] for row in inherited_rows]
        check(inherited_drive.returncode == 0 and inherited_bad == 0
              and inherited_phases == ["before", "after"]
              and inherited_rows[0]["run_id"] and inherited_rows[1]["run_id"] == inherited_rows[0]["run_id"],
              f"inherited RUN_OUTCOME still records the run ({inherited_phases})")

        # f12: routing prose names families, not a model version, and does not ban a pool.
        routing = (ROOT / "docs" / "routing.md").read_text()
        agents = (ROOT / "AGENTS.md").read_text()
        check("Grok 4.7 votes" not in routing, "routing.md does not name a Grok version in the vote rule")
        check("never Grok Build" not in routing and "never Grok Build" not in agents,
              "routing prose does not ban Grok Build")
        check("SuperGrok is not a chair" not in routing, "routing.md does not wall off SuperGrok")
        check("voter effort at high" not in agents and "keeps voter effort at high" not in agents,
              "AGENTS.md does not freeze voter effort")

    check(stamp(REAL) == before, "real runs.jsonl stamp is unchanged")
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
