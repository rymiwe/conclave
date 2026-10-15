#!/usr/bin/env bash
# A PR diffstat mismatch fetches once more and rebuilds, then stops if it still differs.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

work="$(mktemp -d)"
WT1="$work/wt1"
WT2="$work/wt2"
DIR="$work/out"
mkdir -p "$WT1" "$WT2" "$DIR"
count="$DIR/fetches"
: >"$count"
STILL=0

git() {
  if [ "${1:-}" = "-C" ] && [ "${3:-}" = "diff" ]; then
    if [ "$STILL" -eq 1 ] || [ "$2" = "$WT1" ]; then
      printf ' 1 file changed, 1 insertion(+), 1 deletion(-)\n'
    else
      printf ' 1 file changed, 5 insertions(+), 2 deletions(-)\n'
    fi
    return 0
  fi
  command git "$@"
}

review_fetch_pr() { printf 'x\n' >>"$count"; }
review_drop_worktrees() { REVIEW_WORKTREES=""; }
review_build_worktrees() { REVIEW_WORKTREES="$WT2"; }

REVIEW_MODE=pr
PR_ADD=5
PR_DEL=2
PR_BASE=main
REVIEW_DIFFSTAT_RETRIED=0
REVIEW_WORKTREES="$WT1"

review_check_diffstat
fetches="$(wc -l <"$count" | tr -d ' ')"
[ "$fetches" -eq 1 ] || fail "mismatch fetched $fetches times, want 1"
grep -q '+5/-2' "$DIR/diffshape.txt" || fail "shape after rebuild: $(cat "$DIR/diffshape.txt")"

# Already matching: no fetch.
: >"$count"
rm -f "$DIR/diffshape.txt"
REVIEW_DIFFSTAT_RETRIED=0
REVIEW_WORKTREES="$WT2"
review_check_diffstat
fetches="$(wc -l <"$count" | tr -d ' ')"
[ "$fetches" -eq 0 ] || fail "matching diffstat fetched $fetches times"

# Still different after the rebuild: one fetch, then die.
: >"$count"
STILL=1
REVIEW_DIFFSTAT_RETRIED=0
REVIEW_WORKTREES="$WT1"
set +e
err="$(review_check_diffstat 2>&1)"
ec=$?
set -e
[ "$ec" -ne 0 ] || fail "still-different diffstat exited 0: $err"
fetches="$(wc -l <"$count" | tr -d ' ')"
[ "$fetches" -eq 1 ] || fail "still-different fetched $fetches times, want 1: $err"
printf '%s\n' "$err" | grep -q 'after refetch' || fail "still-different message: $err"
rm -rf "$work"
printf 'ok diffstat retry\n'
