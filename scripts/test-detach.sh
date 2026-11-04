#!/usr/bin/env bash
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
CONCLAVE="$ROOT/scripts/conclave.sh"
export CONCLAVE_SKIP_UPDATE_CHECK=1

ID="test-detach-$$"
SESSION="conclave-$ID"
DIR="$ROOT/tmp/council/$ID"
DOT_ID="dot.$$"
DOT_SESSION="conclave+dot_d$$"
DOT_DIR="$ROOT/tmp/council/$DOT_ID"

work="$(mktemp -d)"
real_tmux=""
if command -v tmux >/dev/null 2>&1; then
  real_tmux="$(command -v tmux)"
fi

# A directory that contains tmux also contains the rest of the system. Drop the
# name, not the directory, or conclave.sh dies before it can say tmux is missing.
safe_bin="$work/safe-bin"
mkdir -p "$safe_bin"
IFS=':' read -r -a path_parts <<< "${PATH:-/usr/bin:/bin}"
for part in "${path_parts[@]}"; do
  [ -n "$part" ] && [ -d "$part" ] || continue
  for bin in "$part"/*; do
    [ -x "$bin" ] || continue
    name="$(basename "$bin")"
    [ "$name" = "tmux" ] && continue
    [ -e "$safe_bin/$name" ] && continue
    ln -s "$bin" "$safe_bin/$name"
  done
done
safe_path="$safe_bin"
if PATH="$safe_path" command -v tmux >/dev/null 2>&1; then
  printf 'FAIL safe PATH still contains tmux\n' >&2
  exit 1
fi

kill_tree() {
  local pid="$1" child
  [ -n "$pid" ] || return 0
  for child in $(ps -o pid= --ppid "$pid" 2>/dev/null || true); do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

cleanup() {
  local f pid session
  if [ -d "${work:-}/stub" ]; then
    for f in "$work/stub"/*.pid; do
      [ -f "$f" ] || continue
      pid="$(cat "$f" 2>/dev/null || true)"
      kill_tree "$pid"
    done
  fi
  if [ -n "${real_tmux:-}" ] && [ -f "${work:-}/real-sessions" ]; then
    while IFS= read -r session; do
      [ -n "$session" ] || continue
      TMUX_TMPDIR="$work/tmuxdir" "$real_tmux" -L "$session" kill-server >/dev/null 2>&1 || true
    done <"$work/real-sessions"
  fi
  rm -rf "$work" "$DIR" "$DOT_DIR" "${REAL_DIR:-}" "${JUDGE_DIR:-}" "${JUDGE_REAL_DIR:-}"
}
trap cleanup EXIT

mkdir -p "$work/bin" "$work/stub" "$work/log" "$work/home/.codex" "$work/start" "$work/tmuxdir"
mkdir -p "$DIR" "$DOT_DIR"
: >"$work/stub/calls"
: >"$work/real-sessions"
printf 'kind: design\n\n# Brief\n' >"$DIR/brief.md"
printf 'kind: design\n\n# Brief\n' >"$DOT_DIR/brief.md"
git init -q -b main "$work/start"
printf '%s\n' '{"models":[{"slug":"gpt-test-astra","visibility":"list","priority":1}]}' \
  >"$work/home/.codex/models_cache.json"
printf '%s\n' '{"models":[{"modelId":"muse-wonder-full-1.0","releaseDate":"2026-10-12"}]}' \
  >"$work/muse-catalog.json"

cat >"$work/bin/tmux" <<'EOF'
#!/usr/bin/env bash
set -eu
mkdir -p "$STUB_LOG"
printf '%q ' "$@" >>"$STUB_LOG/calls"
printf '\n' >>"$STUB_LOG/calls"

rewritten=()
for a in "$@"; do
  if [ -n "${STUB_REAL_CONCLAVE:-}" ] && [ "$a" = "$STUB_REAL_CONCLAVE" ]; then
    rewritten+=("$STUB_CHILD")
  else
    rewritten+=("$a")
  fi
done
if [ -n "${STUB_REAL_TMUX:-}" ]; then
  exec "$STUB_REAL_TMUX" "${rewritten[@]}"
fi

args=("${rewritten[@]}")
i=0
n=${#args[@]}
while [ "$i" -lt "$n" ]; do
  case "${args[$i]}" in
    -L) i=$((i + 2)) ;;
    -f) i=$((i + 2)) ;;
    *) break ;;
  esac
done
cmd="${args[$i]}"
i=$((i + 1))

alive_pid() {
  local name="$1" pid
  local pidfile="$STUB_LOG/$name.pid"
  [ -f "$pidfile" ] || return 1
  pid="$(cat "$pidfile")"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

case "$cmd" in
  has-session)
    target=""
    while [ "$i" -lt "$n" ]; do
      if [ "${args[$i]}" = "-t" ]; then
        i=$((i + 1))
        target="${args[$i]}"
      fi
      i=$((i + 1))
    done
    if alive_pid "${target#=}"; then
      exit 0
    fi
    exit 1
    ;;
  new-session)
    sess="" cwd=""
    while [ "$i" -lt "$n" ]; do
      case "${args[$i]}" in
        -d) i=$((i + 1)) ;;
        -s)
          i=$((i + 1))
          sess="${args[$i]}"
          i=$((i + 1))
          ;;
        -c)
          i=$((i + 1))
          cwd="${args[$i]}"
          i=$((i + 1))
          ;;
        --)
          i=$((i + 1))
          break
          ;;
        *)
          printf 'stub tmux: unexpected %s\n' "${args[$i]}" >&2
          exit 1
          ;;
      esac
    done
    if alive_pid "$sess"; then
      printf 'duplicate session: %s\n' "$sess" >&2
      exit 1
    fi
    (
      trap '' HUP
      cd "$cwd"
      exec "${args[@]:$i}"
    ) </dev/null >/dev/null 2>&1 &
    printf '%s\n' "$!" >"$STUB_LOG/$sess.pid"
    disown "$!" 2>/dev/null || true
    exit 0
    ;;
  *)
    printf 'stub tmux: unknown command %s\n' "$cmd" >&2
    exit 1
    ;;
esac
EOF

cat >"$work/bin/fake-conclave" <<'EOF'
#!/usr/bin/env bash
set -eu
mkdir -p "$FAKE_LOG"
printf '%s\n' "$@" >"$FAKE_LOG/child.argv"
pwd -P >"$FAKE_LOG/child.cwd"
env >"$FAKE_LOG/child.env"
dir="$FAKE_ROOT/tmp/council/$1"
case "${FAKE_MODE:-progress}" in
  progress)
    printf 'fake: started\n'
    python3 "$FAKE_ROOT/scripts/stage_hook.py" progress run started --dir "$dir"
    exec sleep 30
    ;;
  silent)
    printf 'fake: working quietly\n'
    exec sleep 30
    ;;
  die)
    printf 'fake: brief rejected\n' >&2
    exit 1
    ;;
  *)
    printf 'fake: unknown mode %s\n' "${FAKE_MODE:-}" >&2
    exit 1
    ;;
esac
EOF

cat >"$work/bin/judge-probe" <<'EOF'
#!/usr/bin/env bash
set -eu
dir="$FAKE_ROOT/tmp/council/$1"
mkdir -p "$dir" "$FAKE_LOG"
export CONCLAVE_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$FAKE_ROOT/scripts/conclave.sh"
key="$(load_jev_api_key || true)"
if [ -n "$key" ]; then
  printf '%s' "$key" | sha256sum | awk '{print $1}' >"$FAKE_LOG/judge.sha"
  printf '%s\n' yes >"$FAKE_LOG/judge.present"
else
  printf '%s\n' missing >"$FAKE_LOG/judge.sha"
  printf '%s\n' no >"$FAKE_LOG/judge.present"
fi
unset key
env >"$FAKE_LOG/judge.env"
python3 "$FAKE_ROOT/scripts/stage_hook.py" progress run started --dir "$dir"
exec sleep 30
EOF

cat >"$work/bin/op-ok" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "read" ] && [ -n "${2:-}" ]; then
  printf '%s' 'sentinel-jev-detach'
  exit 0
fi
exit 1
EOF

cat >"$work/bin/op-fail" <<'EOF'
#!/bin/sh
exit 1
EOF

cat >"$work/bin/cursor-agent" <<'EOF'
#!/bin/sh
if [ "${1:-}" = models ]; then
  printf '%s\n' claude-fable-9-9-thinking-xhigh grok-9.9-xhigh grok-9.9-xhigh-fast grok-9.9-high-fast
fi
exit 0
EOF
cat >"$work/bin/grok" <<'EOF'
#!/bin/sh
if [ "${1:-}" = models ]; then
  printf '%s\n' "Default model: grok-test-1"
fi
exit 0
EOF
cat >"$work/bin/codex" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then
  printf '%s\n' "codex-cli 0.200.0"
fi
exit 0
EOF
cat >"$work/bin/seat-usage" <<'EOF'
#!/bin/sh
exit 1
EOF
cat >"$work/bin/claude" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$work/bin/tmux" "$work/bin/fake-conclave" "$work/bin/judge-probe" \
  "$work/bin/op-ok" "$work/bin/op-fail" "$work/bin/cursor-agent" \
  "$work/bin/grok" "$work/bin/codex" "$work/bin/seat-usage" "$work/bin/claude"

passed=0
failed=0
rc=0
check() {
  local name="$1"
  shift
  if "$@"; then
    passed=$((passed + 1))
    printf 'ok %s\n' "$name"
  else
    failed=$((failed + 1))
    printf 'FAIL %s (rc=%s)\n' "$name" "$rc" >&2
    printf '%s\n' '--- stdout ---' >&2
    cat "$work/out" >&2 || true
    printf '%s\n' '--- stderr ---' >&2
    cat "$work/err" >&2 || true
  fi
}

launch() {
  local -a extra=()
  rm -f "$work/out" "$work/err"
  if [ -n "${STUB_REAL_TMUX:-}" ]; then
    extra+=(STUB_REAL_TMUX="$STUB_REAL_TMUX")
  fi
  if [ -n "${TMUX_TMPDIR:-}" ]; then
    extra+=(TMUX_TMPDIR="$TMUX_TMPDIR")
  fi
  if [ -n "${JUDGE_REF+x}" ]; then
    extra+=(CONCLAVE_JEV_OP_REF="$JUDGE_REF")
  fi
  if [ -n "${JUDGE_OP+x}" ]; then
    extra+=(CONCLAVE_JEV_OP="$JUDGE_OP")
  fi
  if [ -n "${JUDGE_TYPESAFE+x}" ]; then
    extra+=(TYPESAFE_API_KEY="$JUDGE_TYPESAFE")
  fi
  if [ -n "${JUDGE_JEV+x}" ]; then
    extra+=(JEV_API_KEY="$JUDGE_JEV")
  fi
  if [ -n "${DETACH_CHILD+x}" ]; then
    extra+=(STUB_CHILD="$DETACH_CHILD")
  fi
  set +e
  (
    cd "$work/start" || exit 1
    env \
      CONCLAVE_SKIP_UPDATE_CHECK=1 \
      CONCLAVE_GITHUB_HTTPS=0 \
      CONCLAVE_ARGON=0 \
      CONCLAVE_JEV_OP=/bin/false \
      CONCLAVE_MUSE_CATALOG="$work/muse-catalog.json" \
      CONCLAVE_CODEX_BIN="$work/bin/codex" \
      CONCLAVE_CURSOR_AGENT="$work/bin/cursor-agent" \
      CONCLAVE_SEAT_USAGE="$work/bin/seat-usage" \
      CONCLAVE_DETACH_WAIT="${CONCLAVE_DETACH_WAIT:-5}" \
      HOME="$work/home" \
      PATH="${LAUNCH_PATH:-$work/bin:$PATH}" \
      SEAT_USAGE_RUNS="$work/runs.jsonl" \
      STUB_LOG="$work/stub" \
      STUB_CHILD="$work/bin/fake-conclave" \
      STUB_REAL_CONCLAVE="$CONCLAVE" \
      FAKE_LOG="$work/log" \
      FAKE_ROOT="$ROOT" \
      FAKE_MODE="${FAKE_MODE:-progress}" \
      "${extra[@]}" \
      bash "$CONCLAVE" "$@"
  ) >"$work/out" 2>"$work/err"
  rc=$?
  set -e
}

new_session_count() {
  local n=0
  if [ -f "$work/stub/calls" ]; then
    n="$(grep -c 'new-session' "$work/stub/calls" || true)"
  fi
  printf '%s\n' "${n:-0}"
}

stdout_is() {
  # The caller's $(printf ...) drops the final newline. Put one back.
  printf '%s\n' "$1" | cmp -s - "$work/out"
}

err_has() { grep -F -q -- "$1" "$work/err"; }
err_lacks() { ! grep -F -q -- "$1" "$work/err"; }
out_has() { grep -F -q -- "$1" "$work/out"; }
not_alive() { ! session_alive "$1"; }
argv_lacks() { ! grep -F -x -q -- "$1" "$work/log/child.argv"; }
calls_lack() { ! grep -F -q -- "$1" "$work/stub/calls"; }

session_alive() {
  local -a extra=()
  if [ -n "${STUB_REAL_TMUX:-}" ]; then
    extra+=(STUB_REAL_TMUX="$STUB_REAL_TMUX")
  fi
  if [ -n "${TMUX_TMPDIR:-}" ]; then
    extra+=(TMUX_TMPDIR="$TMUX_TMPDIR")
  fi
  env PATH="$work/bin:${PATH}" STUB_LOG="$work/stub" STUB_REAL_CONCLAVE="$CONCLAVE" \
    STUB_CHILD="$work/bin/fake-conclave" "${extra[@]}" \
    "$work/bin/tmux" -L "$1" has-session -t "=$1"
}

kill_stub_sessions() {
  local f pid
  for f in "$work/stub"/*.pid; do
    [ -f "$f" ] || continue
    pid="$(cat "$f" 2>/dev/null || true)"
    kill_tree "$pid"
    rm -f "$f"
  done
  sleep 0.2
}

(
  die() { printf '%s\n' "$*" >&2; exit 1; }
  # shellcheck disable=SC1091
  . "$ROOT/scripts/detach.sh"
  [ "$(detach_session_name 'rel.note')" = 'conclave+rel_dnote' ]
  [ "$(detach_session_name 'a.b')" = 'conclave+a_db' ]
  [ "$(detach_session_name 'a_b')" = 'conclave-a_b' ]
  [ "$(detach_session_name 'a:b')" = 'conclave+a_cb' ]
  [ "$(detach_session_name 'a.b_c')" = 'conclave+a_db__c' ]
  [ "$(detach_session_name 'a.b')" != "$(detach_session_name 'a_b')" ]
  [ "$(detach_session_name 'a.b.c')" = 'conclave+a_db_dc' ]
  [ "$(detach_session_name 'a_db.c')" = 'conclave+a__db_dc' ]
  [ "$(detach_session_name 'a.b.c')" != "$(detach_session_name 'a_db.c')" ]
)
check "session names encode dot and colon" true

mode_names="$(grep -oE '[A-Z][A-Z0-9_]*_MODE=1' "$CONCLAVE" | sed 's/=1$//' | sort -u)"
modes_ok=0
for mode_name in $mode_names; do
  grep -q "$mode_name" "$ROOT/scripts/detach.sh" || modes_ok=1
done
check "refused modes match conclave.sh" test "$modes_ok" -eq 0
check "detach drops both judge key names" grep -q 'env -u TYPESAFE_API_KEY -u JEV_API_KEY' "$ROOT/scripts/detach.sh"
check "conclave unsets both judge key names" grep -q 'unset TYPESAFE_API_KEY JEV_API_KEY' "$CONCLAVE"

launch "$ID" --detach
check "--detach after the id is refused" test "$rc" -eq 1
check "--detach after the id says it goes first" err_has "--detach goes first: scripts/conclave.sh --detach <id> [options]"

launch --detach
check "--detach with no id is refused" test "$rc" -eq 1
check "--detach with no id names the form" err_has "--detach goes first and takes a ballot id: scripts/conclave.sh --detach <id> [options]"

launch --detach --dry-run
check "--detach without an id is refused" test "$rc" -eq 1
check "--detach without an id names the form" err_has "takes a ballot id"

refuse_detach() {
  launch --detach "$ID" "$@"
  local shown="$*"
  check "detach refuses $shown" test "$rc" -eq 1
  check "detach refusal lists foreground modes ($shown)" err_has "stay in the foreground"
}
refuse_detach --ask hi
refuse_detach --findings
refuse_detach --verdicts
refuse_detach --judge
check "mode refusals do not start a session" test "$(new_session_count)" -eq 0

CONCLAVE_DETACH_WAIT=1.5 launch --detach "$ID"
check "a fractional wait is refused" test "$rc" -eq 1
check "a fractional wait names the variable" err_has "CONCLAVE_DETACH_WAIT must be a whole number of seconds"
check "a fractional wait does not start a session" test "$(new_session_count)" -eq 0

calls_before="$(wc -c <"$work/stub/calls")"
LAUNCH_PATH="$safe_path" launch --detach "$ID"
check "missing tmux is non-zero" test "$rc" -ne 0
check "missing tmux says tmux is required" err_has "needs tmux"
check "missing tmux says tmux is missing" err_has "tmux not found"
check "missing tmux says it never falls back" err_has "conclave.sh never falls back to nohup or setsid"
check "missing tmux does not offer nohup" err_lacks "use nohup"
check "missing tmux does not offer setsid" err_lacks "try setsid"
check "missing tmux does not call the stub" test "$calls_before" = "$(wc -c <"$work/stub/calls")"

rm -f "$DIR/progress.md" "$DIR/run.log"
FAKE_MODE=progress launch --detach "$ID" --dry-run
check "dry-run stays in the foreground" err_has "detach: --dry-run stays in the foreground; a real launch starts tmux session $SESSION"
check "dry-run does not call new-session" test "$(new_session_count)" -eq 0
check "dry-run writes no progress.md" test ! -e "$DIR/progress.md"
check "dry-run writes no run.log" test ! -e "$DIR/run.log"
check "dry-run continues" out_has "Dry-run preview:" 

FAKE_MODE=progress launch --detach "$ID"
want="$(printf 'session: %s\ndir: %s\nattach: tmux -L %s attach -t %s\n' "$SESSION" "$DIR" "$SESSION" "$SESSION")"
check "(a) exits 0" test "$rc" -eq 0
check "(a) stdout is the three lines" stdout_is "$want"
progress_line="$(tail -n 1 "$DIR/progress.md")"
check "(a) progress line is a local timestamp and run started" \
  grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2} run started$' <<<"$progress_line"
check "(a) session is alive" session_alive "$SESSION"
check "(a) child argv is the id" test "$(cat "$work/log/child.argv")" = "$ID"
check "(a) child argv has no --detach" argv_lacks "--detach"
check "(a) child cwd is the launch directory" test "$(cat "$work/log/child.cwd")" = "$(cd "$work/start" && pwd -P)"
keys_absent() {
  ! grep -E -q '^(TYPESAFE_API_KEY|JEV_API_KEY)=' "$work/log/child.env" \
    && ! grep -F -q 'sentinel-typesafe' "$work/log/child.env" \
    && ! grep -F -q 'sentinel-jev' "$work/log/child.env"
}
check "(a) child env has no judge keys" keys_absent
check "(a) new-session names the private server and the real script" \
  grep -F -q -- "-L $SESSION -f /dev/null new-session -d -s $SESSION -c $(cd "$work/start" && pwd -P) --" "$work/stub/calls"
check "(a) new-session passes the real conclave.sh" grep -F -q -- "$CONCLAVE" "$work/stub/calls"

before_ns="$(new_session_count)"
FAKE_MODE=progress launch --detach "$ID"
check "second launch is refused" test "$rc" -eq 1
check "second launch names the live session" err_has "tmux session $SESSION is already running"
check "second launch includes the attach hint" err_has "attach: tmux -L $SESSION attach -t $SESSION"
check "second launch does not call new-session" test "$(new_session_count)" = "$before_ns"
check "first session is still alive" session_alive "$SESSION"

kill_stub_sessions

printf 'OLD-LAUNCH-LINE\n' >"$DIR/run.log"
FAKE_MODE=die launch --detach "$ID"
check "(b) exits 1" test "$rc" -eq 1
check "(b) stderr has the child line" err_has "fake: brief rejected"
check "(b) stderr omits the seeded log line" err_lacks "OLD-LAUNCH-LINE"
check "(b) stderr says the session ended" err_has "ended"
check "(b) session is gone" not_alive "$SESSION"

printf '2020-01-01T00:00:00+00:00 run started\n' >"$DIR/progress.md"
FAKE_MODE=silent CONCLAVE_DETACH_WAIT=1 launch --detach "$ID"
check "(c) exits 3" test "$rc" -eq 3
check "(c) stderr says no progress line" err_has "wrote no progress line"
check "(c) stderr has the child line" err_has "fake: working quietly"
check "(c) stderr says the session was left running" err_has "Left running"
check "(c) session is still alive" session_alive "$SESSION"
check "(c) old progress line was not extended" test "$(cat "$DIR/progress.md")" = "2020-01-01T00:00:00+00:00 run started"
kill_stub_sessions

FAKE_MODE=progress launch --detach "$DOT_ID"
dot_want="$(printf 'session: %s\ndir: %s\nattach: tmux -L %s attach -t %s\n' "$DOT_SESSION" "$DOT_DIR" "$DOT_SESSION" "$DOT_SESSION")"
check "dotted id exits 0" test "$rc" -eq 0
check "dotted id prints the encoded session" stdout_is "$dot_want"
check "dotted id child argv is the raw id" test "$(cat "$work/log/child.argv")" = "$DOT_ID"
check "dotted id session is passed to tmux" grep -F -q -- "-L $DOT_SESSION" "$work/stub/calls"
check "dotted id does not pass a dotted -s" calls_lack "-s dot."
check "dotted id does not pass a dotted -L" calls_lack "-L dot."
kill_stub_sessions

file_lacks() {
  if [ ! -f "$1" ]; then
    return 0
  fi
  ! grep -F -q -- "$2" "$1"
}

judge_sentinel='sentinel-jev-detach'
judge_sha="$(printf '%s' "$judge_sentinel" | sha256sum | awk '{print $1}')"
JUDGE_ID="judge-$$"
JUDGE_DIR="$ROOT/tmp/council/$JUDGE_ID"
mkdir -p "$JUDGE_DIR"
printf 'kind: design\n\n# Brief\n' >"$JUDGE_DIR/brief.md"

before_ns="$(new_session_count)"
JUDGE_TYPESAFE='sentinel-env-only' JUDGE_JEV='sentinel-env-only' \
  launch --detach "$JUDGE_ID" --dry-run
check "dry-run with an env key still stays in the foreground" err_has "stays in the foreground"
check "dry-run with an env key does not mention the op ref" err_lacks "CONCLAVE_JEV_OP_REF"
check "dry-run with an env key does not start a session" test "$(new_session_count)" = "$before_ns"
unset JUDGE_TYPESAFE JUDGE_JEV

before_ns="$(new_session_count)"
JUDGE_TYPESAFE='sentinel-env-only' JUDGE_JEV='sentinel-env-only' \
  launch --detach "$JUDGE_ID"
check "env key without an op ref is refused" test "$rc" -ne 0
check "env key without an op ref names CONCLAVE_JEV_OP_REF" err_has "CONCLAVE_JEV_OP_REF is unset"
check "env key without an op ref says to run without --detach" err_has "Run without --detach"
check "env key without an op ref does not print the key" err_lacks "sentinel-env-only"
check "env key without an op ref does not start a session" test "$(new_session_count)" = "$before_ns"
unset JUDGE_TYPESAFE JUDGE_JEV
kill_stub_sessions

before_ns="$(new_session_count)"
JUDGE_REF='op://conclave/test/judge' JUDGE_OP="$work/bin/op-fail" \
  JUDGE_TYPESAFE='sentinel-env-only' JUDGE_JEV='sentinel-env-only' \
  launch --detach "$JUDGE_ID"
check "env key with a failing op is refused" test "$rc" -ne 0
check "env key with a failing op names CONCLAVE_JEV_OP_REF" err_has "reading CONCLAVE_JEV_OP_REF failed"
check "env key with a failing op says to run without --detach" err_has "Run without --detach"
check "env key with a failing op does not print the key" err_lacks "sentinel-env-only"
check "env key with a failing op does not start a session" test "$(new_session_count)" = "$before_ns"
unset JUDGE_REF JUDGE_OP JUDGE_TYPESAFE JUDGE_JEV
kill_stub_sessions

rm -f "$work/log/judge.sha" "$work/log/judge.present" "$work/log/judge.env" "$JUDGE_DIR/run.log"
DETACH_CHILD="$work/bin/judge-probe" \
  JUDGE_REF='op://conclave/test/judge' JUDGE_OP="$work/bin/op-ok" \
  JUDGE_TYPESAFE='sentinel-env-only' JUDGE_JEV='sentinel-env-only' \
  launch --detach "$JUDGE_ID"
check "op sentinel reaches the judge path" test "$rc" -eq 0
check "judge path records a hash" test "$(cat "$work/log/judge.sha")" = "$judge_sha"
check "judge path records that a key was present" test "$(cat "$work/log/judge.present")" = "yes"
check "judge hash is not the sentinel" test "$(cat "$work/log/judge.sha")" != "$judge_sentinel"
check "run.log exists" test -f "$JUDGE_DIR/run.log"
check "run.log does not contain the op sentinel" file_lacks "$JUDGE_DIR/run.log" "$judge_sentinel"
check "launcher stdout has no op sentinel" file_lacks "$work/out" "$judge_sentinel"
check "launcher stderr has no op sentinel" file_lacks "$work/err" "$judge_sentinel"
check "judge env exists" test -f "$work/log/judge.env"
check "judge env does not contain the op sentinel" file_lacks "$work/log/judge.env" "$judge_sentinel"
check "judge env has no judge key names" bash -c 'test -f "$1" && ! grep -E -q "^(TYPESAFE_API_KEY|JEV_API_KEY)=" "$1"' bash "$work/log/judge.env"
unset DETACH_CHILD JUDGE_REF JUDGE_OP JUDGE_TYPESAFE JUDGE_JEV
kill_stub_sessions

if [ -n "$real_tmux" ]; then
  REAL_ID="real-detach-$$"
  REAL_SESSION="conclave-$REAL_ID"
  REAL_DIR="$ROOT/tmp/council/$REAL_ID"
  mkdir -p "$REAL_DIR"
  printf 'kind: design\n\n# Brief\n' >"$REAL_DIR/brief.md"
  printf '%s\n' "$REAL_SESSION" >>"$work/real-sessions"
  STUB_REAL_TMUX="$real_tmux"
  TMUX_TMPDIR="$work/tmuxdir"
  FAKE_MODE=progress launch --detach "$REAL_ID"
  real_want="$(printf 'session: %s\ndir: %s\nattach: tmux -L %s attach -t %s\n' \
    "$REAL_SESSION" "$REAL_DIR" "$REAL_SESSION" "$REAL_SESSION")"
  check "real tmux (a) exits 0" test "$rc" -eq 0
  check "real tmux (a) stdout is the three lines" stdout_is "$real_want"
  check "real tmux (a) session is alive" session_alive "$REAL_SESSION"
  real_env="$(TMUX_TMPDIR="$work/tmuxdir" "$real_tmux" -L "$REAL_SESSION" show-environment -g || true)"
  check "real tmux server has no judge keys" bash -c '! printf "%s\n" "$1" | grep -E -q "^(TYPESAFE_API_KEY|JEV_API_KEY)="' bash "$real_env"
  TMUX_TMPDIR="$work/tmuxdir" "$real_tmux" -L "$REAL_SESSION" kill-server >/dev/null 2>&1 || true

  FAKE_MODE=die launch --detach "$REAL_ID"
  check "real tmux (b) exits 1" test "$rc" -eq 1
  check "real tmux (b) shows the child line" err_has "fake: brief rejected"
  check "real tmux (b) session is gone" not_alive "$REAL_SESSION"

  printf '2020-01-01T00:00:00+00:00 run started\n' >"$REAL_DIR/progress.md"
  FAKE_MODE=silent CONCLAVE_DETACH_WAIT=1 launch --detach "$REAL_ID"
  check "real tmux (c) exits 3" test "$rc" -eq 3
  check "real tmux (c) says no progress line" err_has "wrote no progress line"
  check "real tmux (c) session stays up" session_alive "$REAL_SESSION"
  TMUX_TMPDIR="$work/tmuxdir" "$real_tmux" -L "$REAL_SESSION" kill-server >/dev/null 2>&1 || true

  JUDGE_REAL_ID="judge-real-$$"
  JUDGE_REAL_SESSION="conclave-$JUDGE_REAL_ID"
  JUDGE_REAL_DIR="$ROOT/tmp/council/$JUDGE_REAL_ID"
  mkdir -p "$JUDGE_REAL_DIR"
  printf 'kind: design\n\n# Brief\n' >"$JUDGE_REAL_DIR/brief.md"
  printf '%s\n' "$JUDGE_REAL_SESSION" >>"$work/real-sessions"
  rm -f "$work/log/judge.sha" "$work/log/judge.present" "$work/log/judge.env" "$JUDGE_REAL_DIR/run.log"
  DETACH_CHILD="$work/bin/judge-probe" \
    JUDGE_REF='op://conclave/test/judge' JUDGE_OP="$work/bin/op-ok" \
    JUDGE_TYPESAFE='sentinel-env-only' JUDGE_JEV='sentinel-env-only' \
    launch --detach "$JUDGE_REAL_ID"
  check "real tmux op sentinel reaches the judge path" test "$rc" -eq 0
  check "real tmux judge path records a hash" test "$(cat "$work/log/judge.sha")" = "$judge_sha"
  check "real tmux run.log exists" test -f "$JUDGE_REAL_DIR/run.log"
  check "real tmux run.log does not contain the op sentinel" file_lacks "$JUDGE_REAL_DIR/run.log" "$judge_sentinel"
  check "real tmux launcher stdout has no op sentinel" file_lacks "$work/out" "$judge_sentinel"
  check "real tmux launcher stderr has no op sentinel" file_lacks "$work/err" "$judge_sentinel"
  judge_env="$(TMUX_TMPDIR="$work/tmuxdir" "$real_tmux" -L "$JUDGE_REAL_SESSION" show-environment -g || true)"
  check "real tmux server env has no op sentinel" bash -c '! printf "%s\n" "$1" | grep -F -q -- "$2"' bash "$judge_env" "$judge_sentinel"
  check "real tmux server env has no judge keys" bash -c '! printf "%s\n" "$1" | grep -E -q "^(TYPESAFE_API_KEY|JEV_API_KEY)="' bash "$judge_env"
  TMUX_TMPDIR="$work/tmuxdir" "$real_tmux" -L "$JUDGE_REAL_SESSION" kill-server >/dev/null 2>&1 || true
  unset DETACH_CHILD JUDGE_REF JUDGE_OP JUDGE_TYPESAFE JUDGE_JEV
  unset STUB_REAL_TMUX TMUX_TMPDIR
else
  printf 'skip real tmux\n'
fi

printf '%s passed, %s failed\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
