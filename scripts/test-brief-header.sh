#!/usr/bin/env bash
# A blank line does not end the brief header or drop the headers after it.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

run_brief() {
  local brief="$1"
  BRIEF="$(mktemp)"
  printf '%s' "$brief" >"$BRIEF"
  export COUNCIL_SOURCE_ONLY=1
  export BRIEF
  # shellcheck disable=SC1091
  . "$ROOT/scripts/council.sh"
  parse_brief_header
  printf 'HEADER_KIND=%s\n' "$HEADER_KIND"
  printf 'HEADER_CLAUDE=%s\n' "$HEADER_CLAUDE"
  printf 'HEADER_AUTHOR=%s\n' "$HEADER_AUTHOR"
  printf 'HEADER_PR=%s\n' "$HEADER_PR"
  printf '%s\n' '---BODY---'
  brief_body
  rm -f "$BRIEF"
}

out="$(run_brief "$(printf '%s\n' 'kind: review' '' 'claude: opus' 'author: Grok' 'pr: 12' '# Goal' 'body text' '')")"
printf '%s\n' "$out" | grep -qx 'HEADER_KIND=review' || fail "kind: $out"
printf '%s\n' "$out" | grep -qx 'HEADER_CLAUDE=opus' || fail "claude dropped: $out"
printf '%s\n' "$out" | grep -qx 'HEADER_AUTHOR=grok' || fail "author dropped: $out"
printf '%s\n' "$out" | grep -qx 'HEADER_PR=12' || fail "pr dropped: $out"
printf '%s\n' "$out" | grep -q 'author:' && fail "author stayed in the body: $out"
body="${out#*---BODY---$'\n'}"
[ "$body" = $'# Goal\nbody text' ] || fail "body [$body]"

# A non-header still ends the header. Later keys stay in the body.
out="$(run_brief "$(printf '%s\n' 'kind: review' '' 'not a header' 'claude: opus' '')")"
printf '%s\n' "$out" | grep -qx 'HEADER_CLAUDE=' || fail "body key applied: $out"
body="${out#*---BODY---$'\n'}"
printf '%s\n' "$body" | grep -qx 'claude: opus' || fail "body lost the later key: [$body]"
# brief_winning_kind sees a kind: line that follows a blank line.
kind="$(
  BRIEF="$(mktemp)"
  printf '%s\n' 'pr: 12' '' 'kind: audit' '# Goal' >"$BRIEF"
  export COUNCIL_SOURCE_ONLY=1 BRIEF
  unset FLAG_KIND
  # shellcheck disable=SC1091
  . "$ROOT/scripts/council.sh"
  brief_winning_kind
  rm -f "$BRIEF"
)"
[ "$kind" = audit ] || fail "winning kind after blank: [$kind]"
printf 'ok brief header\n'
