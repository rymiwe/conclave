#!/usr/bin/env bash
# Whole-repo line estimate must sum every xargs wc batch, not only the last.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

added() {
  printf '%s\n' "$DIFFSHAPE" | sed -nE 's/.*, \+([0-9]+)\/-.*/\1/p'
}

work="$(mktemp -d)"
REPO="$work/many"
mkdir -p "$REPO"
git -C "$REPO" init -q -b master
i=1
while [ "$i" -le 3000 ]; do
  printf 'x\n' >"$REPO/file_with_a_deliberately_long_name_to_fill_argv_$i.txt"
  i=$((i + 1))
done
git -C "$REPO" add .
batches="$(cd "$REPO" && git ls-files -z | xargs -0 wc -l | grep -c ' total$' || true)"
[ "$batches" -ge 2 ] || fail "fixture did not split wc into batches ($batches)"
true_lines="$(cd "$REPO" && git ls-files -z | xargs -0 cat -- | wc -l | tr -d ' ')"
review_whole_estimate
got="$(added)"
[ "$got" = "$true_lines" ] || fail "estimate +$got, true +$true_lines, batches $batches, shape [$DIFFSHAPE]"

REPO="$work/one"
mkdir -p "$REPO"
git -C "$REPO" init -q -b master
printf 'a\nb\nc\nd\n' >"$REPO/only.txt"
git -C "$REPO" add only.txt
review_whole_estimate
got="$(added)"
[ "$got" = 4 ] || fail "one file estimate +$got, shape [$DIFFSHAPE]"
rm -rf "$work"
printf 'ok whole estimate\n'
