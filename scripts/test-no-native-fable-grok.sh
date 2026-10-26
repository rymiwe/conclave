#!/usr/bin/env bash
# Fable never calls the claude CLI. The grok CLI runs only with
# CONCLAVE_GROK_NATIVE=1. Stubs record argv. No live model call.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }
pass() { printf 'ok %s\n' "$1"; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/packet" "$work/dir" "$work/wt"
export PATH="$work/bin:$PATH"
export DIR="$work/dir"
export PACKET="$work/packet"
export ERRORS_FILE="$work/dir/errors.md"
export CURSOR_FABLE="claude-fable-9-9-thinking-xhigh"
export CURSOR_GROK="grok-9.9-xhigh"
export GROK_NATIVE_MODEL="grok-native-test"
export GROK_EFFORT="low"
export CLAUDE_EFFORT="high"
: >"$ERRORS_FILE"
printf 'prompt\n' >"$work/packet/PROMPT.md"
printf 'brief\n' >"$work/dir/voter-claude.brief.md"
printf 'audit grok brief\n' >"$work/dir/voter-grok.brief.md"
git -C "$work/wt" init -q

cat >"$work/bin/claude" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" >>"$ARGV_DIR/claude.argv"
printf '%s\n' '---' >>"$ARGV_DIR/claude.argv"
if printf '%s\n' "$@" | grep -qx json; then
  printf '%s\n' '{"result":"opus review","modelUsage":{"claude-opus":{"inputTokens":1}}}'
  exit 0
fi
printf '%s\n' '## Findings' 'opus' '## Approach' 'a' '## Would not do' 'b' '## Uncertainties' 'c'
exit 0
EOF
cat >"$work/bin/grok" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" >>"$ARGV_DIR/grok.argv"
printf '%s\n' '---' >>"$ARGV_DIR/grok.argv"
if [ "${1:-}" = models ]; then
  echo "Default model: grok-native-test"
  exit 0
fi
if printf '%s\n' "$@" | grep -qx -- --prompt-file; then
  printf '%s\n' '## Findings' 'native' '## Approach' 'a' '## Would not do' 'b' '## Uncertainties' 'c'
  exit 0
fi
out="${TMPDIR:-/tmp}/grok-$(id -un)"
mkdir -p "$out"
printf 'native report\n' >"$out/grok-review-1.md"
printf 'Review written to %s\n' "$out/grok-review-1.md"
exit 0
EOF
cat >"$work/bin/cursor-agent" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" >>"$ARGV_DIR/cursor.argv"
printf '%s\n' '---' >>"$ARGV_DIR/cursor.argv"
model=""
prev=""
for a in "$@"; do
  if [ "$prev" = --model ]; then model="$a"; fi
  prev="$a"
done
case "$model" in
  *fable*) display="Fable Thinking" ;;
  *grok*) display="Grok 4" ;;
  *) display="Other" ;;
esac
printf '%s\n' "{\"type\":\"system\",\"model\":\"$display\",\"session_id\":\"s\"}"
printf '%s\n' '{"type":"result","is_error":false,"result":"## Findings\nok\n## Approach\na\n## Would not do\nb\n## Uncertainties\nc\n"}'
EOF
chmod +x "$work/bin/"*

reset_argv() {
  rm -rf "$work/argv"
  mkdir -p "$work/argv"
  export ARGV_DIR="$work/argv"
  rm -f "$work/dir"/voter-*
  printf 'brief\n' >"$work/dir/voter-claude.brief.md"
  printf 'audit grok brief\n' >"$work/dir/voter-grok.brief.md"
}
called() { # $1 file, $2 needle
  grep -qxF -- "$2" "$work/argv/$1" 2>/dev/null
}
not_called() {
  [ ! -s "$work/argv/$1" ]
}

unset CONCLAVE_GROK_NATIVE
reset_argv
CLAUDE_SKU=fable
run_claude || fail "run_claude fable rc=$?"
called cursor.argv --mode || fail "packet fable missing --mode"
called cursor.argv ask || fail "packet fable missing ask"
called cursor.argv "$CURSOR_FABLE" || fail "packet fable model"
not_called claude.argv || fail "packet fable called claude: $(cat "$work/argv/claude.argv")"
pass "packet fable uses cursor-agent"

reset_argv
CLAUDE_SKU=opus
CLAUDE_MODEL_ALIAS=opus
run_claude || fail "run_claude opus rc=$?"
called claude.argv opus || fail "opus did not pass opus"
called claude.argv --model || fail "opus missing --model"
if called claude.argv fable; then fail "opus argv contains fable"; fi
not_called cursor.argv || fail "opus called cursor-agent"
pass "packet opus uses claude"

reset_argv
CLAUDE_SKU=fable
review_run_claude "$work/wt" || fail "review fable rc=$?"
called cursor.argv "$CURSOR_FABLE" || fail "review fable model"
if called cursor.argv ask; then fail "review fable used --mode ask"; fi
not_called claude.argv || fail "review fable called claude"
pass "review fable uses cursor-agent"

reset_argv
CLAUDE_SKU=fable
FABLE_OVERFLOW=cursor
review_run_claude "$work/wt" || fail "review overflow rc=$?"
not_called claude.argv || fail "fable overflow called claude"
called cursor.argv "$CURSOR_FABLE" || fail "overflow did not call cursor-agent"
pass "fable overflow does not call claude"

reset_argv
CLAUDE_SKU=opus
review_run_claude "$work/wt" || fail "review opus rc=$?"
called claude.argv opus || fail "review opus model"
if called claude.argv fable; then fail "review opus argv contains fable"; fi
pass "review opus uses claude"

reset_argv
unset CONCLAVE_GROK_NATIVE
run_grok || fail "run_grok cursor rc=$?"
called cursor.argv ask || fail "packet grok missing ask"
called cursor.argv "$CURSOR_GROK" || fail "packet grok model"
not_called grok.argv || fail "packet grok called grok CLI"
pass "packet grok uses cursor-agent"

reset_argv
CONCLAVE_GROK_NATIVE=1
run_grok || fail "run_grok native rc=$?"
called grok.argv --model || fail "native grok missing --model"
called grok.argv "$GROK_NATIVE_MODEL" || fail "native grok model"
called grok.argv --reasoning-effort || fail "native grok missing effort"
not_called cursor.argv || fail "native grok called cursor-agent"
pass "CONCLAVE_GROK_NATIVE=1 uses the grok CLI"

reset_argv
unset CONCLAVE_GROK_NATIVE
review_run_grok "$work/wt" "$work/dir" || fail "review grok cursor rc=$?"
called cursor.argv "$CURSOR_GROK" || fail "review grok model"
not_called grok.argv || fail "review grok called grok CLI"
[ -s "$work/dir/voter-grok-report.md" ] || fail "review grok wrote no report"
pass "review grok uses cursor-agent"

reset_argv
CONCLAVE_GROK_NATIVE=1
review_run_grok "$work/wt" "$work/dir" || fail "review grok native rc=$?"
called grok.argv --cwd || fail "native review grok missing --cwd"
not_called cursor.argv || fail "native review grok called cursor-agent"
pass "review grok native opt-in"

reset_argv
unset CONCLAVE_GROK_NATIVE
CLAUDE_SKU=fable
audit_run_claude_brief "$work/wt" claude fable || fail "audit fable rc=$?"
called cursor.argv "$CURSOR_FABLE" || fail "audit fable model"
not_called claude.argv || fail "audit fable called claude"
pass "audit fable uses cursor-agent"

reset_argv
audit_run_grok "$work/wt" "$work/dir" || fail "audit grok rc=$?"
called cursor.argv "$CURSOR_GROK" || fail "audit grok model"
if ! grep -q 'audit grok brief' "$work/argv/cursor.argv"; then
  fail "audit grok did not receive the brief"
fi
not_called grok.argv || fail "audit grok called grok CLI"
pass "audit grok uses cursor-agent"

if grep -nE -- '--model fable|claude -p --model fable' \
  "$ROOT/scripts/conclave.sh" "$ROOT/scripts/review-kind.sh" \
  "$ROOT/scripts/audit-kind.sh" "$ROOT/scripts/ask-mode.sh"; then
  fail "a script still names claude --model fable"
fi
pass "no script names claude --model fable"

printf 'ok test-no-native-fable-grok\n'
