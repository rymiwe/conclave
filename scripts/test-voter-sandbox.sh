#!/usr/bin/env bash
# CONCLAVE_VOTER_SANDBOX: read-only by default, test-db by opt-in, anything
# else dies. Drives the review Codex leg and the cursor-agent voter legs
# against stubs that record their argv.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() {
  printf 'FAIL %s\n' "$*" >&2
  exit 1
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/wt"
# Each stub writes one argv element per line to $ARGV_LOG.
cat >"$WORK/codex" <<'SH'
#!/bin/sh
for a in "$@"; do printf '%s\n' "$a"; done >"$ARGV_LOG"
printf 'report\n'
SH
cat >"$WORK/cursor-agent" <<'SH'
#!/bin/sh
for a in "$@"; do printf '%s\n' "$a"; done >"$ARGV_LOG"
SH
chmod +x "$WORK/codex" "$WORK/cursor-agent"

CODEX="$WORK/codex"
CODEX_EFFORT=high
CODEX_REVIEW_MODEL=test-model
REVIEW_MODE=whole
CURSOR_FABLE=test-fable
CURSOR_GROK=test-grok
export CONCLAVE_CURSOR_AGENT="$WORK/cursor-agent"
unset COUNCIL_VOTER_SANDBOX

# leg MODE FUNCTION -> argv in $WORK/<function>.<mode>.argv
leg() {
  local mode="$1" fn="$2" dir="$WORK/out-$2-$1"
  mkdir -p "$dir"
  printf 'brief\n' >"$dir/voter-claude.brief.md"
  printf 'brief\n' >"$dir/voter-grok.brief.md"
  ARGV_LOG="$WORK/$fn.$mode.argv"
  export ARGV_LOG
  : >"$ARGV_LOG"
  (
    DIR="$dir"
    ERRORS_FILE="$dir/errors.md"
    if [ "$mode" = unset ]; then
      unset CONCLAVE_VOTER_SANDBOX
    else
      export CONCLAVE_VOTER_SANDBOX="$mode"
    fi
    # The model proof fails on a stub. Only the argv matters here.
    "$fn" "$WORK/wt" >/dev/null 2>&1 || true
  )
  [ -s "$ARGV_LOG" ] || fail "$fn ($mode) did not launch"
}

has() { grep -qxF -- "$2" "$1"; }
mentions() { grep -qF -- "$2" "$1"; }

# Codex review leg, read-only by default and with an explicit read-only.
for mode in unset '' read-only; do
  leg "$mode" review_run_codex
  argv="$WORK/review_run_codex.$mode.argv"
  has "$argv" sandbox_mode=read-only || fail "codex ($mode) lacks read-only: $(cat "$argv")"
  has "$argv" sandbox_workspace_write.network_access=false \
    || fail "codex ($mode) lacks network_access=false: $(cat "$argv")"
  ! mentions "$argv" workspace-write || fail "codex ($mode) has workspace-write: $(cat "$argv")"
  ! mentions "$argv" network_access=true || fail "codex ($mode) has network: $(cat "$argv")"
  ! mentions "$argv" 'Database rule:' || fail "codex ($mode) has the test-db rule"
  has "$argv" review || fail "codex ($mode) is not codex review: $(cat "$argv")"
done

# Codex review leg, test-db opt-in.
leg test-db review_run_codex
argv="$WORK/review_run_codex.test-db.argv"
has "$argv" sandbox_mode=workspace-write || fail "codex test-db lacks workspace-write: $(cat "$argv")"
has "$argv" sandbox_workspace_write.network_access=true || fail "codex test-db lacks network: $(cat "$argv")"
! mentions "$argv" sandbox_mode=read-only || fail "codex test-db still read-only"
for want in 'only the test database (RAILS_ENV=test' 'roll back' \
  'Never start, stop, restart, or create a container or service' 'Do not run docker commands'; do
  grep -F -- 'developer_instructions=' "$argv" | grep -qF -- "$want" \
    || fail "codex test-db developer_instructions lack '$want': $(cat "$argv")"
done

# cursor-agent voter legs: --sandbox enabled by default, unchanged on test-db.
for fn in review_run_cursor_fable review_run_grok_cursor audit_run_grok_cursor; do
  leg unset "$fn"
  argv="$WORK/$fn.unset.argv"
  grep -A1 -x -- --sandbox "$argv" | grep -qx enabled || fail "$fn lacks --sandbox enabled: $(cat "$argv")"
  leg test-db "$fn"
  argv="$WORK/$fn.test-db.argv"
  ! has "$argv" --sandbox || fail "$fn test-db passed --sandbox: $(cat "$argv")"
  has "$argv" --trust || fail "$fn test-db lost --trust: $(cat "$argv")"
done

# The lineup line names the mode.
CONCLAVE_VOTER_SANDBOX='' voter_sandbox_line | grep -q '^voter sandbox: read-only' \
  || fail "empty mode line: $(voter_sandbox_line)"
CONCLAVE_VOTER_SANDBOX=test-db voter_sandbox_line | grep -q '^voter sandbox: test-db' \
  || fail "test-db mode line"

# A bad value dies with a clear message, under either name.
for name in CONCLAVE_VOTER_SANDBOX COUNCIL_VOTER_SANDBOX; do
  set +e
  msg="$( (unset CONCLAVE_VOTER_SANDBOX COUNCIL_VOTER_SANDBOX; export "$name=workspace-write"; voter_sandbox_check) 2>&1)"
  rc=$?
  set -e
  [ "$rc" -ne 0 ] || fail "$name=workspace-write did not die"
  case "$msg" in
    *"$name='workspace-write' is not a voter sandbox"*read-only*test-db*) ;;
    *) fail "$name bad value message: $msg" ;;
  esac
done
for ok in '' read-only test-db; do
  ( CONCLAVE_VOTER_SANDBOX="$ok" voter_sandbox_check ) || fail "'$ok' died"
done

# The launcher checks the value before kind dispatch, so no leg launches.
set +e
msg="$(cd "$WORK" && env -u COUNCIL_SOURCE_ONLY CONCLAVE_VOTER_SANDBOX=bogus \
  CONCLAVE_SKIP_UPDATE_CHECK=1 bash "$ROOT/scripts/conclave.sh" sandbox-test-no-brief 2>&1)"
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "conclave.sh accepted a bad sandbox"
case "$msg" in
  *"is not a voter sandbox"*) ;;
  *) fail "conclave.sh bad sandbox message: $msg" ;;
esac

printf 'test-voter-sandbox: ok\n'
