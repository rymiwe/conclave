#!/usr/bin/env python3
"""Argon voter: packet-first agy leg, fail-soft, blinded, judged with the others.

Stubs only. This file never calls the agy binary on PATH.
"""

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
COUNCIL = SCRIPTS / "council.sh"
CONCLAVE = SCRIPTS / "conclave.sh"

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


SCHEMA = (
    "Preamble from the model.\n"
    "## Findings\n"
    "Gemini Argon Google Antigravity agy gemini-4-argon-low found it.\n"
    "## Approach\n"
    "lean\n"
    "## Would not do\n"
    "none\n"
    "## Uncertainties\n"
    "none\n"
)

USAGE_OK = "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t100%\t2026-10-27T01:38:40Z\n"
USAGE_ZERO = "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t0%\t2026-10-27T01:38:40Z\n"

STUB = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
rec = Path(os.environ["AGY_RECORD"])
rec.mkdir(parents=True, exist_ok=True)
argv = sys.argv[1:]
text = "\n".join(argv)
with (rec / "calls").open("a", encoding="utf-8") as fh:
    fh.write("---\n" + text + "\n")
watched = (
    "TYPESAFE_API_KEY",
    "JEV_API_KEY",
    "COUNCIL_SAVED_TYPESAFE",
    "COUNCIL_SAVED_JEV",
)
with (rec / "env").open("a", encoding="utf-8") as fh:
    fh.write("---\nrecorded\n")
    for key in watched:
        if key in os.environ:
            fh.write(key + "=" + os.environ[key] + "\n")
data = sys.stdin.buffer.read()
if "/usage" in argv:
    (rec / "usage-argv").write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(os.environ.get("AGY_USAGE_OUT", ""))
    sys.stderr.write(os.environ.get("AGY_USAGE_ERR", ""))
    raise SystemExit(int(os.environ.get("AGY_USAGE_RC", "0")))
(rec / "cwd").write_text(os.getcwd() + "\n", encoding="utf-8")
(rec / "argv").write_text(text + "\n", encoding="utf-8")
(rec / "stdin").write_bytes(data)
stream = os.environ.get("AGY_STREAM", "")
if stream:
    sys.stdout.write(Path(stream).read_text(encoding="utf-8"))
sys.stderr.write(os.environ.get("AGY_RUN_ERR", ""))
raise SystemExit(int(os.environ.get("AGY_RUN_RC", "0")))
'''

HEAD = f"""
set -eu
export COUNCIL_SOURCE_ONLY=1
. {COUNCIL}
"""


def jq_bin():
    found = shutil.which("jq")
    if not found:
        raise SystemExit("FAIL jq is not on PATH")
    return found


def base_env(home: Path, bindir: Path, extra=None):
    env = os.environ.copy()
    for key in (
        "CONCLAVE_ARGON", "COUNCIL_ARGON", "CONCLAVE_AGY", "COUNCIL_AGY",
        "CONCLAVE_ARGON_MODEL", "COUNCIL_ARGON_MODEL",
        "CONCLAVE_ARGON_TIMEOUT", "COUNCIL_ARGON_TIMEOUT",
        "TYPESAFE_API_KEY", "JEV_API_KEY",
        "COUNCIL_SAVED_TYPESAFE", "COUNCIL_SAVED_JEV",
        "CONCLAVE_SEAT_USAGE", "COUNCIL_SEAT_USAGE",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    env["PATH"] = f"{bindir}:{Path(jq_bin()).parent}:/usr/bin:/bin"
    env["CONCLAVE_JEV_OP"] = "/bin/false"
    env["CONCLAVE_ARGON"] = "1"
    # A stub or a missing path. Never command -v, and never ~/.local/bin/agy.
    env["CONCLAVE_AGY"] = str(home / "no-such-agy")
    if extra:
        env.update(extra)
    return env


def bash(body, env):
    return subprocess.run(["bash", "-c", body], text=True, capture_output=True, env=env)


def write_stub(path: Path):
    path.write_text(STUB, encoding="utf-8")
    path.chmod(0o755)


def stream_file(path: Path, status, response, error="", usage=None):
    if usage is None:
        usage = {"input_tokens": 3}
    events = [
        {"event": "init"},
        {"event": "step_update", "text_delta": "x"},
        {"event": "result", "result": {
            "status": status,
            "response": response,
            "error": error,
            "usage": usage,
        }},
    ]
    path.write_text("".join(json.dumps(ev) + "\n" for ev in events), encoding="utf-8")


def test_models(env):
    proc = bash(HEAD + """
ARGON_EFFORT=low
printf 'low=%s\\n' "$(argon_model_id)"
ARGON_EFFORT=medium
printf 'med=%s\\n' "$(argon_model_id)"
ARGON_EFFORT=high
printf 'high=%s\\n' "$(argon_model_id)"
ARGON_EFFORT=xhigh
printf 'xhigh=%s\\n' "$(argon_model_id)"
ARGON_EFFORT=max
printf 'max=%s\\n' "$(argon_model_id)"
export CONCLAVE_ARGON_MODEL=gemini-4-argon-custom
printf 'over=%s\\n' "$(argon_model_id)"
unset CONCLAVE_ARGON_MODEL
export COUNCIL_ARGON_MODEL=gemini-4-argon-alias
printf 'alias=%s\\n' "$(argon_model_id)"
""", env)
    out = proc.stdout
    check("low=gemini-4-argon-low" in out and "med=gemini-4-argon-medium" in out
          and "high=gemini-4-argon-high" in out and "xhigh=gemini-4-argon-high" in out
          and "max=gemini-4-argon-high" in out, f"effort maps to an Argon model id ({out} {proc.stderr})")
    check("over=gemini-4-argon-custom" in out, "CONCLAVE_ARGON_MODEL overrides the id")
    check("alias=gemini-4-argon-alias" in out, "COUNCIL_ARGON_MODEL is the alias")


def test_seat(env):
    proc = bash(HEAD + """
export CONCLAVE_SEAT_USAGE=/no/such/seat-usage
seat() {
  KIND="$1"
  VOTERS=claude,codex
  VOTERS_EXPLICIT="$2"
  apply_kind_voters
  printf '%s voters=%s\\n' "$3" "$VOTERS"
}
export CONCLAVE_ARGON=1
seat design 0 on
export CONCLAVE_ARGON=0
seat design 0 off-new
unset CONCLAVE_ARGON
export COUNCIL_ARGON=0
seat design 0 off-old
unset COUNCIL_ARGON
export CONCLAVE_ARGON=1
export COUNCIL_ARGON=0
seat design 0 new-wins
export CONCLAVE_ARGON=0
VOTERS=claude,codex
VOTERS_EXPLICIT=1
KIND=design
apply_kind_voters
printf 'explicit=%s\\n' "$VOTERS"
export CONCLAVE_ARGON=0
VOTERS=claude,argon
VOTERS_EXPLICIT=1
KIND=design
apply_kind_voters
printf 'named=%s\\n' "$VOTERS"
KIND=review
VOTERS=claude,codex,argon
VOTERS_EXPLICIT=1
apply_kind_voters
printf 'review=%s\\n' "$VOTERS"
printf 'reason=%s\\n' "$(argon_unsupported_reason review)"
printf 'audit=%s\\n' "$(argon_unsupported_reason audit)"
""", env)
    out = proc.stdout
    check("on voters=claude,codex,argon" in out, f"default design seats argon ({out} {proc.stderr})")
    check("off-new voters=claude,codex" in out, "CONCLAVE_ARGON=0 turns the leg off")
    check("off-old voters=claude,codex" in out, "COUNCIL_ARGON=0 turns the leg off")
    check("new-wins voters=claude,codex,argon" in out, "CONCLAVE_ARGON wins over the alias")
    check("explicit=claude,codex" in out, "--voters without argon does not seat it")
    check("named=claude,argon" in out, "--voters argon seats it even when the default is off")
    check("review=claude,codex,argon" in out, "review keeps a named argon so the skip can be recorded")
    reason = "argon: kind review is not supported; Argon votes on design and mixed only"
    check(f"reason={reason}" in out, "review skip reason is the recorded sentence")
    check("audit=argon: kind audit is not supported; Argon votes on design and mixed only" in out,
          "audit skip reason names audit")
    audit = (SCRIPTS / "audit-kind.sh").read_text(encoding="utf-8")
    check("note_argon_kind_skip" in audit and "argon) continue" in audit, "audit skips argon")
    review = (SCRIPTS / "review-kind.sh").read_text(encoding="utf-8")
    check("review_run_argon" in review and "argon) continue" not in review, "review launches a seated argon")


def test_happy(tmp: Path, env_base):
    d = tmp / "happy"
    packet = d / "packet"
    bindir = d / "bin"
    rec = d / "rec"
    packet.mkdir(parents=True)
    bindir.mkdir()
    prompt = "Packet prompt for the leg.\n"
    (packet / "PROMPT.md").write_text(prompt, encoding="utf-8")
    stream = d / "stream.jsonl"
    stream_file(stream, "SUCCESS", SCHEMA, usage={"input_tokens": 9, "output_tokens": 4})
    write_stub(bindir / "agy")
    env = dict(env_base)
    env.update({
        "CONCLAVE_AGY": str(bindir / "agy"),
        "AGY_RECORD": str(rec),
        "AGY_STREAM": str(stream),
        "AGY_USAGE_OUT": USAGE_OK,
        "AGY_USAGE_RC": "0",
        "AGY_RUN_RC": "0",
    })
    proc = bash(HEAD + f"""
DIR={d}
PACKET={packet}
ERRORS_FILE={d}/errors.md
: >"$ERRORS_FILE"
KIND=design
ARGON_EFFORT=low
VOTERS=argon
if ! argon_preflight >/dev/null; then echo preflight-failed; exit 1; fi
run_argon
publish_voter_opinions
echo "n=$OPINION_N missing=[$MISSING_VOTERS]"
HDR_BRANCH=none HDR_PR=none HDR_VA=fable HDR_VB=none HDR_BUILDER=unknown
council_write_header_json
""", env)
    ok = (d / "voter-argon.ok").is_file()
    proof = (d / "voter-argon.model.txt").read_text() if (d / "voter-argon.model.txt").is_file() else ""
    body = (d / "voter-argon.md").read_text() if (d / "voter-argon.md").is_file() else ""
    check(ok and proof.strip() == "argon: ok model=gemini-4-argon-low" and body.startswith("## Findings"),
          f"happy path writes the ballot and the model proof ({proof!r} {proc.stderr[-400:]})")
    check("Preamble" not in body, "leading text before Findings is trimmed")
    check("n=1 missing=[]" in proc.stdout, f"opinion published ({proc.stdout} {proc.stderr[-200:]})")
    opinion = (d / "opinion-1.md").read_text() if (d / "opinion-1.md").is_file() else ""
    for word in ("Gemini", "Argon", "Google", "Antigravity", "agy", "gemini-4-argon-low"):
        check(word not in opinion, f"published opinion redacts {word}")
    check("found it." in opinion, "published opinion keeps the finding")
    header = json.loads((d / "header.json").read_text()) if (d / "header.json").is_file() else {}
    check(header.get("argon_model") == "gemini-4-argon-low", f"header.json has argon_model ({header})")
    argv = (rec / "argv").read_text() if (rec / "argv").is_file() else ""
    cwd = (rec / "cwd").read_text().strip() if (rec / "cwd").is_file() else ""
    stdin = (rec / "stdin").read_bytes() if (rec / "stdin").is_file() else b""
    expect = subprocess.run(
        ["jq", "-cn", "--rawfile", "c", str(packet / "PROMPT.md"),
         '{event:"user",message:{role:"user",content:$c}}'],
        check=True, capture_output=True).stdout
    check("--dangerously-skip-permissions" not in argv and "--add-dir" not in argv
          and "\n--mode\n" not in f"\n{argv}\n" and "--mode\n" not in argv,
          f"argv has no permission skip ({argv})")
    check("--disable-slash-commands" in argv and "\n--sandbox\n" in f"\n{argv}\n"
          and "gemini-4-argon-low" in argv and "--print-timeout\n900s" in argv
          and "--input-format\nstream-json" in argv and "--output-format\nstream-json" in argv
          and argv.strip().endswith("-p="),
          f"argv is the stream-json packet launch ({argv})")
    check(cwd == str(packet), f"cwd is the packet ({cwd})")
    check(stdin == expect, "stdin prompt equals PROMPT.md")
    calls = (rec / "calls").read_text() if (rec / "calls").is_file() else ""
    check("/usage" in calls and calls.count("--model") == 1, "preflight is usage and the run is one model call")


def test_skips(tmp: Path, env_base):
    def run_case(name, usage_out="", usage_err="", usage_rc="0", stream=None, run_rc="0", run_err="",
                 missing=False, schema=False):
        d = tmp / name
        packet = d / "packet"
        bindir = d / "bin"
        rec = d / "rec"
        packet.mkdir(parents=True)
        bindir.mkdir()
        (packet / "PROMPT.md").write_text("prompt\n", encoding="utf-8")
        for voter in ("claude", "codex"):
            (d / f"voter-{voter}.md").write_text(
                "## Findings\na\n## Approach\nb\n## Would not do\nc\n## Uncertainties\nd\n",
                encoding="utf-8")
            (d / f"voter-{voter}.ok").write_text("", encoding="utf-8")
        env = dict(env_base)
        if missing:
            env["CONCLAVE_AGY"] = str(d / "no-such-agy")
        else:
            write_stub(bindir / "agy")
            env["CONCLAVE_AGY"] = str(bindir / "agy")
        env.update({
            "AGY_RECORD": str(rec),
            "AGY_USAGE_OUT": usage_out,
            "AGY_USAGE_ERR": usage_err,
            "AGY_USAGE_RC": usage_rc,
            "AGY_RUN_RC": run_rc,
            "AGY_RUN_ERR": run_err,
        })
        if stream is not None:
            stream_path = d / "stream.jsonl"
            stream_path.write_text(stream, encoding="utf-8")
            env["AGY_STREAM"] = str(stream_path)
        launch = "run_argon" if stream is not None or run_rc != "0" else "true"
        if schema or (stream is not None) or run_rc != "0":
            launch = "if argon_preflight >/dev/null; then run_argon; fi"
        else:
            launch = "prepare_argon_leg"
        proc = bash(HEAD + f"""
DIR={d}
PACKET={packet}
ERRORS_FILE={d}/errors.md
: >"$ERRORS_FILE"
KIND=design
ARGON_EFFORT=low
VOTERS=claude,codex,argon
{launch}
miss="$(named_voters_missing)"
printf 'missing=[%s]\\n' "$miss"
publish_voter_opinions
printf 'n=%s missing2=[%s]\\n' "$OPINION_N" "$MISSING_VOTERS"
HDR_BRANCH=none HDR_PR=none HDR_VA=fable HDR_VB=astra HDR_BUILDER=unknown
council_write_header_json
""", env)
        skipped = (d / "voter-argon.skipped").read_text() if (d / "voter-argon.skipped").is_file() else ""
        header = {}
        if (d / "header.json").is_file():
            header = json.loads((d / "header.json").read_text())
        return proc, skipped, header

    proc, skipped, _header = run_case("missing", missing=True)
    check(skipped.startswith("argon: skipped\n") and "agy: not found:" in skipped
          and "missing=[]" in proc.stdout and "n=2" in proc.stdout and not (tmp / "missing" / "voter-argon.ok").exists(),
          f"missing agy skips and the run continues ({skipped!r} {proc.stdout} {proc.stderr[-200:]})")

    proc, skipped, header = run_case("signed-out", usage_err="not signed in\n", usage_rc="1")
    check("not signed in" in skipped and skipped.startswith("argon: skipped\n"),
          f"usage failure keeps the agy text ({skipped!r})")
    check(header.get("argon_skipped") == "not signed in" and "argon_model" not in header,
          f"header records argon_skipped ({header})")
    check("n=2" in proc.stdout and "missing=[]" in proc.stdout, "signed-out leg is not a missing voter")

    proc, skipped, _header = run_case("zero", usage_out=USAGE_ZERO, usage_rc="0")
    check(USAGE_ZERO.strip() in skipped, f"0% row is kept verbatim ({skipped!r})")

    err_stream = tmp / "err-stream.jsonl"
    stream_file(err_stream, "ERROR", "", error="quota exceeded")
    proc, skipped, _header = run_case("error", usage_out=USAGE_OK, stream=err_stream.read_text())
    check("quota exceeded" in skipped and "voter-argon.ok" not in str(list((tmp / "error").glob("voter-argon*"))),
          f"ERROR status keeps result.error ({skipped!r} {proc.stderr[-200:]})")

    bad = tmp / "bad-stream.jsonl"
    stream_file(bad, "SUCCESS", "hello without headings\n")
    proc, skipped, _header = run_case("schema", usage_out=USAGE_OK, stream=bad.read_text(), schema=True)
    check("schema headings" in skipped and not (tmp / "schema" / "voter-argon.ok").exists()
          and "n=2" in proc.stdout, f"schema miss skips ({skipped!r} {proc.stdout})")

    proc, skipped, _header = run_case("exit", usage_out=USAGE_OK, stream="", run_rc="7", run_err="boom from agy\n")
    # empty stream path still set only when stream is not None. Pass a file via run.
    # The case above passes stream="" which writes a stream file of empty string only if stream is not None.
    # stream="" is not None, so it writes empty and run_rc 7. agy exits 7 before reading stream... stub exits after writing stderr.
    check("boom from agy" in skipped, f"nonzero exit keeps stderr ({skipped!r} {proc.stderr[-300:]})")


def test_kind_skip_file(tmp: Path, env):
    d = tmp / "kind"
    d.mkdir()
    proc = bash(HEAD + f"""
DIR={d}
ERRORS_FILE={d}/errors.md
: >"$ERRORS_FILE"
KIND=review
VOTERS=claude,codex,argon
note_argon_kind_skip
note_argon_kind_skip
printf '%s\\n' "$(cat "$DIR/voter-argon.skipped")"
echo "---errors---"
cat "$ERRORS_FILE"
""", env)
    text = proc.stdout
    check(text.startswith("argon: skipped\nargon: kind review is not supported; Argon votes on design and mixed only"),
          f"review skip file is the reason ({text})")
    check(text.count("argon: kind review") == 2, "a second note does not log twice")


def test_blind(tmp: Path, env):
    d = tmp / "blind"
    d.mkdir()
    src = d / "report.md"
    src.write_text(
        "Gemini said Argon via Google agy and Antigravity, model gemini-4-argon-high.\n"
        "metadata and google-cloud/client.go stay.\n",
        encoding="utf-8")
    subprocess.run(["bash", str(SCRIPTS / "blind.sh"), str(d), str(src)], check=True)
    out = (d / "blind" / "leg-A.md").read_text()
    for word in ("Gemini", "Argon", "Google", "Antigravity", "agy", "gemini-4-argon-high"):
        check(word not in out, f"blind redacts {word} ({out.strip()})")
    check("metadata" in out and "client.go" in out, f"blind keeps code words ({out.strip()})")
    proc = bash(HEAD + f"anonymize {src}\n", env)
    anon = proc.stdout
    for word in ("Gemini", "Argon", "Google", "Antigravity", "agy", "gemini-4-argon-high"):
        check(word not in anon, f"anonymize redacts {word}")
    check("metadata" in anon, "anonymize keeps metadata")


def test_jev(tmp: Path, env):
    import importlib.util
    spec = importlib.util.spec_from_file_location("jev_judge", SCRIPTS / "jev-judge.py")
    judge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(judge)
    opinions = ["one approach", "two approach", "three approach from argon"]
    state = judge.build_state("brief", opinions)
    check("## Opinion 3" in state and "three approach from argon" in state, "three opinions stay in the judge state")
    header = judge.format_judgment_header(
        decision="implement", confidence="0.900", judge_model="jev",
        meta={"kind": "design", "argon_model": "gemini-4-argon-low",
              "voter_a_model": "fable", "voter_b_model": "astra"},
    )
    check("argon_model: gemini-4-argon-low\n" in header, "judgment header prints argon_model")
    skipped = judge.format_judgment_header(
        decision="no automatic decision", confidence="n/a", judge_model="jev",
        meta={"kind": "design", "argon_skipped": "not signed in"},
    )
    check("argon_skipped: not signed in\n" in skipped and "argon_model:" not in skipped,
          "judgment header prints argon_skipped")
    d = tmp / "unproven"
    d.mkdir()
    proc = bash(HEAD + f"""
DIR={d}
KIND=design
VOTERS=claude,codex,argon
HDR_BRANCH=none HDR_PR=none HDR_VA=fable HDR_VB=astra HDR_BUILDER=unknown
council_write_header_json
""", env)
    check(proc.returncode == 0, f"header writer exits 0 ({proc.stderr})")
    meta = json.loads((d / "header.json").read_text())
    check(meta.get("argon_model") == "unproven", f"named argon without proof is unproven ({meta})")


def test_record(tmp: Path, env_base):
    bindir = tmp / "rec-bin"
    bindir.mkdir()
    write_stub(bindir / "agy")
    env = dict(env_base)
    env["CONCLAVE_AGY"] = str(bindir / "agy")
    env["AGY_RECORD"] = str(tmp / "rec-record")
    proc = bash(HEAD + """
export CONCLAVE_AGY
council_voter_started argon && echo started || echo not-started
ARGON_EFFORT=low
KIND=design
council_launch_efforts
printf 'effort=%s\\n' "$RAN_ARGON_EFFORT"
""", env)
    check("started" in proc.stdout and "not-started" not in proc.stdout and "effort=low" in proc.stdout,
          f"record treats a resolvable agy as started ({proc.stdout} {proc.stderr})")
    empty = tmp / "empty-bin"
    empty.mkdir()
    record_home = tmp / "record-home"
    record_home.mkdir()
    env2 = dict(env_base)
    env2["HOME"] = str(record_home)
    env2["PATH"] = f"{empty}:/usr/bin:/bin"
    env2["CONCLAVE_AGY"] = str(empty / "agy")
    proc = bash(HEAD + "council_voter_started argon && echo started || echo not-started\n", env2)
    check("not-started" in proc.stdout, f"missing agy is not started ({proc.stdout} {proc.stderr})")
    path = tmp / "runs.jsonl"
    row = subprocess.run(
        ["python3", str(SCRIPTS / "record-run.py"),
         "--phase", "before", "--path", str(path), "--run-id", "argon-1",
         "--voters", "claude,argon",
         "--ran-claude-model", "fable", "--ran-claude-effort", "high",
         "--ran-argon-model", "gemini-4-argon-low", "--ran-argon-effort", "low"],
        text=True, capture_output=True)
    check(row.returncode == 0, f"record-run accepts argon ({row.stderr})")
    data = json.loads(path.read_text().splitlines()[-1])
    names = {item["name"]: item for item in data["ran"]["voters"]}
    check(names["argon"]["model"] == "gemini-4-argon-low" and names["argon"]["effort"] == "low",
          f"ran row stores the argon effort ({names})")


def test_clear(tmp: Path, env):
    d = tmp / "clear"
    d.mkdir()
    (d / "voter-argon.skipped").write_text("argon: skipped\nold\n", encoding="utf-8")
    (d / "voter-argon.md").write_text("x\n", encoding="utf-8")
    proc = bash(HEAD + f"""
DIR={d}
clear_ballot_artifacts
if [ -e "$DIR/voter-argon.skipped" ] || [ -e "$DIR/voter-argon.md" ]; then echo left; else echo cleared; fi
""", env)
    check("cleared" in proc.stdout, f"clear removes a skipped leg ({proc.stdout} {proc.stderr})")


def test_smoke_seam():
    text = (SCRIPTS / "argon-smoke.sh").read_text(encoding="utf-8")
    check("CONCLAVE_SOURCE_ONLY=1" in text and "argon_preflight" in text and "run_argon" in text,
          "smoke seam uses the ballot preflight and run")
    check('mktemp -d /tmp/argon-smoke' in text, "smoke packet lives under /tmp")
    check(
        "unset TYPESAFE_API_KEY JEV_API_KEY COUNCIL_SAVED_TYPESAFE COUNCIL_SAVED_JEV" in text,
        "smoke unsets judge keys before preflight",
    )


def test_judge_key_hidden(tmp: Path, env_base):
    d = tmp / "keys"
    packet = d / "packet"
    bindir = d / "bin"
    rec = d / "rec"
    packet.mkdir(parents=True)
    bindir.mkdir()
    (packet / "PROMPT.md").write_text("prompt\n", encoding="utf-8")
    stream = d / "stream.jsonl"
    stream_file(stream, "SUCCESS", SCHEMA, usage={"input_tokens": 2, "output_tokens": 1})
    write_stub(bindir / "agy")
    sentinels = {
        "TYPESAFE_API_KEY": "sentinel-typesafe-9f3a",
        "JEV_API_KEY": "sentinel-jev-9f3a",
        "COUNCIL_SAVED_TYPESAFE": "sentinel-saved-typesafe-9f3a",
        "COUNCIL_SAVED_JEV": "sentinel-saved-jev-9f3a",
    }
    env = dict(env_base)
    env.update(sentinels)
    env.update({
        "CONCLAVE_AGY": str(bindir / "agy"),
        "AGY_RECORD": str(rec),
        "AGY_STREAM": str(stream),
        "AGY_USAGE_OUT": USAGE_OK,
        "AGY_USAGE_RC": "0",
        "AGY_RUN_RC": "0",
    })
    proc = bash(HEAD + f"""
DIR={d}
PACKET={packet}
ERRORS_FILE={d}/errors.md
: >"$ERRORS_FILE"
KIND=design
ARGON_EFFORT=low
VOTERS=argon
if ! argon_preflight >/dev/null; then echo preflight-failed; exit 1; fi
run_argon
""", env)
    seen = (rec / "env").read_text(encoding="utf-8") if (rec / "env").is_file() else ""
    check(proc.returncode == 0 and seen.count("recorded") >= 2,
          f"preflight and run both reached the stub ({proc.returncode} {seen!r} {proc.stderr[-300:]})")
    for value in sentinels.values():
        check(value not in seen, f"agy did not see {value} ({seen!r})")

    rec2 = d / "rec-smoke"
    env["AGY_RECORD"] = str(rec2)
    before = set(Path("/tmp").glob("argon-smoke.*"))
    try:
        smoke = subprocess.run(
            ["bash", str(SCRIPTS / "argon-smoke.sh"), "What is two plus two?"],
            text=True, capture_output=True, env=env)
    finally:
        for path in set(Path("/tmp").glob("argon-smoke.*")) - before:
            shutil.rmtree(path, ignore_errors=True)
    seen2 = (rec2 / "env").read_text(encoding="utf-8") if (rec2 / "env").is_file() else ""
    check(smoke.returncode == 0 and seen2.count("recorded") >= 2,
          f"smoke reached the stub ({smoke.returncode} {smoke.stderr[-300:]} {smoke.stdout[-200:]})")
    for value in sentinels.values():
        check(value not in seen2, f"smoke agy did not see {value} ({seen2!r})")


def test_skipped_argon_not_required(tmp: Path, env):
    def reports(d: Path):
        d.mkdir()
        (d / "voter-claude.json").write_text(
            json.dumps({"result": "first leg report"}) + "\n", encoding="utf-8")
        (d / "voter-claude.model.txt").write_text("claude: ok model=fable\n", encoding="utf-8")
        (d / "voter-codex.md").write_text("second leg report\n", encoding="utf-8")
        (d / "voter-codex.model.txt").write_text("codex: ok model=astra\n", encoding="utf-8")

    def run_kind(kind, blind_body):
        d = tmp / f"count-{kind}"
        reports(d)
        return bash(HEAD + f"""
DIR={d}
ERRORS_FILE={d}/errors.md
: >"$ERRORS_FILE"
KIND={kind}
VOTERS=claude,codex,argon
VOTERS_EXPLICIT=1
REVIEW_FAIL=0
note_argon_kind_skip
review_count_reports
printf 'expected=%s present=%s missing=[%s]\\n' \
  "$REVIEW_REPORTS_EXPECTED" "$REVIEW_REPORTS_PRESENT" "$REVIEW_REPORTS_MISSING"
printf 'listed=[%s]\\n' "$(review_report_voters | paste -sd, -)"
{blind_body}
printf 'fail=%s\\n' "$REVIEW_FAIL"
if [ -d "$DIR/blind" ]; then
  printf 'legs=%s\\n' "$(find "$DIR/blind" -maxdepth 1 -name 'leg-*.md' | wc -l | tr -d ' ')"
  if grep -q 'voter-argon' "$DIR/blind/key.txt"; then echo key-has-argon; else echo key-clean; fi
else
  echo legs=0
fi
printf 'skip<<\\n'
cat "$DIR/voter-argon.skipped"
printf 'skip>>\\n'
if [ -f "$DIR/model-check.md" ] && grep -q 'report check: argon missing' "$DIR/model-check.md"; then
  echo argon-missing-in-check
else
  echo argon-not-in-check
fi
""", env)

    proc = run_kind("review", "if review_blind; then echo blind-ok; else echo blind-bad; fi")
    out = proc.stdout
    check("expected=2 present=2 missing=[]" in out and "listed=[claude,codex]" in out,
          f"review count omits skipped argon ({out} {proc.stderr[-300:]})")
    check("blind-ok" in out and "fail=0" in out and "legs=2" in out and "key-clean" in out,
          f"review blinds the other legs ({out})")
    check("argon: kind review is not supported" in out and "skip<<" in out,
          f"review keeps the skip reason ({out})")
    errors = (tmp / "count-review" / "errors.md").read_text(encoding="utf-8")
    check("not judgeable" not in errors and "no report to blind" not in errors
          and "argon: kind review is not supported" in errors and "argon-not-in-check" in out,
          f"review does not fail because of argon ({errors!r} {out})")

    proc = run_kind("audit", """
AUDIT_KEPT=none
AUDIT_TYPE=content
audit_check_reports
if audit_blind; then echo blind-ok; else echo blind-bad; fi
""")
    out = proc.stdout
    check("expected=2 present=2 missing=[]" in out and "blind-ok" in out and "fail=0" in out
          and "legs=2" in out and "key-clean" in out and "argon-not-in-check" in out,
          f"audit count and blind omit skipped argon ({out} {proc.stderr[-400:]})")
    check("argon: kind audit is not supported" in out, f"audit keeps the skip reason ({out})")
    errors = (tmp / "count-audit" / "errors.md").read_text(encoding="utf-8")
    check("not judgeable" not in errors and "no report to blind" not in errors
          and "report check: argon missing" not in errors,
          f"audit does not fail because of argon ({errors!r})")

    bare = tmp / "count-bare"
    reports(bare)
    proc = bash(HEAD + f"""
DIR={bare}
ERRORS_FILE={bare}/errors.md
: >"$ERRORS_FILE"
KIND=review
VOTERS=claude,codex,argon
REVIEW_FAIL=0
review_count_reports
printf 'bare-expected=%s bare-present=%s bare-missing=[%s]\\n' \
  "$REVIEW_REPORTS_EXPECTED" "$REVIEW_REPORTS_PRESENT" "$REVIEW_REPORTS_MISSING"
""", env)
    check("bare-expected=3 bare-present=2 bare-missing=[argon]" in proc.stdout,
          f"argon without a skip file is still required ({proc.stdout} {proc.stderr[-200:]})")


def write_exe(path: Path, body: str):
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def test_dry_run(tmp: Path):
    work = tmp / "dry"
    bindir = work / "bin"
    home = work / "home"
    bindir.mkdir(parents=True)
    (home / ".codex").mkdir(parents=True)
    write_exe(bindir / "seat-usage", """\
        #!/bin/sh
        cat "$SEAT_STUB_JSON"
        """)
    write_exe(bindir / "codex", """\
        #!/bin/sh
        if [ "${1:-}" = "--version" ]; then echo "codex-cli 0.200.0"; exit 0; fi
        exit 0
        """)
    write_exe(bindir / "claude", "#!/bin/sh\nexit 0\n")
    write_exe(bindir / "grok", """\
        #!/bin/sh
        if [ "${1:-}" = "models" ]; then echo "Default model: grok-test-1"; exit 0; fi
        exit 0
        """)
    write_exe(bindir / "muse", "#!/bin/sh\nexit 0\n")
    write_exe(bindir / "cursor-agent", "#!/bin/sh\nexit 0\n")
    write_stub(bindir / "agy")
    (home / ".codex" / "models_cache.json").write_text(
        '{"models": [{"slug": "gpt-test-astra", "visibility": "list", "priority": 1}]}\n',
        encoding="utf-8")
    catalog = work / "muse-catalog.json"
    catalog.write_text(
        '{"models":[{"modelId":"muse-wonder-full-1.0","releaseDate":"2026-10-12"}]}\n',
        encoding="utf-8")
    lineup = work / "lineup.json"
    lineup.write_text(json.dumps([{
        "role": "lineup", "mode": "default", "reason": "stub",
        "lineup": {"voters": [
            {"family": "fable", "seat": "claude", "effort": "high"},
            {"family": "astra", "seat": "chatgpt", "effort": "high"},
        ]},
    }]), encoding="utf-8")
    target = work / "target"
    subprocess.run(["git", "init", "-q", "-b", "main", str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)
    (target / "f").write_text("t\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-qm", "one"], check=True)
    ballot_id = "argon-dry"
    ballot = ROOT / "tmp" / "council" / ballot_id
    shutil.rmtree(ballot, ignore_errors=True)
    ballot.mkdir(parents=True)
    (ballot / "brief.md").write_text(
        "kind: design\ndifficulty: low\n\n# Goal\nPick.\n\n## Decision needed\nWhich.\n",
        encoding="utf-8")
    rec = work / "rec"
    env = base_env(home, bindir, {
        "CONCLAVE_AGY": str(bindir / "agy"),
        "AGY_RECORD": str(rec),
        "AGY_USAGE_OUT": USAGE_OK,
        "AGY_USAGE_RC": "0",
        "CONCLAVE_SKIP_UPDATE_CHECK": "1",
        "CONCLAVE_CODEX_BIN": str(bindir / "codex"),
        "CONCLAVE_SEAT_USAGE": str(bindir / "seat-usage"),
        "CONCLAVE_MUSE_CATALOG": str(catalog),
        "SEAT_STUB_JSON": str(lineup),
        "CONCLAVE_ARGON": "1",
    })
    try:
        proc = subprocess.run(
            ["bash", str(CONCLAVE), ballot_id, "--dry-run", "--repo", str(target)],
            text=True, capture_output=True, env=env, cwd=str(target))
        check(proc.returncode == 0, f"design dry-run exits 0 ({proc.stderr[-400:]})")
        check("voters: claude,codex,argon" in proc.stdout, f"dry-run seats argon ({proc.stdout})")
        check("argon=gemini-4-argon-medium" in proc.stdout, f"dry-run shows the model ({proc.stdout})")
        calls = (rec / "calls").read_text() if (rec / "calls").is_file() else ""
        check("/usage" in calls and "--model" not in calls and "-p=" not in calls,
              f"dry-run calls usage only ({calls!r})")
        (ballot / "brief.md").write_text("kind: review\ndifficulty: low\n\n# Goal\nCheck.\n", encoding="utf-8")
        shutil.rmtree(ballot / "preview", ignore_errors=True)
        rec2 = work / "rec-review"
        env["AGY_RECORD"] = str(rec2)
        proc = subprocess.run(
            ["bash", str(CONCLAVE), ballot_id, "--dry-run", "--kind", "review",
             "--repo", str(target), "--whole", "--voters", "claude,codex,argon"],
            text=True, capture_output=True, env=env, cwd=str(target))
        check(proc.returncode == 0, f"review dry-run exits 0 ({proc.stderr[-400:]})")
        check("voters: claude,codex,argon" in proc.stdout, f"review dry-run seats a named argon ({proc.stdout})")
        check("kind review is not supported" not in proc.stdout, f"review dry-run does not skip argon ({proc.stdout})")
        calls = (rec2 / "calls").read_text() if (rec2 / "calls").is_file() else ""
        check("--version" in calls and "-p=" not in calls and "--model" not in calls,
              f"review dry-run reads the agy version and does not launch ({calls!r})")
    finally:
        shutil.rmtree(ballot, ignore_errors=True)


def test_open_stdin(tmp: Path, env_base):
    """The caller's stdin stays open. The /usage preflight must not read it."""
    d = tmp / "open-stdin"
    packet = d / "packet"
    bindir = d / "bin"
    rec = d / "rec"
    packet.mkdir(parents=True)
    bindir.mkdir()
    (packet / "PROMPT.md").write_text("Packet prompt for the leg.\n", encoding="utf-8")
    stream = d / "stream.jsonl"
    stream_file(stream, "SUCCESS", SCHEMA)
    write_stub(bindir / "agy")
    env = dict(env_base)
    env.update({
        "CONCLAVE_AGY": str(bindir / "agy"),
        "AGY_RECORD": str(rec),
        "AGY_STREAM": str(stream),
        "AGY_USAGE_OUT": USAGE_OK,
        "AGY_USAGE_RC": "0",
        "AGY_RUN_RC": "0",
    })
    body = HEAD + f"""
DIR={d}
PACKET={packet}
ERRORS_FILE={d}/errors.md
: >"$ERRORS_FILE"
KIND=design
ARGON_EFFORT=low
VOTERS=argon
if ! argon_preflight; then echo preflight-failed; exit 1; fi
run_argon
[ -f "$DIR/voter-argon.ok" ] && echo leg-ok
"""
    # stdin is a pipe this test never writes to or closes until the end.
    proc = subprocess.Popen(["bash", "-c", body], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    try:
        proc.wait(timeout=30)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        timed_out = True
    out = proc.stdout.read()
    proc.stdin.close()
    check(not timed_out, "open stdin: preflight does not block on the caller's stdin")
    check(proc.returncode == 0 and "preflight-failed" not in out, f"open stdin: preflight passes ({out!r})")
    check("leg-ok" in out, "open stdin: the leg still writes its ballot")


def main():
    import tempfile
    home = Path(tempfile.mkdtemp(prefix="argon-home-"))
    bindir = home / "bin"
    bindir.mkdir()
    env = base_env(home, bindir)
    try:
        with tempfile.TemporaryDirectory(prefix="argon-voter-") as raw:
            tmp = Path(raw)
            test_models(env)
            test_seat(env)
            test_happy(tmp, env)
            test_skips(tmp, env)
            test_kind_skip_file(tmp, env)
            test_blind(tmp, env)
            test_jev(tmp, env)
            test_record(tmp, env)
            test_clear(tmp, env)
            test_smoke_seam()
            test_judge_key_hidden(tmp, env)
            test_skipped_argon_not_required(tmp, env)
            test_dry_run(tmp)
            test_open_stdin(tmp, env)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print(f"{passed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
