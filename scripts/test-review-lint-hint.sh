#!/usr/bin/env bash
# CONCLAVE_REVIEW_LINT in the next-step heredoc must print literally when unset,
# including under set -u (unquoted heredoc must not expand it).
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail=0
ok() { echo "ok $1"; }
bad() { echo "FAIL $1"; fail=1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
DIR="$work/out"
mkdir -p "$DIR/blind"
: >"$DIR/readonly.txt"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
ID=t
DIFFSHAPE=none
VOTERS=""
REVIEW_BLIND_FAILED=0
REVIEW_FAIL=0

unset CONCLAVE_REVIEW_LINT || true
set -u
set +e
out=$(review_print_result 2>"$work/err")
rc=$?
set +u
set -e

if [ "$rc" -ne 0 ]; then
  bad "review_print_result exit $rc under set -u with CONCLAVE_REVIEW_LINT unset: $(cat "$work/err")"
elif printf '%s\n' "$out" | grep -Fq '$CONCLAVE_REVIEW_LINT'; then
  ok "unset CONCLAVE_REVIEW_LINT prints literally under set -u"
else
  bad "expected literal \$CONCLAVE_REVIEW_LINT in output; err=$(cat "$work/err"); out=$(printf '%s\n' "$out" | rg -n 'CONCLAVE_REVIEW|Next step' || true)"
fi

if [ "$fail" -ne 0 ]; then
  echo "test-review-lint-hint FAILED"
  exit 1
fi
echo "test-review-lint-hint OK"
