#!/usr/bin/env python3
"""Muse as a named voter: whitelist, launch argv, model proof, missing CLI.

The model id in the launch tests is a fixture. Resolution is tested
through muse-models.py and a catalog override on the resolver.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
COUNCIL = SCRIPTS / "council.sh"
RESOLVE = SCRIPTS / "resolve-models.sh"
MUSE_MODELS = SCRIPTS / "muse-models.py"

_spec = importlib.util.spec_from_file_location("muse_usage", SCRIPTS / "muse-usage.py")
muse_usage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(muse_usage)

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


def pin():
    return "muse-wonder-1.0"


SCHEMA = "## Findings\none\n## Approach\nlean\n## Would not do\nnone\n## Uncertainties\nnone\n"


def journal(model_id, text):
    def rec(kind, payload):
        return json.dumps({"payload_type": kind, "payload": payload})
    lines = []
    if model_id:
        lines.append(rec("run.model.configured", {"model_id": model_id}))
    lines.append(rec("run.output.delta", {"text": text}))
    lines.append(rec("run.terminal.completed", {"text": text}))
    return "\n".join(lines) + "\n"


def bash(body, env=None):
    proc_env = os.environ.copy()
    for k in ("CONCLAVE_MUSE_SHADOW", "COUNCIL_MUSE_SHADOW"):
        proc_env.pop(k, None)
    proc_env["MUSE_USAGE_PATH"] = str(Path(tempfile.mkdtemp()) / "muse-usage.json")
    if env:
        proc_env.update(env)
    return subprocess.run(["bash", "-c", body], text=True, capture_output=True, env=proc_env)


HEAD = f"""
set -eu
export COUNCIL_SOURCE_ONLY=1
. {COUNCIL}
"""


def write_stub(path, journal_text, exit_code=0):
    data = path.with_suffix(".jsonl")
    data.write_text(journal_text, encoding="utf-8")
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" >\"{path}.argv\"\ncat \"{data}\"\nexit {exit_code}\n",
                    encoding="utf-8")
    path.chmod(0o755)


def test_proof(model):
    recs = muse_usage.line_records(journal(model, SCHEMA))
    check(muse_usage.model_proof(recs, model) == f"muse: ok model={model}", "proof passes on the pin")
    recs = muse_usage.line_records(journal("other-model", SCHEMA))
    check(muse_usage.model_proof(recs, model).startswith("muse: FAIL ran=other-model"), "proof fails on another id")
    recs = muse_usage.line_records(journal("", SCHEMA))
    check("FAIL model not proven" in muse_usage.model_proof(recs, model), "proof fails with no configured model")


def test_argv(model):
    proc = bash(HEAD + f"""
MUSE_WONDER_MODEL={model}
MUSE_EFFORT=medium
DIR=/x
echo ---packet
muse_voter_argv /x/p.md "" no
echo ---audit
muse_voter_argv /x/b.md /wt yes
""")
    packet, audit = proc.stdout.split("---audit")
    packet_lines = packet.split()
    check(f"--model\n{model}" in packet and "--reasoning-effort\nmedium" in packet
          and "--disable-web-tools" in packet_lines
          and "--workspace" not in packet_lines and "--disable-shell" in packet_lines
          and "--disable-write" in packet_lines and "--contributor-mode\noff" in packet,
          f"packet argv carries the model, its effort, and no tools ({proc.stderr})")
    audit_lines = audit.split()
    check("--workspace\n/wt" in audit and "--disable-web-tools" not in audit_lines
          and "--disable-shell" in audit_lines and "--disable-write" in audit_lines,
          "audit argv has the workspace, web, and still no shell or write")


def test_design_leg(model, tmp):
    for case, mid, want_ok in (("pass", model, True), ("mismatch", "other-model", False)):
        d = tmp / case
        bindir = d / "bin"
        packet = d / "packet"
        bindir.mkdir(parents=True)
        packet.mkdir()
        (packet / "PROMPT.md").write_text("packet\n", encoding="utf-8")
        write_stub(bindir / "muse", journal(mid, SCHEMA))
        proc = bash(HEAD + f"""
export PATH={bindir}:$PATH
DIR={d}; PACKET={packet}; ERRORS_FILE={d}/errors.md; : >"$ERRORS_FILE"
MUSE_WONDER_MODEL={model}
MUSE_EFFORT=low
run_muse_voter || true
""")
        ok = (d / "voter-muse.ok").exists() and (d / "voter-muse.md").exists()
        proof = (d / "voter-muse.model.txt").read_text() if (d / "voter-muse.model.txt").exists() else ""
        check(ok == want_ok, f"design leg {case}: ballot ok={ok} proof={proof.strip()} {proc.stderr[-300:]}")
        if want_ok:
            argv = (bindir / "muse.argv").read_text()
            check(f"--model\n{model}\n" in argv and "--reasoning-effort\nlow\n" in argv,
                  "design launch carries the model and its effort")


def test_review_leg_missing_and_ok(model, tmp):
    d = tmp / "review"
    wt = d / "leg" / "wt"
    wt.mkdir(parents=True)
    (d / "voter-muse.brief.md").write_text("brief\n", encoding="utf-8")
    empty_bin = d / "nobin"
    empty_bin.mkdir()
    proc = bash(HEAD + f"""
export PATH={empty_bin}:/usr/bin:/bin
DIR={d}; ERRORS_FILE={d}/errors.md; : >"$ERRORS_FILE"
MUSE_WONDER_MODEL={model}
if review_run_muse {wt} {d}/leg/tmp no; then echo ran; else echo failed; fi
""")
    fail = (d / "voter-muse.fail").read_text() if (d / "voter-muse.fail").exists() else ""
    check("failed" in proc.stdout and "exit 127" in fail, f"missing muse FAILs the leg cleanly ({fail.strip()} {proc.stderr[-200:]})")

    (d / "voter-muse.fail").unlink()
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    subprocess.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    (wt / "new.txt").write_text("DIFF_SENTINEL\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(wt), "add", "new.txt"], check=True)
    bindir = d / "bin"
    bindir.mkdir()
    write_stub(bindir / "muse", journal(model, "report body\n"))
    proc = bash(HEAD + f"""
export PATH={bindir}:$PATH
DIR={d}; ERRORS_FILE={d}/errors.md; : >"$ERRORS_FILE"
MUSE_WONDER_MODEL={model}
MUSE_EFFORT=low
review_run_muse {wt} {d}/leg/tmp no
review_report_present muse && echo present
review_voter_header_value muse
""")
    check("present" in proc.stdout and model in proc.stdout, f"review leg report and header ({proc.stdout} {proc.stderr[-200:]})")
    argv = (bindir / "muse.argv").read_text()
    check(f"--workspace\n{wt}\n" in argv and "--disable-web-tools" in argv
          and "--reasoning-effort\nhigh\n" in argv,
          "review launch is read-only, no web, and high effort")
    prompt = (d / "voter-muse.prompt.md").read_text() if (d / "voter-muse.prompt.md").exists() else ""
    check(prompt.startswith("brief") and "+DIFF_SENTINEL" in prompt and f"--prompt-file\n{d}/voter-muse.prompt.md" in argv,
          "review prompt is the brief plus the staged diff")


def test_whitelist(model, tmp):
    d = tmp / "publish"
    d.mkdir()
    for v in ("claude", "muse"):
        (d / f"voter-{v}.md").write_text(SCHEMA, encoding="utf-8")
        (d / f"voter-{v}.ok").write_text("", encoding="utf-8")
    proc = bash(HEAD + f"""
DIR={d}; ERRORS_FILE={d}/errors.md; : >"$ERRORS_FILE"
VOTERS=claude,muse
publish_voter_opinions
echo "n=$OPINION_N missing=$MISSING_VOTERS"
council_voter_started muse && echo started || echo not-started
export CONCLAVE_MUSE_SHADOW=1
MUSE_WONDER_MODEL={model}
PIDS=""
launch_muse_shadow
echo "shadow=$MUSE_SHADOW_STATUS pids=[$PIDS]"
""")
    check("n=2 missing=" in proc.stdout, f"muse opinion is published ({proc.stdout} {proc.stderr[-200:]})")
    check("shadow=skipped pids=[]" in proc.stdout, "shadow does not run beside a named muse voter")
    for f in ("review-kind.sh", "audit-kind.sh"):
        text = (SCRIPTS / f).read_text(encoding="utf-8")
        check("claude|opus|codex|grok|muse)" in text, f"{f} whitelists muse")


def test_brief_and_blind(model, tmp):
    d = tmp / "brief"
    d.mkdir()
    proc = bash(HEAD + f"""
review_write_claude_brief() {{ printf 'Target: x\\n- Execute anything with computable behaviour (x)\\n- keep\\n' >"$1"; }}
review_write_muse_brief {d}/b.md
""")
    text = (d / "b.md").read_text() if (d / "b.md").exists() else ""
    check("Execute anything" not in text and "keep" in text, f"muse brief drops the execute bullet ({proc.stderr})")
    rep = d / "r.md"
    rep.write_text(f"Muse said, Meta model {model}; meta.json and muse/x.py stay\n", encoding="utf-8")
    subprocess.run(["bash", str(SCRIPTS / "blind.sh"), str(d), str(rep)], check=True)
    out = (d / "blind" / "leg-A.md").read_text()
    check("Muse" not in out and "Meta" not in out and model not in out
          and "meta.json" in out and "muse/x.py" in out, f"blind redacts Muse names only ({out.strip()})")


def test_gaps(model, tmp):
    d = tmp / "gaps"
    d.mkdir()
    op = d / "op.md"
    op.write_text(f"Muse Wonder said; Meta model {model}; metadata and muse/x.py stay\n", encoding="utf-8")
    proc = bash(HEAD + f"anonymize {op}\n")
    out = proc.stdout
    check("Muse" not in out and "Meta " not in out and model not in out
          and "metadata" in out and "muse/x.py" in out, f"design anonymize redacts Muse ({out.strip()})")
    sys.path.insert(0, str(SCRIPTS))
    import audit_gate
    check({"Muse", "Meta"} <= set(audit_gate.keep_words_in_text("Muse and Meta are named.")),
          "audit keep-list keeps Muse and Meta")
    check(audit_gate.keep_words_in_text(f"ran {model}") == [], "a muse model id keeps nothing")
    spec = importlib.util.spec_from_file_location("jev_judge", SCRIPTS / "jev-judge.py")
    jj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jj)
    check(jj._LEG_FILE_LABELS.get("voter-muse.md") == "Meta Muse", "muse leg column is labelled")
    (d / "voter-muse.model.txt").write_text(f"muse: ok model={model}\n", encoding="utf-8")
    (d / "voter-muse.seconds").write_text("231\n", encoding="utf-8")
    proc = bash(HEAD + f"""
DIR={d}; KIND=review; VOTERS=claude,opus,muse
HDR_BRANCH=b HDR_PR=1 HDR_VA=x HDR_VB=unproven HDR_BUILDER=y
council_write_header_json
""")
    meta = json.loads((d / "header.json").read_text()) if (d / "header.json").exists() else {}
    check(meta.get("muse_model") == model and meta.get("muse_seconds") == "231"
          and meta.get("voter_b_model") == "none", f"header has muse and no empty Astra slot ({meta} {proc.stderr[-200:]})")
    text = jj.format_judgment_header(decision="clean", confidence="n/a", judge_model="jev", meta=meta)
    check(f"muse_model: {model}\nmuse_seconds: 231\n" in text, "judgment header prints the muse lines")
    rec = subprocess.run([sys.executable, str(SCRIPTS / "record-run.py"), "--help"], text=True, capture_output=True)
    check("--ran-muse-model" in rec.stdout and "--ran-muse-effort" in rec.stdout,
          "record-run takes the muse model and effort")


def test_newest_wonder():
    spec = importlib.util.spec_from_file_location("muse_models", MUSE_MODELS)
    mm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mm)
    catalog = [
        {"modelId": "muse-spark-1.3", "releaseDate": "2026-11-01"},
        {"modelId": "muse-wonder-1.0", "releaseDate": "2026-10-12"},
        {"modelId": "muse-wonder-full-1.0", "releaseDate": "2026-10-12"},
        {"modelId": "muse-wonder-2.0-contributor", "releaseDate": "2026-12-01"},
    ]
    check(mm.newest_wonder(catalog) == "muse-wonder-1.0", "equal dates keep catalog order and skip spark")
    newer = [{"modelId": "muse-wonder-1.0", "releaseDate": "2026-10-12"},
             {"modelId": "muse-wonder-2.0", "releaseDate": "2026-12-01"}]
    check(mm.newest_wonder(newer) == "muse-wonder-2.0", "a later releaseDate wins")
    check(mm.newest_wonder([]) == "", "an empty listing resolves nothing")
    proc = subprocess.run(
        [sys.executable, str(MUSE_MODELS), "--catalog", os.devnull],
        text=True, capture_output=True,
    )
    check(proc.returncode == 0 and proc.stdout == "", f"a bad catalog prints nothing ({proc.stdout!r})")


def test_resolver_uses_catalog(tmp):
    home = tmp / "resolve-home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "models_cache.json").write_text(
        '{"models":[{"slug":"gpt-test-astra","visibility":"list","priority":1}]}\n',
        encoding="utf-8",
    )
    catalog = tmp / "catalog.json"
    catalog.write_text(json.dumps({"models": [
        {"modelId": "muse-wonder-full-1.0", "releaseDate": "2026-10-12"},
        {"modelId": "muse-wonder-1.0", "releaseDate": "2026-10-12"},
        {"modelId": "muse-spark-1.3", "releaseDate": "2026-11-01"},
    ]}), encoding="utf-8")
    jq = shutil.which("jq")
    check(jq is not None, "jq is on PATH")
    if not jq:
        return
    jqdir = tmp / "jq-bin"
    jqdir.mkdir()
    os.symlink(jq, jqdir / "jq")
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{jqdir}:/usr/bin:/bin"
    env["CONCLAVE_CODEX_BIN"] = "/bin/true"
    env["CONCLAVE_MUSE_CATALOG"] = str(catalog)
    env.pop("COUNCIL_CODEX_BIN", None)
    env.pop("COUNCIL_MUSE_CATALOG", None)
    proc = subprocess.run(
        ["bash", str(RESOLVE)], cwd=ROOT, env=env, text=True, capture_output=True,
    )
    emitted = ""
    for line in proc.stdout.splitlines():
        if line.startswith("MUSE_WONDER_MODEL="):
            emitted = line.split("=", 1)[1]
    check(proc.returncode == 0 and emitted == "muse-wonder-full-1.0",
          f"resolver emits the catalog's newest Wonder id ({proc.returncode} {emitted!r} {proc.stderr[-200:]})")


def main():
    model = pin()
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        test_newest_wonder()
        test_resolver_uses_catalog(tmp)
        test_proof(model)
        test_argv(model)
        test_design_leg(model, tmp)
        test_review_leg_missing_and_ok(model, tmp)
        test_whitelist(model, tmp)
        test_brief_and_blind(model, tmp)
        test_gaps(model, tmp)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
