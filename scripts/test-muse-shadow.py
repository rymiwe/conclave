#!/usr/bin/env python3
"""Muse Wonder shadow leg: family check, exclusion, skip, and agreement.

The model id in these tests is a fixture. The launcher reads
MUSE_WONDER_MODEL from the resolver, which asks muse-models.py.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("jev_judge", SCRIPTS / "jev-judge.py")
jev_judge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev_judge)

COUNCIL = SCRIPTS / "council.sh"
RECORD = SCRIPTS / "record-run.py"
SENTINEL = "MUSE_VERDICT_SENTINEL_9f3a"
_USAGE_SANDBOX = Path(tempfile.mkdtemp(prefix="muse-usage-sandbox-"))

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
    """A Wonder fixture. Not read from the resolver."""
    return "muse-wonder-1.0"


def repo_has_no_hardcoded_wonder_id():
    """Production files name the family, not an id.

    Tests hold fixtures. Handoffs quote the old pin. Both are skipped.
    """
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", "tmp", "__pycache__", ".claude"} for part in path.parts):
            continue
        if path.parent == SCRIPTS and path.name.startswith("test-"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "muse-wonder-" in text or "muse-spark-" in text:
            hits.append(str(path.relative_to(ROOT)))
    check(hits == [], f"model id is not hardcoded outside tests and handoffs ({hits})")


def bash(body, env=None):
    proc_env = os.environ.copy()
    proc_env.pop("CONCLAVE_MUSE_SHADOW", None)
    proc_env.pop("COUNCIL_MUSE_SHADOW", None)
    proc_env["MUSE_USAGE_PATH"] = str(_USAGE_SANDBOX / "muse-usage.json")
    if env:
        proc_env.update(env)
    return subprocess.run(
        ["bash", "-c", body],
        text=True,
        capture_output=True,
        env=proc_env,
    )


def source_head():
    return f"""
set -eu
export COUNCIL_SOURCE_ONLY=1
. {COUNCIL}
"""


SCHEMA = """## Findings
one
## Approach
lean
## Would not do
none
## Uncertainties
none
"""


def launch_shape(text, model):
    """True when the argv has exactly --contributor-mode off, never on, and the model id is the pin."""
    lines = [line for line in text.splitlines() if line]
    pairs = list(zip(lines, lines[1:]))
    joined = " ".join(lines)
    models = [nxt for cur, nxt in pairs if cur == "--model"]
    bad_id = any("contributor" in mid or "spark" in mid for mid in models)
    return (
        joined.count("--contributor-mode off") == 1
        and "--contributor-mode on" not in joined
        and models == [model]
        and not bad_id
    )


def test_pin_and_launch(model):
    proc = bash(source_head() + f"""
MODEL={model}
MUSE_WONDER_MODEL="$MODEL"
DIR=/tmp/muse-shadow-pin
PACKET=/tmp/muse-shadow-pin-packet
if ! muse_pin_usable; then echo "pin rejected" >&2; exit 1; fi
args="$(muse_launch_argv)"
printf '%s\n' "$args"
printf '%s\n' "$args" | grep -qx -- '--disable-shell'
printf '%s\n' "$args" | grep -qx -- '--disable-write'
printf '%s\n' "$args" | grep -qx -- '--disable-web-tools'
printf '%s\n' "$args" | grep -qx -- '--provider'
printf '%s\n' "$args" | grep -qx meta
printf '%s\n' "$args" | grep -qx -- '--json'
[ "$(printf '%s\n' "$args" | grep -cx -- '--json')" = 1 ]
if printf '%s\n' "$args" | grep -qx -- '--reasoning-effort'; then echo "shadow effort" >&2; exit 1; fi
""")
    check(proc.returncode == 0, f"launch command pins the model ({proc.stderr.strip()})")
    check(launch_shape(proc.stdout, model), f"launch has contributor-mode off and the pin ({proc.stdout!r})")


def test_env_alias():
    proc = bash(source_head() + r"""
unset CONCLAVE_MUSE_SHADOW COUNCIL_MUSE_SHADOW || true
if muse_shadow_on; then echo default-on; exit 1; fi
export COUNCIL_MUSE_SHADOW=1
if ! muse_shadow_on; then echo alias-off; exit 1; fi
export CONCLAVE_MUSE_SHADOW=0
if muse_shadow_on; then echo conclave-lost; exit 1; fi
export CONCLAVE_MUSE_SHADOW=1
if ! muse_shadow_on; then echo conclave-off; exit 1; fi
echo ok
""")
    check(proc.returncode == 0 and proc.stdout.strip() == "ok",
          f"CONCLAVE_MUSE_SHADOW wins over the COUNCIL_ alias ({proc.stdout} {proc.stderr})")


def test_skip_missing(model):
    proc = bash(source_head() + f"""
export CONCLAVE_MUSE_SHADOW=1
export PATH=/usr/bin:/bin
MUSE_WONDER_MODEL={model}
DIR="$(mktemp -d)"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
PIDS=""
launch_muse_shadow
status="$(cat "$DIR/muse-shadow.status")"
[ "$status" = skipped ]
grep -qx 'muse: CLI not found; skipping shadow leg' "$ERRORS_FILE"
[ ! -e "$DIR/voter-muse.md" ]
echo ok
""", env={"PATH": "/usr/bin:/bin"})
    check(proc.returncode == 0, f"missing muse skips ({proc.returncode} {proc.stderr})")


def write_stub(path, body):
    path.write_text(body)
    path.chmod(0o755)


def test_skip_auth_and_bad_pin(model, tmp: Path):
    bindir = tmp / "bin"
    bindir.mkdir()
    args = tmp / "args"
    write_stub(bindir / "muse", f"""#!/bin/sh
printf '%s\\n' "$0" "$@" > {args}
echo 'missing meta credentials: run `muse login` or set META_API_KEY' >&2
exit 1
""")
    proc = bash(source_head() + f"""
export CONCLAVE_MUSE_SHADOW=1
export PATH="{bindir}:$PATH"
MUSE_WONDER_MODEL={model}
DIR="$(mktemp -d)"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
PACKET="$(mktemp -d)"
printf 'brief\\n' >"$PACKET/PROMPT.md"
before="$(cksum "$PACKET/PROMPT.md")"
run_muse
after="$(cksum "$PACKET/PROMPT.md")"
[ "$before" = "$after" ]
[ "$(cat "$DIR/muse-shadow.status")" = skipped ]
[ ! -e "$DIR/voter-muse.md" ]
grep -qx 'muse: auth failure; skipping shadow leg' "$ERRORS_FILE"
echo ok
""")
    check(proc.returncode == 0, f"auth failure skips ({proc.returncode} {proc.stderr})")
    recorded = args.read_text(encoding="utf-8") if args.exists() else ""
    check(launch_shape(recorded, model),
          f"executed command pins the model ({recorded!r})")

    spark_args = tmp / "spark-args"
    write_stub(bindir / "muse", f"""#!/bin/sh
printf '%s\\n' "$@" > {spark_args}
echo ok
""")
    proc = bash(source_head() + f"""
export CONCLAVE_MUSE_SHADOW=1
export PATH="{bindir}:$PATH"
MUSE_WONDER_MODEL=muse-spark-1.3
DIR="$(mktemp -d)"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
PIDS=""
launch_muse_shadow
[ "$(cat "$DIR/muse-shadow.status")" = skipped ]
grep -qx 'muse: Wonder model unavailable; skipping shadow leg' "$ERRORS_FILE"
echo ok
""")
    check(proc.returncode == 0 and not spark_args.exists(),
          f"a Spark id is not launched ({proc.returncode} {proc.stderr} exists={spark_args.exists()})")

    contrib_args = tmp / "contrib-args"
    write_stub(bindir / "muse", f"""#!/bin/sh
printf '%s\\n' "$@" > {contrib_args}
echo ok
""")
    proc = bash(source_head() + f"""
export CONCLAVE_MUSE_SHADOW=1
export PATH="{bindir}:$PATH"
MUSE_WONDER_MODEL={model}-contributor
DIR="$(mktemp -d)"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
PIDS=""
launch_muse_shadow
[ "$(cat "$DIR/muse-shadow.status")" = skipped ]
grep -qx 'muse: Wonder model unavailable; skipping shadow leg' "$ERRORS_FILE"
echo ok
""")
    check(proc.returncode == 0 and not contrib_args.exists(),
          f"a contributor id is not launched ({proc.returncode} {proc.stderr})")

    other_args = tmp / "other-args"
    write_stub(bindir / "muse", f"""#!/bin/sh
printf '%s\\n' "$@" > {other_args}
echo ok
""")
    proc = bash(source_head() + f"""
export CONCLAVE_MUSE_SHADOW=1
export PATH="{bindir}:$PATH"
MUSE_WONDER_MODEL=other-model-9
DIR="$(mktemp -d)"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
PIDS=""
launch_muse_shadow
[ "$(cat "$DIR/muse-shadow.status")" = skipped ]
grep -qx 'muse: Wonder model unavailable; skipping shadow leg' "$ERRORS_FILE"
echo ok
""")
    check(proc.returncode == 0 and not other_args.exists(),
          f"an id other than the pin is not launched ({proc.returncode} {proc.stderr})")


def test_cwd_and_verdict(model, tmp: Path):
    bindir = tmp / "ok-bin"
    bindir.mkdir()
    pwd_file = tmp / "pwd"
    args = tmp / "ok-args"
    write_stub(bindir / "muse", f"""#!/bin/sh
printf '%s\\n' "$PWD" > {pwd_file}
printf '%s\\n' "$@" > {args}
cat <<'EOF'
muse: workspace root: ignored
## Findings
{SENTINEL}
## Approach
lean
## Would not do
none
## Uncertainties
none
## Decision
implement
EOF
""")
    proc = bash(source_head() + f"""
export CONCLAVE_MUSE_SHADOW=1
export PATH="{bindir}:$PATH"
MUSE_WONDER_MODEL={model}
ID=muse-shadow
REPO="$(mktemp -d)"
git init -q -b main "$REPO"
git -C "$REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
DIR="$(mktemp -d)"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
PACKET_TMP="$(alloc_packet_tmpdir)"
PACKET="$PACKET_TMP"
printf 'packet brief\\n' >"$PACKET/PROMPT.md"
run_muse
[ "$(cat "$DIR/muse-shadow.status")" = ran ]
grep -q '{SENTINEL}' "$DIR/voter-muse.md"
grep -q '^## Decision$' "$DIR/voter-muse.md"
grep -qx implement "$DIR/voter-muse.md"
if grep -q 'muse: workspace' "$DIR/voter-muse.md"; then exit 1; fi
python3 - "$PACKET" "$REPO" "{ROOT}" <<'PY'
import sys
from pathlib import Path
packet, repo, root = (Path(p).resolve() for p in sys.argv[1:])
def under(child, parent):
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
if under(packet, repo) or under(packet, root):
    raise SystemExit("packet is inside a repo")
PY
echo ok
""")
    check(proc.returncode == 0, f"shadow writes a verdict from the packet cwd ({proc.returncode} {proc.stderr})")
    got_pwd = pwd_file.read_text(encoding="utf-8").strip() if pwd_file.exists() else ""
    recorded = args.read_text(encoding="utf-8") if args.exists() else ""
    check(got_pwd.startswith("/") and "council-pkt." in got_pwd,
          f"cwd is the out-of-repo packet ({got_pwd})")
    check("--disable-shell" in recorded and launch_shape(recorded, model),
          f"executed tool-less command carries the pin ({recorded!r})")


def test_exclusion(tmp: Path):
    ballot = tmp / "ballot"
    ballot.mkdir()
    (ballot / "brief.md").write_text("brief only\n", encoding="utf-8")
    (ballot / "voter-claude.md").write_text(SCHEMA, encoding="utf-8")
    (ballot / "voter-codex.md").write_text(SCHEMA, encoding="utf-8")
    (ballot / "voter-claude.ok").write_text("", encoding="utf-8")
    (ballot / "voter-codex.ok").write_text("", encoding="utf-8")
    (ballot / "voter-muse.md").write_text(SCHEMA + f"\n{SENTINEL}\n## Decision\nimplement\n", encoding="utf-8")
    (ballot / "voter-muse.ok").write_text("", encoding="utf-8")
    (ballot / "voter-muse.raw.md").write_text(SENTINEL + "\n", encoding="utf-8")
    (ballot / "muse-shadow.status").write_text("ran\n", encoding="utf-8")
    (ballot / "judgment.md").write_text("implement\n", encoding="utf-8")
    (ballot / "options.md").write_text("options\n", encoding="utf-8")
    (ballot / "header.json").write_text("{}\n", encoding="utf-8")
    proc = bash(source_head() + f"""
DIR={ballot}
VOTERS=claude,codex
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
publish_voter_opinions
[ "$OPINION_N" = 2 ]
if grep -q '{SENTINEL}' "$DIR"/opinion-*.md; then exit 1; fi
echo ok
""")
    check(proc.returncode == 0, f"opinions exclude the shadow file ({proc.returncode} {proc.stderr})")
    opinions = jev_judge.load_decision_opinions(ballot)
    state = jev_judge.build_state((ballot / "brief.md").read_text(encoding="utf-8"), opinions)
    chair = jev_judge.chair_inputs(ballot)
    check(SENTINEL not in state and SENTINEL not in chair,
          "Jev state and chair inputs exclude the Muse verdict")
    check(all(SENTINEL not in op for op in opinions), "opinion list has no Muse verdict")


def record(tmp: Path, extra):
    path = tmp / "runs.jsonl"
    proc = subprocess.run(
        ["python3", str(RECORD),
         "--phase", "after", "--status", "completed", "--started", "100",
         "--path", str(path), "--run-id", "muse-1", "--kind", "design",
         *extra],
        text=True, capture_output=True,
    )
    row = None
    if path.exists() and path.read_text(encoding="utf-8").strip():
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    path.unlink(missing_ok=True)
    return proc, row


def test_agreement(model, tmp: Path):
    proc, row = record(tmp, [
        "--muse-status", "ran", "--muse-model", model,
        "--muse-decision", "implement", "--final-decision", "implement",
    ])
    check(proc.returncode == 0 and row and row["muse_shadow"]["agreed"] is True
          and row["muse_shadow"]["model"] == model and row["muse_shadow"]["status"] == "ran",
          f"matching verdict is agreed ({proc.stderr.strip()} {row})")

    proc, row = record(tmp, [
        "--muse-status", "ran", "--muse-model", model,
        "--muse-decision", "no automatic decision", "--final-decision", "implement",
    ])
    check(proc.returncode == 0 and row and row["muse_shadow"]["agreed"] is False,
          "a different verdict is not agreed")

    proc, row = record(tmp, [
        "--muse-status", "skipped", "--muse-model", model,
        "--muse-decision", "implement", "--final-decision", "implement",
    ])
    check(proc.returncode == 0 and row and row["muse_shadow"]["agreed"] is None
          and row["muse_shadow"]["status"] == "skipped",
          "a skipped leg records no agreement")

    proc, row = record(tmp, ["--muse-status", "substituted"])
    check(proc.returncode == 2 and row is None, "a bad muse status writes nothing")

    proc, row = record(tmp, [])
    check(proc.returncode == 0 and row and "muse_shadow" not in row,
          "an armed-off outcome omits the shadow field")

    runs = tmp / "wired.jsonl"
    ballot = tmp / "wired-ballot"
    ballot.mkdir()
    (ballot / "voter-muse.md").write_text(SCHEMA + "## Decision\nimplement with changes\n", encoding="utf-8")
    (ballot / "judgment.md").write_text(
        "kind: design\ndecision: implement with changes\nimplement with changes\n",
        encoding="utf-8",
    )
    proc = bash(source_head() + f"""
export SEAT_USAGE_RUNS={runs}
export HOME={tmp / "home"}
export CONCLAVE_MUSE_SHADOW=1
ID=wired
KIND=design
DIFFICULTY=low
DIR={ballot}
RUN_OUTCOME=1
RUN_ID=wired-1
MUSE_SHADOW_STATUS=ran
MUSE_WONDER_MODEL={model}
VOTERS=claude,codex
exit 0
""")
    rows = [json.loads(line) for line in runs.read_text(encoding="utf-8").splitlines() if line.strip()] if runs.exists() else []
    after = [row for row in rows if row.get("phase") == "after"]
    check(proc.returncode == 0 and len(after) == 1
          and after[0]["muse_shadow"]["agreed"] is True
          and after[0]["muse_shadow"]["model"] == model,
          f"the outcome trap records agreement ({proc.returncode} {proc.stderr} {after})")


def test_runtime_unavailable(model, tmp: Path):
    bindir = tmp / "unavail-bin"
    bindir.mkdir()
    write_stub(bindir / "muse", """#!/bin/sh
echo 'model selection failed: using the provider default model muse-spark-1.3' >&2
echo '## Findings' 
echo substituted
exit 0
""")
    proc = bash(source_head() + f"""
export CONCLAVE_MUSE_SHADOW=1
export PATH="{bindir}:$PATH"
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
echo ok
""")
    check(proc.returncode == 0, f"a substituted model is discarded ({proc.returncode} {proc.stderr})")


def main():
    model = pin()
    repo_has_no_hardcoded_wonder_id()
    test_pin_and_launch(model)
    test_env_alias()
    test_skip_missing(model)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        test_skip_auth_and_bad_pin(model, tmp)
        test_cwd_and_verdict(model, tmp)
        test_exclusion(tmp)
        test_agreement(model, tmp)
        test_runtime_unavailable(model, tmp)
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
