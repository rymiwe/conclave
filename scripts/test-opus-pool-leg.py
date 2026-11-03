#!/usr/bin/env python3
"""The claude leg runs Opus on the pool the lineup picked: agy, native, or cursor.

seat-usage seats Opus on google (agy), claude (native), or cursor (cursor-agent)
and lineup.py turns that into `opus:<via>:<model>`. Stubs only: no agy,
cursor-agent, or claude binary on PATH is ever called.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
COUNCIL = SCRIPTS / "council.sh"
sys.path.insert(0, str(SCRIPTS))
import lineup  # noqa: E402

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


SCHEMA = "## Findings\nnone\n## Approach\nlean\n## Would not do\nnone\n## Uncertainties\nnone\n"

AGY_STUB = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
rec = Path(os.environ["AGY_RECORD"])
rec.mkdir(parents=True, exist_ok=True)
(rec / "argv").write_text("\n".join(sys.argv[1:]) + "\n", encoding="utf-8")
sys.stdin.read()
sys.stdout.write(Path(os.environ["AGY_STREAM"]).read_text(encoding="utf-8"))
raise SystemExit(int(os.environ.get("AGY_RUN_RC", "0")))
'''

CURSOR_STUB = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
rec = Path(os.environ["CURSOR_RECORD"])
rec.mkdir(parents=True, exist_ok=True)
(rec / "argv").write_text("\n".join(sys.argv[1:]) + "\n", encoding="utf-8")
for ev in (
    {"type": "system", "model": os.environ["CURSOR_DISPLAY"], "session_id": "s1"},
    {"type": "result", "is_error": False, "result": os.environ["CURSOR_RESULT"]},
):
    print(json.dumps(ev))
'''


def row(family, seat, vendor, **extra):
    r = {"role": "voter", "family": family, "seat": seat, "vendor": vendor, "effort": "high"}
    r.update(extra)
    return r


def raw(*voters):
    return json.dumps([{"role": "lineup", "mode": "default", "lineup": {"voters": list(voters)}}])


GROK = row("grok", "grok", "xai")
ASTRA = row("astra", "chatgpt", "openai")


def opus(seat, model):
    return row("opus", seat, "anthropic", model=model, pool="p")


def stubs(tmp: Path):
    bindir = tmp / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for name, body in (("agy", AGY_STUB), ("cursor-agent", CURSOR_STUB)):
        (bindir / name).write_text(body, encoding="utf-8")
        (bindir / name).chmod(0o755)
    return bindir


def bash(body, extra=None):
    env = os.environ.copy()
    for key in list(env):
        if "ARGON" in key or key.startswith(("CONCLAVE_", "COUNCIL_")):
            env.pop(key)
    env["CONCLAVE_JEV_OP"] = "/bin/false"
    env["PATH"] = f"{Path(shutil.which('jq')).parent}:/usr/bin:/bin"
    env.update(extra or {})
    head = f"set -u\nexport COUNCIL_SOURCE_ONLY=1\n. {COUNCIL}\nset +e\n"
    return subprocess.run(["bash", "-c", head + body], text=True, capture_output=True, env=env)


def agy_stream(path: Path, status, response):
    events = [{"event": "init", "init": {"agent": "conclave-voter"}},
              {"event": "result", "result": {"status": status, "response": response, "error": ""}}]
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_lineup_parse():
    cases = {
        "google": ("claude-opus-5-5-high", "opus:agy:claude-opus-5-5-high"),
        "claude": ("opus", "opus:claude:opus"),
        "cursor": ("claude-opus-5-5-high", "opus:cursor:claude-opus-5-5-high"),
    }
    for seat, (model, want) in cases.items():
        out = lineup.parse(raw(opus(seat, model), GROK)).splitlines()
        check(out[:3] == ["ok", "claude,grok", want], f"opus on {seat} parses to {want} ({out})")
    check(lineup.parse(raw(opus("google", "bad id;rm"), GROK)) == "bad\n",
          "an unsafe Opus model id is a bad lineup, not a launch")
    check(lineup.parse(raw(opus("google", None), GROK)) == "bad\n", "an Opus row with no model is bad")
    check(lineup.parse(raw(opus("chatgpt", "x"), GROK)) == "bad\n", "Opus on a non-Opus seat is bad")
    check(lineup._FAMILY_VENDOR.get("opus") == "anthropic", "Opus is vendor anthropic on every pool")
    fable = lineup.parse(raw(row("fable", "cursor", "anthropic"), ASTRA)).splitlines()
    check(fable[:3] == ["ok", "claude,codex", "cursor-fable"], f"cursor Fable still parses ({fable})")
    route = raw(opus("cursor", "claude-opus-5-5-high"), GROK)
    rep = lineup.pick_replacement(route, "codex", "xai", "", "grok").splitlines()
    check(rep == ["ok", "claude", "opus:cursor:claude-opus-5-5-high"],
          f"a replacement Opus carries its pool and model ({rep})")
    rep = lineup.pick_replacement(route, "codex", "anthropic", "", "grok").splitlines()
    check(rep[:1] == ["none"] and "unreadable" not in "".join(rep),
          f"an Anthropic builder never gets an Opus replacement ({rep})")


def test_shell_apply():
    proc = bash(f"""
show() {{
  CLAUDE_SKU=fable; CLAUDE_VIA=""; CLAUDE_OPUS_MODEL=""; CLAUDE_EFFORT=medium
  claude_apply_launch_line "$1"; rc=$?
  printf '%s rc=%s sku=%s via=%s model=%s alias=%s effort=%s cli=%s seat=%s brief=%s\\n' "$1" "$rc" \\
    "$CLAUDE_SKU" "$(claude_leg_via)" "$CLAUDE_OPUS_MODEL" "$CLAUDE_MODEL_ALIAS" "$CLAUDE_EFFORT" \\
    "$(claude_launch_cli)" "$(review_failure_seat claude)" \\
    "$(VOTERS=claude,grok review_claude_needs_brief && echo yes || echo no)"
}}
DIR=$(mktemp -d)
show opus:agy:claude-opus-5-5-high
show opus:claude:opus
show opus:cursor:claude-opus-5-5-high
show opus:bogus:x
CLAUDE_SKU=opus; CLAUDE_VIA=""
printf 'header-opus via=%s\\n' "$(claude_leg_via)"
LINES="$(lineup_parse_voters '{raw(opus("google", "claude-opus-5-5-high"), GROK)}')"
printf 'parse=%s\\n' "$(printf '%s' "$LINES" | tr '\\n' '|')"
""")
    out = proc.stdout
    want = [
        "opus:agy:claude-opus-5-5-high rc=0 sku=opus via=agy model=claude-opus-5-5-high "
        "alias=claude-opus-5-5-high effort=high cli=agy seat=google brief=yes",
        "opus:claude:opus rc=0 sku=opus via=claude model=opus alias=opus effort=high cli=claude seat=claude brief=no",
        "opus:cursor:claude-opus-5-5-high rc=0 sku=opus via=cursor model=claude-opus-5-5-high "
        "alias=claude-opus-5-5-high effort=high cli=cursor-agent seat=cursor brief=yes",
        "opus:bogus:x rc=1 sku=fable via=cursor",
        "header-opus via=claude",
        "parse=ok|claude,grok|opus:agy:claude-opus-5-5-high",
    ]
    for w in want:
        hit = any(line.startswith(w) for line in out.splitlines())
        check(hit, f"shell: {w}" + ("" if hit else f" ({out}{proc.stderr})"))


def test_design_agy(tmp: Path):
    d = tmp / "agy"
    packet = d / "packet"
    packet.mkdir(parents=True)
    (packet / "PROMPT.md").write_text("Design packet.\n", encoding="utf-8")
    bindir = stubs(d)
    stream = d / "stream.jsonl"
    env = {"CONCLAVE_AGY": str(bindir / "agy"), "AGY_RECORD": str(d / "rec"), "AGY_STREAM": str(stream)}
    body = f"""
DIR={d}; PACKET={packet}; KIND=design; ERRORS_FILE={d}/errors.md; : >"$ERRORS_FILE"
claude_apply_launch_line opus:agy:claude-opus-5-5-high
run_claude; echo rc=$?
"""
    agy_stream(stream, "SUCCESS", SCHEMA)
    proc = bash(body, env)
    argv = (d / "rec" / "argv").read_text(encoding="utf-8").splitlines()
    check("rc=0" in proc.stdout, f"design Opus on agy succeeds ({proc.stdout}{proc.stderr})")
    check(argv[argv.index("--model") + 1] == "claude-opus-5-5-high", f"agy ran the Opus id ({argv})")
    check((d / "voter-claude.ok").exists() and "## Findings" in (d / "voter-claude.md").read_text(),
          "the claude ballot is written and schema-checked")
    check((d / "voter-claude.model.txt").read_text() == "claude: ok via=agy model=claude-opus-5-5-high\n",
          "the proof names agy and the model")
    check(not list(d.glob("voter-argon*")), "the claude leg on agy writes no argon files")
    agy_stream(stream, "ERROR", "")
    proc = bash(body, env)
    check("rc=1" in proc.stdout and not (d / "voter-claude.md").exists(),
          f"a failed agy Opus is a failed claude leg, not a silent skip ({proc.stdout})")
    check("claude: Opus on agy failed" in (d / "errors.md").read_text(), "the failure is logged loud")


def test_design_cursor(tmp: Path):
    d = tmp / "cursor"
    packet = d / "packet"
    packet.mkdir(parents=True)
    (packet / "PROMPT.md").write_text("Design packet.\n", encoding="utf-8")
    bindir = stubs(d)
    env = {"CONCLAVE_CURSOR_AGENT": str(bindir / "cursor-agent"), "CURSOR_RECORD": str(d / "rec"),
           "CURSOR_RESULT": SCHEMA, "CURSOR_FABLE": "claude-fable-5-thinking-xhigh"}
    body = f"""
DIR={d}; PACKET={packet}; KIND=design; ERRORS_FILE={d}/errors.md; : >"$ERRORS_FILE"
claude_apply_launch_line opus:cursor:claude-opus-5-5-high
run_claude; echo rc=$?
"""
    proc = bash(body, dict(env, CURSOR_DISPLAY="Claude Opus 5.5 High"))
    argv = (d / "rec" / "argv").read_text(encoding="utf-8").splitlines()
    check("rc=0" in proc.stdout, f"design Opus on cursor succeeds ({proc.stdout}{proc.stderr})")
    check(argv[argv.index("--model") + 1] == "claude-opus-5-5-high", f"cursor ran Opus, not Fable ({argv})")
    check((d / "voter-claude.model.txt").read_text().startswith(
        "claude: ok via=cursor model=claude-opus-5-5-high"), "the proof names cursor and Opus")
    proc = bash(body, dict(env, CURSOR_DISPLAY="Claude Fable 5"))
    check("rc=1" in proc.stdout, f"a Cursor run that reports Fable fails the Opus leg ({proc.stdout})")


def test_review_agy(tmp: Path):
    d = tmp / "review"
    ballot = d / "ballot"
    ballot.mkdir(parents=True)
    repo = d / "wt"
    genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@e")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "b"], check=True, env=genv)
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (ballot / "voter-claude.brief.md").write_text("Review brief.\n", encoding="utf-8")
    bindir = stubs(d)
    stream = d / "stream.jsonl"
    agy_stream(stream, "SUCCESS", "FINDING: a review finding\n")
    tmpdir = d / "tmp"
    tmpdir.mkdir()
    env = {"CONCLAVE_AGY": str(bindir / "agy"), "AGY_RECORD": str(d / "rec"), "AGY_STREAM": str(stream),
           "TMPDIR": str(tmpdir)}
    proc = bash(f"""
DIR={ballot}; ID=t; REPO={repo}; KIND=review; DRY_RUN=0; ERRORS_FILE={ballot}/errors.md; : >"$ERRORS_FILE"
claude_apply_launch_line opus:agy:claude-opus-5-5-high
review_run_claude {repo}; echo rc=$?
review_report_present claude && echo present
""", env)
    check("rc=0" in proc.stdout and "present" in proc.stdout,
          f"review Opus on agy is a present claude report ({proc.stdout}{proc.stderr})")
    js = json.loads((ballot / "voter-claude.json").read_text()) if (ballot / "voter-claude.json").exists() else {}
    check(js.get("via") == "agy" and js.get("model") == "claude-opus-5-5-high"
          and "a review finding" in js.get("result", ""), f"voter-claude.json carries the agy result ({js})")
    check("Review brief." in (ballot / "voter-claude.prompt.md").read_text(), "agy read the claude brief")
    check(not list(ballot.glob("voter-argon*")), "no argon files on the claude leg")


def test_ask_agy(tmp: Path):
    """The ask route's Opus voter maps to the claude leg and runs on its pool."""
    d = tmp / "ask"
    packet = d / "packet"
    packet.mkdir(parents=True)
    (packet / "PROMPT.md").write_text("One question.\n", encoding="utf-8")
    bindir = stubs(d)
    stream = d / "stream.jsonl"
    route = json.dumps({"role": "ask", "mode": "default", "voters": [
        GROK, opus("google", "claude-opus-5-5-high")]})
    env = {"CONCLAVE_AGY": str(bindir / "agy"), "AGY_RECORD": str(d / "rec"), "AGY_STREAM": str(stream),
           "ROUTE": route}
    body = f"""
DIR={d}/run; mkdir -p "$DIR"; ASK_OUT={d}; PACKET={packet}; KIND=ask
ERRORS_FILE={d}/errors.md; : >"$ERRORS_FILE"; FLAG_CLAUDE=""; ASK_ROUTE_JSON="$ROUTE"
printf 'voters=%s\\n' "$(ask_voters_from_route)"
line="$(ask_route_opus_line)"; printf 'line=%s\\n' "$line"
claude_apply_launch_line "$line"; printf 'via=%s effort=%s\\n' "$(claude_leg_via)" "$CLAUDE_EFFORT"
ask_run_claude; echo rc=$?
"""
    agy_stream(stream, "SUCCESS", "Opus answer text.")
    proc = bash(body, env)
    out = proc.stdout
    check("voters=grok,claude" in out, f"ask route opus maps to the claude leg ({out}{proc.stderr})")
    check("line=opus:agy:claude-opus-5-5-high" in out, f"ask route opus gives an agy launch line ({out})")
    check("via=agy effort=high" in out, f"ask Opus runs on agy at high ({out})")
    check("rc=0" in out and (d / "answer-claude.ok").exists(), f"ask Opus on agy answers ({out}{proc.stderr})")
    argv = (d / "rec" / "argv").read_text(encoding="utf-8").splitlines() if (d / "rec" / "argv").exists() else []
    check("--model" in argv and argv[argv.index("--model") + 1] == "claude-opus-5-5-high",
          f"ask agy ran the Opus id ({argv})")
    check("Opus answer text." in (d / "answer-claude.md").read_text(encoding="utf-8")
          if (d / "answer-claude.md").exists() else False, "ask answer text is kept")
    agy_stream(stream, "ERROR", "")
    proc = bash(body, env)
    check("rc=1" in proc.stdout and not (d / "answer-claude.md").exists(),
          f"a failed ask Opus on agy is a failed leg ({proc.stdout}{proc.stderr})")
    check("claude: Opus on agy failed" in (d / "errors.md").read_text(encoding="utf-8"),
          "the ask failure is logged loud")


def main():
    import tempfile
    test_lineup_parse()
    test_shell_apply()
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        test_design_agy(tmp)
        test_design_cursor(tmp)
        test_review_agy(tmp)
        test_ask_agy(tmp)
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
