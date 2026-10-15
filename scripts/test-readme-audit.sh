#!/usr/bin/env bash
# The README is the kinds specification. Audit is shipped.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
readme="$ROOT/README.md"
fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

if grep -F -q 'Pending. Not on master.' "$readme"; then
  fail "README still says audit is pending"
fi
grep -F -q 'design`, `review`, `mixed`, or `audit`' "$readme" \
  || fail "kinds sentence omits audit"
grep -F -q 'kind: audit` is never inferred' "$readme" \
  || fail "README does not say audit is never inferred"
row="$(grep -E '^\| `audit` \|' "$readme" || true)"
[ -n "$row" ] || fail "no audit row in the kinds table"
printf '%s\n' "$row" | grep -F -q 'Pending' && fail "audit row still says pending"
printf '%s\n' "$row" | grep -F -q 'same brief' || fail "audit row omits the shared brief"
printf '%s\n' "$row" | grep -F -q -- '--findings' || fail "audit row omits --findings"
printf '%s\n' "$row" | grep -F -q -- '--verdicts' || fail "audit row omits --verdicts"
printf 'ok readme audit\n'
