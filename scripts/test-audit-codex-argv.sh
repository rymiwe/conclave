#!/usr/bin/env bash
# A long audit brief must not be one argv string. Codex reads `-` from stdin.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

work="$(mktemp -d)"
CODEX="$work/codex"
cat >"$CODEX" <<'EOF'
#!/bin/bash
printf '%s\n' "${!#}" >"$CODEX_LOG/last"
wc -c >"$CODEX_LOG/stdin"
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then
    printf 'report\n' >"$a"
  fi
  prev="$a"
done
exit 0
EOF
chmod +x "$CODEX"
CODEX_REVIEW_MODEL=test-model
export CODEX CODEX_LOG CODEX_REVIEW_MODEL

run_leg() {
  local bytes="$1"
  local dir="$work/b$bytes"
  mkdir -p "$dir/wt" "$dir/log"
  CODEX_LOG="$dir/log"
  DIR="$dir"
  : >"$dir/.run-stamp"
  head -c "$bytes" /dev/zero | tr '\0' a >"$dir/voter-codex.brief.md"
  rm -f "$dir/voter-codex.fail" "$dir/voter-codex.md"
  set +e
  audit_run_codex "$dir/wt"
  ec=$?
  set -e
  printf '%s\n' "$ec"
}

ec="$(run_leg 140000)"
[ "$ec" -eq 0 ] || fail "large brief exit $ec: $(cat "$work/b140000/voter-codex.fail" 2>/dev/null || true)"
[ ! -e "$work/b140000/voter-codex.fail" ] || fail "large brief wrote a fail file"
[ "$(cat "$work/b140000/log/last")" = "-" ] || fail "large brief argv was [$(cat "$work/b140000/log/last")]"
stdin_bytes="$(tr -d ' ' <"$work/b140000/log/stdin")"
[ "$stdin_bytes" -eq 140000 ] || fail "large brief stdin was $stdin_bytes bytes"
[ -s "$work/b140000/voter-codex.md" ] || fail "large brief wrote no report"

ec="$(run_leg 80)"
[ "$ec" -eq 0 ] || fail "short brief exit $ec: $(cat "$work/b80/voter-codex.fail" 2>/dev/null || true)"
[ "$(cat "$work/b80/log/last")" = "$(cat "$work/b80/voter-codex.brief.md")" ] \
  || fail "short brief was not the argv prompt"
stdin_bytes="$(tr -d ' ' <"$work/b80/log/stdin")"
[ "$stdin_bytes" -eq 0 ] || fail "short brief stdin was $stdin_bytes bytes"
rm -rf "$work"
printf 'ok audit codex argv\n'
