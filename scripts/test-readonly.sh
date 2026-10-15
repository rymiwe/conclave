#!/usr/bin/env bash
# Drive review_compare_readonly without a ballot.
# The seam is COUNCIL_SOURCE_ONLY: council.sh returns after defining functions.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() {
  printf 'FAIL %s\n' "$*" >&2
  exit 1
}

new_repo() {
  local work="$1"
  mkdir -p "$work/out"
  git init -q -b master "$work/repo"
  git -C "$work/repo" -c user.email=t@example.com -c user.name=t commit \
    -q --allow-empty -m init
  printf 'one\n' >"$work/repo/file.txt"
  git -C "$work/repo" add file.txt
  git -C "$work/repo" -c user.email=t@example.com -c user.name=t commit -q -m file
  git -C "$work/repo" checkout -q -b run
  git -C "$work/repo" worktree add --detach "$work/leg" HEAD >/dev/null
  DIR="$work/out"
  ERRORS_FILE="$DIR/errors.md"
  : >"$ERRORS_FILE"
  REPO="$work/repo"
  REVIEW_WORKTREES="$work/leg"
  REVIEW_FAIL=0
  review_readonly_hash
  [ "$REVIEW_HASH_OK" -eq 1 ] || fail "baseline hash"
  REVIEW_RO_BEFORE="$REVIEW_HASH_VALUE"
  REVIEW_REFS_BEFORE="$REVIEW_REFS_VALUE"
  review_record_leg_setup
}

# Same as new_repo, then the review_make_leg sequence: detach at the PR head
# and soft-reset to the base, so the setup reflog holds both commits.
leg_after_reset() {
  local work="$1"
  mkdir -p "$work/out"
  git init -q -b master "$work/repo"
  git -C "$work/repo" -c user.email=t@example.com -c user.name=t commit \
    -q --allow-empty -m init
  printf 'one\n' >"$work/repo/file.txt"
  git -C "$work/repo" add file.txt
  git -C "$work/repo" -c user.email=t@example.com -c user.name=t commit -q -m base
  BASE="$(git -C "$work/repo" rev-parse HEAD)"
  printf 'two\n' >>"$work/repo/file.txt"
  git -C "$work/repo" add file.txt
  git -C "$work/repo" -c user.email=t@example.com -c user.name=t commit -q -m head
  PR_HEAD="$(git -C "$work/repo" rev-parse HEAD)"
  git -C "$work/repo" checkout -q -b run
  git -C "$work/repo" worktree add --detach "$work/leg" "$PR_HEAD" >/dev/null
  git -C "$work/leg" reset --soft "$BASE"
  DIR="$work/out"
  ERRORS_FILE="$DIR/errors.md"
  : >"$ERRORS_FILE"
  REPO="$work/repo"
  REVIEW_WORKTREES="$work/leg"
  REVIEW_FAIL=0
  review_readonly_hash
  [ "$REVIEW_HASH_OK" -eq 1 ] || fail "baseline hash"
  REVIEW_RO_BEFORE="$REVIEW_HASH_VALUE"
  REVIEW_REFS_BEFORE="$REVIEW_REFS_VALUE"
  review_record_leg_setup
}

# 1. Another session moves master. HEAD of this checkout stays put.
# Not a failure. The move is recorded.
work="$(mktemp -d)"
new_repo "$work"
old="$(git -C "$work/repo" rev-parse master)"
new="$(git -C "$work/repo" commit-tree "HEAD^{tree}" -p master -m 'other session')"
git -C "$work/repo" update-ref refs/heads/master "$new"
review_compare_readonly
[ "$REVIEW_FAIL" -eq 0 ] || fail "outside move was a failure: $(cat "$ERRORS_FILE")"
grep -q 'result: match (refs moved, not attributed to a leg)' "$DIR/readonly.txt" \
  || fail "outside result: $(cat "$DIR/readonly.txt")"
grep -q "ref moved, not attributed to a leg: refs/heads/master $old -> $new" "$DIR/readonly.txt" \
  || fail "outside line: $(cat "$DIR/readonly.txt")"
grep -q "note: ref moved, not attributed to a leg: refs/heads/master $old -> $new" "$ERRORS_FILE" \
  || fail "outside note: $(cat "$ERRORS_FILE")"
if grep -q 'read-only' "$ERRORS_FILE"; then
  fail "outside move reached errors.md: $(cat "$ERRORS_FILE")"
fi
printf 'ok outside ref move\n'
git -C "$work/repo" worktree remove --force "$work/leg" >/dev/null 2>&1 || true
rm -rf "$work"

# 2. The leg commits, and a branch points at that commit. Failure.
work="$(mktemp -d)"
new_repo "$work"
printf 'two\n' >>"$work/leg/file.txt"
git -C "$work/leg" add file.txt
git -C "$work/leg" -c user.email=t@example.com -c user.name=t commit -q -m 'leg commit'
tip="$(git -C "$work/leg" rev-parse HEAD)"
git -C "$work/repo" branch side "$tip"
review_compare_readonly
[ "$REVIEW_FAIL" -eq 1 ] || fail "leg ref move was not a failure"
grep -q 'read-only: leg moved ref refs/heads/side' "$ERRORS_FILE" \
  || fail "leg message: $(cat "$ERRORS_FILE")"
if grep -F -q "$work/leg" "$ERRORS_FILE" || grep -F -q "$work/leg" "$DIR/readonly.txt"; then
  fail "worktree path leaked"
fi
grep -q 'result: mismatch' "$DIR/readonly.txt" || fail "leg result: $(cat "$DIR/readonly.txt")"
printf 'ok leg moved ref\n'
git -C "$work/repo" worktree remove --force "$work/leg" >/dev/null 2>&1 || true
rm -rf "$work"

# 2t. The leg commits and tags that commit. Annotated and lightweight both fail.
work="$(mktemp -d)"
new_repo "$work"
printf 'two\n' >>"$work/leg/file.txt"
git -C "$work/leg" add file.txt
git -C "$work/leg" -c user.email=t@example.com -c user.name=t commit -q -m 'leg commit'
tip="$(git -C "$work/leg" rev-parse HEAD)"
git -C "$work/repo" -c user.email=t@example.com -c user.name=t -c tag.gpgsign=false \
  tag -a -m note ann "$tip"
git -C "$work/repo" tag light "$tip"
review_compare_readonly
[ "$REVIEW_FAIL" -eq 1 ] || fail "tag was not a failure: $(cat "$ERRORS_FILE")"
grep -q 'read-only: leg moved ref refs/tags/ann' "$ERRORS_FILE" \
  || fail "annotated tag: $(cat "$ERRORS_FILE")"
grep -q 'read-only: leg moved ref refs/tags/light' "$ERRORS_FILE" \
  || fail "lightweight tag: $(cat "$ERRORS_FILE")"
grep -q 'result: mismatch' "$DIR/readonly.txt" || fail "tag result: $(cat "$DIR/readonly.txt")"
printf 'ok annotated and lightweight tag\n'
git -C "$work/repo" worktree remove --force "$work/leg" >/dev/null 2>&1 || true
rm -rf "$work"

# 2c. The leg checks out master and back. A branch at master is not a failure.
work="$(mktemp -d)"
new_repo "$work"
git -C "$work/leg" checkout -q master
git -C "$work/leg" checkout -q --detach HEAD
master="$(git -C "$work/repo" rev-parse master)"
git -C "$work/repo" branch from-dev "$master"
review_compare_readonly
[ "$REVIEW_FAIL" -eq 0 ] || fail "checkout and back was a failure: $(cat "$ERRORS_FILE")"
grep -q 'result: match (refs moved, not attributed to a leg)' "$DIR/readonly.txt" \
  || fail "checkout and back result: $(cat "$DIR/readonly.txt")"
grep -q "ref moved, not attributed to a leg: refs/heads/from-dev none -> $master" "$DIR/readonly.txt" \
  || fail "checkout and back line: $(cat "$DIR/readonly.txt")"
grep -q "note: ref moved, not attributed to a leg: refs/heads/from-dev none -> $master" "$ERRORS_FILE" \
  || fail "checkout and back note: $(cat "$ERRORS_FILE")"
if grep -q 'read-only' "$ERRORS_FILE"; then
  fail "checkout and back reached errors.md: $(cat "$ERRORS_FILE")"
fi
printf 'ok checkout and back\n'
git -C "$work/repo" worktree remove --force "$work/leg" >/dev/null 2>&1 || true
rm -rf "$work"

# 2d. The leg checks out master and stays. A branch at master is not a failure.
work="$(mktemp -d)"
new_repo "$work"
git -C "$work/leg" checkout -q master
master="$(git -C "$work/repo" rev-parse master)"
git -C "$work/repo" branch from-dev "$master"
review_compare_readonly
[ "$REVIEW_FAIL" -eq 0 ] || fail "checkout stay was a failure: $(cat "$ERRORS_FILE")"
grep -q 'result: match (refs moved, not attributed to a leg)' "$DIR/readonly.txt" \
  || fail "checkout stay result: $(cat "$DIR/readonly.txt")"
grep -q "ref moved, not attributed to a leg: refs/heads/from-dev none -> $master" "$DIR/readonly.txt" \
  || fail "checkout stay line: $(cat "$DIR/readonly.txt")"
grep -q "note: ref moved, not attributed to a leg: refs/heads/from-dev none -> $master" "$ERRORS_FILE" \
  || fail "checkout stay note: $(cat "$ERRORS_FILE")"
if grep -q 'read-only' "$ERRORS_FILE"; then
  fail "checkout stay reached errors.md: $(cat "$ERRORS_FILE")"
fi
printf 'ok checkout stays\n'
git -C "$work/repo" worktree remove --force "$work/leg" >/dev/null 2>&1 || true
rm -rf "$work"

# 2a. Another process branches at the leg's base. That commit is already in
# the setup reflog. Not a failure.
work="$(mktemp -d)"
leg_after_reset "$work"
git -C "$work/repo" branch from-base "$BASE"
review_compare_readonly
[ "$REVIEW_FAIL" -eq 0 ] || fail "base branch was a failure: $(cat "$ERRORS_FILE")"
grep -q 'result: match (refs moved, not attributed to a leg)' "$DIR/readonly.txt" \
  || fail "base result: $(cat "$DIR/readonly.txt")"
grep -q "ref moved, not attributed to a leg: refs/heads/from-base none -> $BASE" "$DIR/readonly.txt" \
  || fail "base line: $(cat "$DIR/readonly.txt")"
grep -q "note: ref moved, not attributed to a leg: refs/heads/from-base none -> $BASE" "$ERRORS_FILE" \
  || fail "base note: $(cat "$ERRORS_FILE")"
if grep -q 'read-only' "$ERRORS_FILE"; then
  fail "base branch reached errors.md: $(cat "$ERRORS_FILE")"
fi
printf 'ok branch at base\n'
git -C "$work/repo" worktree remove --force "$work/leg" >/dev/null 2>&1 || true
rm -rf "$work"

# 2b. Another process branches at the recorded setup HEAD. Same outcome.
work="$(mktemp -d)"
leg_after_reset "$work"
setup_head="${REVIEW_LEG_SETUP_HEAD[$work/leg]}"
[ -n "$setup_head" ] || fail "setup HEAD was not recorded"
[ "$setup_head" = "$BASE" ] || fail "setup HEAD is not the base after reset --soft"
git -C "$work/repo" branch from-setup "$setup_head"
review_compare_readonly
[ "$REVIEW_FAIL" -eq 0 ] || fail "setup HEAD branch was a failure: $(cat "$ERRORS_FILE")"
grep -q 'result: match (refs moved, not attributed to a leg)' "$DIR/readonly.txt" \
  || fail "setup result: $(cat "$DIR/readonly.txt")"
grep -q "ref moved, not attributed to a leg: refs/heads/from-setup none -> $setup_head" "$DIR/readonly.txt" \
  || fail "setup line: $(cat "$DIR/readonly.txt")"
grep -q "note: ref moved, not attributed to a leg: refs/heads/from-setup none -> $setup_head" "$ERRORS_FILE" \
  || fail "setup note: $(cat "$ERRORS_FILE")"
if grep -q 'read-only' "$ERRORS_FILE"; then
  fail "setup HEAD branch reached errors.md: $(cat "$ERRORS_FILE")"
fi
printf 'ok branch at setup HEAD\n'
git -C "$work/repo" worktree remove --force "$work/leg" >/dev/null 2>&1 || true
rm -rf "$work"

# 3. A tracked file in $REPO changes. Failure.
work="$(mktemp -d)"
new_repo "$work"
printf 'edited\n' >>"$work/repo/file.txt"
review_compare_readonly
[ "$REVIEW_FAIL" -eq 1 ] || fail "checkout edit was not a failure"
grep -q 'read-only baseline mismatch' "$ERRORS_FILE" \
  || fail "baseline message: $(cat "$ERRORS_FILE")"
grep -q 'result: mismatch' "$DIR/readonly.txt" || fail "edit result: $(cat "$DIR/readonly.txt")"
printf 'ok checkout mismatch\n'
git -C "$work/repo" worktree remove --force "$work/leg" >/dev/null 2>&1 || true
rm -rf "$work"

# 4. Print functions. REVIEW_FAIL decides failure. Other errors.md lines are notes.
work="$(mktemp -d)"
DIR="$work/out"
mkdir -p "$DIR"
: >"$DIR/readonly.txt"
ERRORS_FILE="$DIR/errors.md"
ID=t
DIFFSHAPE=none
VOTERS=""
REVIEW_BLIND_FAILED=0
AUDIT_DRIFT=n/a
AUDIT_KEPT=none
AUDIT_TYPE=content
check_print() {
  local fn="$1" want="$2" ec
  set +e
  "$fn" >/dev/null 2>"$work/err.txt"
  ec=$?
  set -e
  [ "$ec" -eq "$want" ] || fail "$fn exit $ec, wanted $want: $(cat "$work/err.txt")"
}
REVIEW_FAIL=0
printf 'target_drift: refetch failed: timeout\n' >"$ERRORS_FILE"
check_print review_print_result 0
grep -q 'Phase 1 recorded notes. See tmp/council/t/errors.md' "$work/err.txt" \
  || fail "drift notes: $(cat "$work/err.txt")"
if grep -q 'Phase 1 recorded failures' "$work/err.txt"; then
  fail "drift was a failure: $(cat "$work/err.txt")"
fi
check_print audit_print_result 0
grep -q 'Phase 1 recorded notes. See tmp/council/t/errors.md' "$work/err.txt" \
  || fail "audit drift notes: $(cat "$work/err.txt")"
REVIEW_FAIL=0
printf 'note: ref moved, not attributed to a leg: refs/heads/side none -> abc\n' >"$ERRORS_FILE"
check_print review_print_result 0
grep -q 'Phase 1 recorded notes. See tmp/council/t/errors.md' "$work/err.txt" \
  || fail "note message: $(cat "$work/err.txt")"
if grep -q 'Phase 1 recorded failures' "$work/err.txt"; then
  fail "note was a failure: $(cat "$work/err.txt")"
fi
check_print audit_print_result 0
grep -q 'Phase 1 recorded notes. See tmp/council/t/errors.md' "$work/err.txt" \
  || fail "audit note message: $(cat "$work/err.txt")"
REVIEW_FAIL=1
printf 'read-only: leg moved ref refs/heads/side\n' >"$ERRORS_FILE"
check_print review_print_result 1
grep -q 'Phase 1 recorded failures. See tmp/council/t/errors.md' "$work/err.txt" \
  || fail "failure message: $(cat "$work/err.txt")"
if grep -q 'Phase 1 recorded notes' "$work/err.txt"; then
  fail "failure also printed notes: $(cat "$work/err.txt")"
fi
check_print audit_print_result 1
grep -q 'Phase 1 recorded failures. See tmp/council/t/errors.md' "$work/err.txt" \
  || fail "audit failure message: $(cat "$work/err.txt")"
printf 'ok print result\n'
rm -rf "$work"

printf 'ok\n'
