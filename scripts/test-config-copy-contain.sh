#!/usr/bin/env bash
# Drive review_copy_ignored_config against a reviewed commit whose committed
# symlinks point out of the leg worktree.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

status=0
fail() {
  printf 'FAIL %s\n' "$*"
  status=1
}

# $1 work dir, $2 shell snippet run in the repo to commit the hostile layout.
leg_with() {
  local work="$1" layout="$2"
  mkdir -p "$work/outside"
  git init -q -b master "$work/repo"
  printf 'config/database.yml\n.env\n' >"$work/repo/.gitignore"
  git -C "$work/repo" add .gitignore
  git -C "$work/repo" -c user.email=t@example.com -c user.name=t commit -q -m base
  git -C "$work/repo" checkout -q -b pr
  (cd "$work/repo" && eval "$layout")
  git -C "$work/repo" add -f -A
  git -C "$work/repo" -c user.email=t@example.com -c user.name=t commit -q -m hostile
  git -C "$work/repo" worktree add --detach -q "$work/leg" pr
  git -C "$work/repo" checkout -q master
  mkdir -p "$work/repo/config"
  printf 'secret-db\n' >"$work/repo/config/database.yml"
  printf 'SECRET=1\n' >"$work/repo/.env"
  REPO="$work/repo"
  unset ERRORS_FILE
}

# $1 name, $2 layout, $3 file expected in the note.
check() {
  local name="$1" layout="$2" file="$3" work err leaked
  work="$(mktemp -d)"
  leg_with "$work" "$layout"
  err="$work/stderr"
  review_copy_ignored_config "$work/leg" 2>"$err" || true
  leaked="$(find "$work/outside" -mindepth 1 | sort)"
  if [ -z "$leaked" ]; then
    printf 'ok %s: nothing written outside the leg\n' "$name"
  else
    fail "$name: written outside the leg: $(printf '%s' "$leaked" | tr '\n' ' ')"
  fi
  if [ "$(grep -c "not copied" "$err")" -eq 1 ] && grep -q -- "$file" "$err"; then
    printf 'ok %s: one note names %s\n' "$name" "$file"
  else
    fail "$name: expected one note naming $file, stderr was: $(tr '\n' ' ' <"$err")"
  fi
  rm -rf "$work"
}

check "config dir symlink" 'ln -s ../outside config' config/database.yml
check "config symlink to missing dir" 'ln -s ../outside/made/here config' config/database.yml
check "dangling database.yml symlink" 'mkdir config && ln -s ../../outside/db.yml config/database.yml' config/database.yml
check "dangling .env symlink" 'mkdir -p config && printf x >config/keep && ln -s ../outside/env .env' .env

work="$(mktemp -d)"
leg_with "$work" 'mkdir -p config && printf x >config/keep'
review_copy_ignored_config "$work/leg" 2>"$work/stderr"
if cmp -s "$work/repo/config/database.yml" "$work/leg/config/database.yml" && cmp -s "$work/repo/.env" "$work/leg/.env" && [ ! -s "$work/stderr" ]; then
  printf 'ok contained layout: both files copied, no note\n'
else
  fail "contained layout: files not copied or a note printed: $(tr '\n' ' ' <"$work/stderr")"
fi
rm -rf "$work"

exit "$status"
