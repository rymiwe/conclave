#!/usr/bin/env bash
# The pace key is the seat line's first whole word. grok is not grokbot.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

SEAT_STATUS=ok
SEAT_TEXT="$(printf '%s\n' '  grokbot  | Pro+ | week 10%' '  grok     | SuperGrok active | week 3%')"
got="$(review_pace_cell grok)"
printf '%s\n' "$got" | grep -q 'SuperGrok' || fail "grok cell was [$got]"
printf '%s\n' "$got" | grep -q 'Pro+' && fail "grok cell used the grokbot line [$got]"

SEAT_TEXT="$(printf '%s\n' '  grokbot  | Pro+ | week 10%')"
got="$(review_pace_cell grok)"
[ "$got" = "pace unknown" ] || fail "grok with only grokbot was [$got]"

SEAT_TEXT="$(printf '%s\n' '  chatgpt  | Plus | week 1%' '  claude   | Max | week 2%')"
got="$(review_pace_cell claude)"
printf '%s\n' "$got" | grep -q 'Max' || fail "claude cell was [$got]"
printf '%s\n' "$got" | grep -q 'Plus' && fail "claude cell used chatgpt [$got]"

printf 'ok pace cell\n'
