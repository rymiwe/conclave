#!/usr/bin/env bash
# An empty CURSOR_GROK must fail loud in every cursor-agent Grok voter leg:
# nonzero return, a message naming CURSOR_GROK, and cursor-agent never run.
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
mkdir -p "$WORK/wt" "$WORK/packet"
cat >"$WORK/cursor-agent" <<'SH'
#!/bin/sh
echo "$@" >>"$LAUNCH_LOG"
SH
chmod +x "$WORK/cursor-agent"
export CONCLAVE_CURSOR_AGENT="$WORK/cursor-agent" LAUNCH_LOG="$WORK/launches"
: >"$LAUNCH_LOG"
printf 'prompt\n' >"$WORK/packet/PROMPT.md"

# leg STATE FUNCTION [ARG] runs one leg with CURSOR_GROK unset or empty, then
# checks the return code, the message, and that nothing launched.
leg() {
  local state="$1" fn="$2" arg="${3:-}" dir="$WORK/out-$2-$1" msg
  mkdir -p "$dir"
  printf 'brief\n' >"$dir/voter-grok.brief.md"
  : >"$LAUNCH_LOG"
  msg="$(
    DIR="$dir"
    ASK_OUT="$dir"
    PACKET="$WORK/packet"
    ERRORS_FILE=''
    DRY_RUN=0
    if [ "$state" = unset ]; then unset CURSOR_GROK; else CURSOR_GROK=''; fi
    "$fn" $arg 2>&1
    echo "rc=$?"
  )" || true
  case "$msg" in
    *"rc=0") fail "$fn ($state) returned 0: $msg" ;;
  esac
  { [ -f "$dir/voter-grok.fail" ] && cat "$dir/voter-grok.fail"; printf '%s\n' "$msg"; } |
    grep -q 'CURSOR_GROK is empty; refusing to launch' || fail "$fn ($state) did not say why: $msg"
  [ ! -s "$LAUNCH_LOG" ] || fail "$fn ($state) launched cursor-agent: $(cat "$LAUNCH_LOG")"
  printf 'ok %s (%s) refuses an empty CURSOR_GROK\n' "$fn" "$state"
}

for state in unset empty; do
  leg "$state" review_run_grok_cursor "$WORK/wt"
  leg "$state" audit_run_grok_cursor "$WORK/wt"
  leg "$state" run_grok_cursor
  leg "$state" ask_run_grok_cursor
done

printf 'test-grok-empty-model: ok\n'
