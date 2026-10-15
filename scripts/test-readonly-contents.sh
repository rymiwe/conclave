#!/usr/bin/env bash
# Ignored-file bytes, and untracked files outside the invoking directory, change the hash.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

work="$(mktemp -d)"
repo="$work/repo"
mkdir -p "$repo/app" "$repo/log" "$repo/tmp/cache" "$repo/node_modules/x"
printf 'a\n' >"$repo/log/development.log"
printf 'a\n' >"$repo/tmp/cache/c"
printf 'a\n' >"$repo/node_modules/x/i.js"
git -C "$repo" init -q -b master
printf '.env\ndangling\nlog/*.log\ntmp/\nnode_modules/\n' >"$repo/.gitignore"
printf 'x\n' >"$repo/app/a.txt"
printf 'SECRET=1\n' >"$repo/.env"
printf 'n\n' >"$repo/notes.txt"
ln -s missing "$repo/dangling"
git -C "$repo" add .gitignore app/a.txt
git -C "$repo" -c user.email=t@example.com -c user.name=t commit -q -m init

hash_now() {
  REPO="$1"
  review_readonly_hash
  [ "$REVIEW_HASH_OK" -eq 1 ] || fail "hash failed for $1: $REVIEW_HASH_REASON"
  printf '%s\n' "$REVIEW_HASH_VALUE"
}

before="$(hash_now "$repo")"
printf 'SECRET=2\n' >"$repo/.env"
after="$(hash_now "$repo")"
[ "$before" != "$after" ] || fail "rewritten .env left the hash unchanged"
printf 'SECRET=1\n' >"$repo/.env"

# A running app writes log/ and tmp/. That must not read as a leg's write.
before="$(hash_now "$repo")"
printf 'b\n' >>"$repo/log/development.log"
printf 'b\n' >>"$repo/tmp/cache/c"
printf 'b\n' >>"$repo/node_modules/x/i.js"
after="$(hash_now "$repo")"
[ "$before" = "$after" ] || fail "log/, tmp/, or node_modules/ churn changed the hash"

before="$(hash_now "$repo/app")"
printf 'changed\n' >>"$repo/notes.txt"
after="$(hash_now "$repo/app")"
[ "$before" != "$after" ] || fail "untracked file outside the subdirectory left the hash unchanged"
again="$(hash_now "$repo/app")"
[ "$after" = "$again" ] || fail "unchanged tree hashed differently"
rm -rf "$work"
printf 'ok readonly contents\n'
