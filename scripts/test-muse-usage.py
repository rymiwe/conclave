#!/usr/bin/env python3
"""Muse shadow usage capture: fixture, atomic write, and a quiet failure."""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = SCRIPTS / "fixtures" / "muse" / "subscription-usage.sse"
COUNCIL = SCRIPTS / "council.sh"
RESOLVE = SCRIPTS / "resolve-models.sh"

_spec = importlib.util.spec_from_file_location("muse_usage", SCRIPTS / "muse-usage.py")
muse_usage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(muse_usage)

ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SENTINEL = "usage-capture-sentinel"
VERDICT = (
    "## Findings\n"
    "one\n"
    "## Approach\n"
    "lean\n"
    "## Would not do\n"
    "none\n"
    "## Uncertainties\n"
    "none\n"
    "## Decision\n"
    "implement\n"
)

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
    """A Wonder fixture. The resolver no longer stores a literal id."""
    text = RESOLVE.read_text(encoding="utf-8")
    check("muse-models.py" in text and "muse-wonder-" not in text, "resolver asks for the newest Wonder full id")
    return "muse-wonder-full-1.0"


def snapshot(window_used=40, weekly_used=12.5, duration=300, window_reset=1759900000, weekly_reset=1760000000):
    return {
        "window": {
            "used_percent": window_used,
            "window_duration_mins": duration,
            "resets_at": window_reset,
        },
        "weekly": {
            "used_percent": weekly_used,
            "resets_at": weekly_reset,
        },
    }


def test_fixture_shape():
    text = FIXTURE.read_text(encoding="utf-8")
    check("event: response.subscription_usage" in text, "fixture names the SSE event")
    check(SENTINEL in text, "fixture carries a sentinel that must stay out of the cache")
    got = muse_usage.extract_subscription_usage(text)
    check(got == snapshot(), f"fixture snapshot ({got})")
    check("tier" not in json.dumps(got), "snapshot omits tier")
    check(SENTINEL not in json.dumps(got), "snapshot omits the sentinel")


def test_last_event_and_rejects():
    first = FIXTURE.read_text(encoding="utf-8")
    second = """
event: response.subscription_usage
data: {"subscription":{"window":{"used_percent":80,"window_duration_mins":300,"resets_at":1759910000},"weekly":{"used_percent":3,"resets_at":"2026-10-19T00:00:00Z"}}}
"""
    got = muse_usage.extract_subscription_usage(first + second)
    expect = snapshot(window_used=80, weekly_used=3, window_reset=1759910000, weekly_reset="2026-10-19T00:00:00Z")
    check(got == expect, f"later SSE event wins and a string reset is kept ({got})")

    crlf = first.replace("\n", "\r\n")
    check(muse_usage.extract_subscription_usage(crlf) == snapshot(), "CRLF SSE still parses")

    incomplete = '{"subscription":{"window":{"used_percent":1,"resets_at":1},"weekly":{"used_percent":1,"resets_at":1}}}'
    check(muse_usage.extract_subscription_usage(incomplete) is None, "a window missing duration is ignored")

    flagged = '{"window":{"used_percent":true,"window_duration_mins":300,"resets_at":1},"weekly":{"used_percent":1,"resets_at":1}}'
    check(muse_usage.extract_subscription_usage(flagged) is None, "a boolean percent is ignored")

    both = incomplete + "\n" + second
    check(muse_usage.extract_subscription_usage(both) == expect, "a later complete event still wins")


def test_jsonl_and_embedded_sse():
    usage = {
        "subscription": {
            "tier": "everyday",
            "label": SENTINEL,
            "window": {"used_percent": 40, "window_duration_mins": 300, "resets_at": 1759900000},
            "weekly": {"used_percent": 12.5, "resets_at": 1760000000},
        }
    }
    lines = [
        {"payload_type": "run.lifecycle.started", "payload": {"note": SENTINEL}},
        {"payload_type": "tool.result", "payload": {"text": "tool-output-should-stay-out"}},
        {"payload_type": "run.output.delta", "payload": {"text": "## Findings\none\n"}},
        {"payload_type": "run.output.delta", "payload": {"text": "## Approach\nlean\n## Decision\nimplement\n"}},
        {"payload_type": "response.subscription_usage", "payload": usage},
    ]
    raw = "\n".join(json.dumps(line) for line in lines) + "\n"
    got = muse_usage.extract_subscription_usage(raw)
    check(got == snapshot(), f"JSONL payload yields the snapshot ({got})")
    verdict = muse_usage.verdict_replacement(raw)
    check(
        verdict == "## Findings\none\n## Approach\nlean\n## Decision\nimplement\n",
        f"deltas join into the verdict ({verdict!r})",
    )
    check(verdict is not None and "tool-output-should-stay-out" not in verdict, "tool text stays out of the verdict")
    check(SENTINEL not in (verdict or ""), "the sentinel stays out of the verdict")

    terminal_only = "\n".join([
        json.dumps({"payload_type": "run.output.delta", "payload": {"text": "  "}}),
        json.dumps({"payload_type": "run.terminal.completed", "payload": {"text": VERDICT}}),
    ]) + "\n"
    check(muse_usage.verdict_replacement(terminal_only) == VERDICT, "empty deltas fall back to terminal text")

    embedded = json.dumps({
        "payload_type": "provider.sse",
        "payload": {"chunk": FIXTURE.read_text(encoding="utf-8")},
    })
    check(muse_usage.extract_subscription_usage(embedded) == snapshot(), "SSE text inside a JSON string is read")

    plain = VERDICT + "\n" + json.dumps({"payload_type": "run.output.delta", "payload": {"text": "nope"}}) + "\n"
    check(muse_usage.verdict_replacement(plain) is None, "a ## heading keeps plain stdout")


def test_atomic_write(tmp: Path):
    dest = tmp / "cache" / "quartermaster" / "muse-usage.json"
    dest.parent.mkdir(parents=True)
    dest.write_text('{"captured_at":"old","stale":true}\n', encoding="utf-8")
    muse_usage.atomic_write_usage(dest, snapshot(), captured_at="2026-10-12T23:09:00Z")
    body = dest.read_text(encoding="utf-8")
    doc = json.loads(body)
    check(doc == {
        "captured_at": "2026-10-12T23:09:00Z",
        "window": snapshot()["window"],
        "weekly": snapshot()["weekly"],
    }, f"atomic replace writes the snapshot ({doc})")
    check(body.endswith("\n"), "usage file ends with a newline")
    leftovers = [path.name for path in dest.parent.iterdir() if path != dest]
    check(leftovers == [], f"temp file is gone after rename ({leftovers})")

    created = tmp / "new-parent" / "muse-usage.json"
    muse_usage.atomic_write_usage(created, snapshot(), captured_at="2026-10-12T23:10:00Z")
    check(created.is_file(), "write creates the cache directory")

    blocked = tmp / "blocked"
    blocked.mkdir()
    raised = False
    try:
        muse_usage.atomic_write_usage(blocked, snapshot())
    except OSError:
        raised = True
    leftovers = [path.name for path in tmp.iterdir() if path.name.startswith(".blocked.")]
    check(raised and leftovers == [], f"a failed rename removes the temp file ({raised} {leftovers})")

    parent = tmp / "not-a-directory"
    parent.write_text("x", encoding="utf-8")
    try:
        muse_usage.atomic_write_usage(parent / "muse-usage.json", snapshot())
        check(False, "a file parent raises")
    except OSError:
        check(parent.read_text(encoding="utf-8") == "x", "a failed write leaves the blocker unchanged")


def _run_cli(raw: Path, usage: Path, err: Path | None = None, expect_model=""):
    argv = ["--raw", str(raw), "--usage-file", str(usage), "--expect-model", expect_model]
    if err is not None:
        argv.extend(["--err", str(err)])
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "muse-usage.py"), *argv],
        text=True,
        capture_output=True,
    )
    return proc


def test_cli_fixture_and_silence(tmp: Path):
    raw = tmp / "raw.txt"
    raw.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    usage = tmp / "out" / "muse-usage.json"
    proc = _run_cli(raw, usage)
    check(proc.returncode == 0 and proc.stdout == "" and proc.stderr == "",
          f"cli is silent ({proc.returncode} {proc.stdout!r} {proc.stderr!r})")
    doc = json.loads(usage.read_text(encoding="utf-8"))
    check(ISO_TIME.match(doc["captured_at"]) is not None, f"captured_at is UTC ISO ({doc['captured_at']})")
    check(doc["window"] == snapshot()["window"] and doc["weekly"] == snapshot()["weekly"],
          f"cli stores the fixture fields ({doc})")
    stored = usage.read_text(encoding="utf-8")
    check(SENTINEL not in stored and "label" not in stored and "tier" not in stored,
          "cache file has no sentinel, label, or tier")
    kept = raw.read_text(encoding="utf-8")
    check("response.subscription_usage" not in kept and SENTINEL not in kept,
          "SSE usage lines are removed from plain stdout")

    untouched = tmp / "kept.json"
    untouched.write_text('{"captured_at":"keep"}\n', encoding="utf-8")
    plain = tmp / "plain.md"
    plain.write_text(VERDICT, encoding="utf-8")
    before = plain.read_text(encoding="utf-8")
    proc = _run_cli(plain, untouched)
    check(proc.returncode == 0 and plain.read_text(encoding="utf-8") == before, "plain verdict stdout stays")
    check(untouched.read_text(encoding="utf-8") == '{"captured_at":"keep"}\n',
          "a missing event leaves the cache file unchanged")


def test_cli_jsonl_rewrite_and_failure(tmp: Path, model):
    usage_obj = {
        "subscription": {
            "tier": "everyday",
            "label": SENTINEL,
            "window": {"used_percent": 9, "window_duration_mins": 300, "resets_at": 1759900000},
            "weekly": {"used_percent": 1, "resets_at": 1760000000},
        }
    }
    raw = tmp / "journal.jsonl"
    raw.write_text("\n".join([
        json.dumps({"payload_type": "run.lifecycle.started", "payload": {"note": SENTINEL}}),
        json.dumps({"payload_type": "run.output.delta", "payload": {"text": VERDICT}}),
        json.dumps({"payload_type": "response.subscription_usage", "payload": usage_obj}),
    ]) + "\n", encoding="utf-8")
    err = tmp / "err.txt"
    err.write_text("", encoding="utf-8")
    usage = tmp / "usage.json"
    blocked = tmp / "blocked-dir"
    blocked.mkdir()
    proc = _run_cli(raw, blocked, err, expect_model=model)
    rewritten = raw.read_text(encoding="utf-8")
    check(proc.returncode == 0 and proc.stdout == "" and proc.stderr == "", "jsonl capture stays silent on a bad path")
    check(rewritten == VERDICT, f"jsonl rewrites to the verdict when the cache write fails ({rewritten!r})")
    check(SENTINEL not in rewritten, "rewritten verdict drops the sentinel")
    check(not (blocked / "muse-usage.json").exists() and SENTINEL not in "".join(p.read_text(encoding="utf-8", errors="replace") for p in blocked.rglob("*") if p.is_file()),
          "a directory usage path stores nothing")

    raw.write_text("\n".join([
        json.dumps({"payload_type": "run.model.configured", "payload": {"model_id": model}}),
        json.dumps({"payload_type": "run.output.delta", "payload": {"text": VERDICT}}),
        json.dumps({"payload_type": "response.subscription_usage", "payload": usage_obj}),
    ]) + "\n", encoding="utf-8")
    err.write_text("muse: ready\n", encoding="utf-8")
    usage = tmp / "matched.json"
    proc = _run_cli(raw, usage, err, expect_model=model)
    doc = json.loads(usage.read_text(encoding="utf-8"))
    check(proc.returncode == 0 and doc["window"]["used_percent"] == 9, "a matching journal model still captures usage")
    check("journal model_id does not match" not in err.read_text(encoding="utf-8"), "the pin does not append a mismatch")

    raw.write_text("\n".join([
        json.dumps({"payload_type": "run.model.configured", "payload": {"model_id": "other-model-9"}}),
        json.dumps({"payload_type": "run.output.delta", "payload": {"text": VERDICT}}),
    ]) + "\n", encoding="utf-8")
    err.write_text("", encoding="utf-8")
    usage = tmp / "mismatch.json"
    proc = _run_cli(raw, usage, err, expect_model=model)
    note = err.read_text(encoding="utf-8")
    check(proc.returncode == 0 and "journal model_id does not match the resolved model" in note, f"a foreign journal model is recorded ({note!r})")
    check("other-model-9" not in note, "the foreign model id is not copied into the error file")
    check(not usage.exists(), "a mismatch without a snapshot writes no cache file")


def test_stderr_event(tmp: Path):
    raw = tmp / "answer.md"
    raw.write_text(VERDICT, encoding="utf-8")
    err = tmp / "stderr.txt"
    err.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    usage = tmp / "from-err.json"
    proc = _run_cli(raw, usage, err)
    check(proc.returncode == 0 and raw.read_text(encoding="utf-8") == VERDICT, "stderr usage leaves the answer in place")
    doc = json.loads(usage.read_text(encoding="utf-8"))
    check(doc["weekly"]["used_percent"] == 12.5 and SENTINEL not in usage.read_text(encoding="utf-8"),
          "stderr SSE still fills the cache")


def write_stub(path, body):
    path.write_text(body)
    path.chmod(0o755)


def bash(body, env=None):
    proc_env = os.environ.copy()
    proc_env.pop("CONCLAVE_MUSE_SHADOW", None)
    proc_env.pop("COUNCIL_MUSE_SHADOW", None)
    proc_env.pop("MUSE_USAGE_PATH", None)
    if env:
        proc_env.update(env)
    return subprocess.run(["bash", "-c", body], text=True, capture_output=True, env=proc_env)


def test_run_muse(model, tmp: Path):
    bindir = tmp / "bin"
    bindir.mkdir()
    args = tmp / "args"
    usage_obj = {
        "subscription": {
            "label": SENTINEL,
            "window": {"used_percent": 40, "window_duration_mins": 300, "resets_at": 1759900000},
            "weekly": {"used_percent": 12.5, "resets_at": 1760000000},
        }
    }
    journal = "\n".join([
        json.dumps({"payload_type": "run.lifecycle.started", "payload": {"note": SENTINEL}}),
        json.dumps({"payload_type": "run.output.delta", "payload": {"text": VERDICT}}),
        json.dumps({"payload_type": "response.subscription_usage", "payload": usage_obj}),
    ]) + "\n"
    write_stub(bindir / "muse", f"""#!/bin/sh
printf '%s\\n' "$@" > {args}
cat <<'EOF'
{journal}EOF
""")
    usage = tmp / "cache" / "muse-usage.json"
    home = tmp / "home"
    home.mkdir()
    proc = bash(f"""
set -eu
export HOME={home}
export COUNCIL_SOURCE_ONLY=1
. {COUNCIL}
export CONCLAVE_MUSE_SHADOW=1
export PATH="{bindir}:$PATH"
export MUSE_USAGE_PATH={usage}
MUSE_WONDER_MODEL={model}
DIR="$(mktemp -d)"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
PACKET="$(mktemp -d)"
printf 'brief\\n' >"$PACKET/PROMPT.md"
run_muse
[ "$(cat "$DIR/muse-shadow.status")" = ran ]
grep -qx implement "$DIR/voter-muse.md"
if grep -q '{SENTINEL}' "$DIR/voter-muse.md" "$DIR/voter-muse.raw.md" "$ERRORS_FILE"; then exit 1; fi
echo ok
""")
    recorded = args.read_text(encoding="utf-8") if args.exists() else ""
    doc = json.loads(usage.read_text(encoding="utf-8")) if usage.is_file() else None
    check(proc.returncode == 0, f"shadow run keeps the verdict ({proc.returncode} {proc.stderr})")
    check("--json" in recorded.splitlines() and recorded.splitlines().count("--json") == 1,
          f"the live launch passes --json once ({recorded!r})")
    check(doc and doc["window"]["used_percent"] == 40 and doc["weekly"]["used_percent"] == 12.5
          and ISO_TIME.match(doc["captured_at"]) and SENTINEL not in usage.read_text(encoding="utf-8"),
          f"shadow run writes the cache ({doc})")

    blocked = tmp / "not-dir"
    blocked.write_text("x", encoding="utf-8")
    bad_usage = blocked / "muse-usage.json"
    proc = bash(f"""
set -eu
export HOME={home}
export COUNCIL_SOURCE_ONLY=1
. {COUNCIL}
export CONCLAVE_MUSE_SHADOW=1
export PATH="{bindir}:$PATH"
export MUSE_USAGE_PATH={bad_usage}
MUSE_WONDER_MODEL={model}
DIR="$(mktemp -d)"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
PACKET="$(mktemp -d)"
printf 'brief\\n' >"$PACKET/PROMPT.md"
run_muse
[ "$(cat "$DIR/muse-shadow.status")" = ran ]
grep -qx implement "$DIR/voter-muse.md"
echo ok
""")
    check(proc.returncode == 0 and blocked.read_text(encoding="utf-8") == "x",
          f"a failed usage write still finishes the shadow leg ({proc.returncode} {proc.stderr})")

    write_stub(bindir / "muse", f"""#!/bin/sh
cat <<'EOF'
{json.dumps({"payload_type": "response.subscription_usage", "payload": usage_obj})}
EOF
exit 1
""")
    failed_usage = tmp / "failed-run.json"
    proc = bash(f"""
set -eu
export HOME={home}
export COUNCIL_SOURCE_ONLY=1
. {COUNCIL}
export CONCLAVE_MUSE_SHADOW=1
export PATH="{bindir}:$PATH"
export MUSE_USAGE_PATH={failed_usage}
MUSE_WONDER_MODEL={model}
DIR="$(mktemp -d)"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
PACKET="$(mktemp -d)"
printf 'brief\\n' >"$PACKET/PROMPT.md"
run_muse
[ "$(cat "$DIR/muse-shadow.status")" = skipped ]
[ ! -e "$DIR/voter-muse.md" ]
echo ok
""")
    failed_doc = json.loads(failed_usage.read_text(encoding="utf-8")) if failed_usage.is_file() else None
    check(proc.returncode == 0 and failed_doc and failed_doc["window"]["used_percent"] == 40,
          f"a failed muse exit still stores usage and skips the verdict ({proc.returncode} {proc.stderr} {failed_doc})")

    write_stub(bindir / "muse", f"""#!/bin/sh
cat <<'EOF'
{json.dumps({"payload_type": "run.model.configured", "payload": {"model_id": "other-model-9"}})}
{json.dumps({"payload_type": "run.output.delta", "payload": {"text": VERDICT}})}
{json.dumps({"payload_type": "response.subscription_usage", "payload": usage_obj})}
EOF
""")
    mismatch_usage = tmp / "mismatch-run.json"
    proc = bash(f"""
set -eu
export HOME={home}
export COUNCIL_SOURCE_ONLY=1
. {COUNCIL}
export CONCLAVE_MUSE_SHADOW=1
export PATH="{bindir}:$PATH"
export MUSE_USAGE_PATH={mismatch_usage}
MUSE_WONDER_MODEL={model}
DIR="$(mktemp -d)"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
PACKET="$(mktemp -d)"
printf 'brief\\n' >"$PACKET/PROMPT.md"
run_muse
[ "$(cat "$DIR/muse-shadow.status")" = skipped ]
[ ! -e "$DIR/voter-muse.md" ]
grep -qx 'muse: Wonder model unavailable; skipping shadow leg' "$ERRORS_FILE"
if grep -q other-model-9 "$ERRORS_FILE"; then exit 1; fi
echo ok
""")
    mismatch_doc = json.loads(mismatch_usage.read_text(encoding="utf-8")) if mismatch_usage.is_file() else None
    check(proc.returncode == 0 and mismatch_doc and mismatch_doc["weekly"]["used_percent"] == 12.5,
          f"a foreign journal model skips the verdict and still stores usage ({proc.returncode} {proc.stderr})")

    default_home = tmp / "default-home"
    default_home.mkdir()
    write_stub(bindir / "muse", f"""#!/bin/sh
cat <<'EOF'
{json.dumps({"payload_type": "run.output.delta", "payload": {"text": VERDICT}})}
{json.dumps({"payload_type": "response.subscription_usage", "payload": usage_obj})}
EOF
""")
    proc = bash(f"""
set -eu
export HOME={default_home}
unset MUSE_USAGE_PATH
export COUNCIL_SOURCE_ONLY=1
. {COUNCIL}
export CONCLAVE_MUSE_SHADOW=1
export PATH="{bindir}:$PATH"
MUSE_WONDER_MODEL={model}
DIR="$(mktemp -d)"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
PACKET="$(mktemp -d)"
printf 'brief\\n' >"$PACKET/PROMPT.md"
run_muse
[ "$(cat "$DIR/muse-shadow.status")" = ran ]
echo ok
""")
    default_file = default_home / ".cache" / "quartermaster" / "muse-usage.json"
    default_doc = json.loads(default_file.read_text(encoding="utf-8")) if default_file.is_file() else None
    check(proc.returncode == 0 and default_doc and default_doc["window"]["window_duration_mins"] == 300,
          f"the default path is ~/.cache/quartermaster/muse-usage.json ({proc.returncode} {proc.stderr} {default_doc})")


def main():
    model = pin()
    test_fixture_shape()
    test_last_event_and_rejects()
    test_jsonl_and_embedded_sse()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        test_atomic_write(tmp)
        test_cli_fixture_and_silence(tmp)
        test_cli_jsonl_rewrite_and_failure(tmp, model)
        test_stderr_event(tmp)
        test_run_muse(model, tmp)
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
