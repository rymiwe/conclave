#!/usr/bin/env bash
# CONCLAVE_REVIEWER_PREFS set vs unset (and missing file).
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/reviewer-prefs.sh
. "$ROOT/scripts/reviewer-prefs.sh"
fail=0
ok() { echo "ok $1"; }
bad() { echo "FAIL $1"; fail=1; }

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

# Unset → clear default
unset CONCLAVE_REVIEWER_PREFS || true
out=$(emit_reviewer_prefs)
case "$out" in
  *"None configured. Using the generic template defaults above."*) ok "unset prints default";;
  *) bad "unset default missing: $out";;
esac
case "$out" in
  *"Set CONCLAVE_REVIEWER_PREFS"*) ok "unset names the env var";;
  *) bad "unset missing env hint";;
esac
case "$out" in
  *"(from CONCLAVE_REVIEWER_PREFS)"*) bad "unset should not claim prefs loaded";;
  *) ok "unset does not claim prefs loaded";;
esac

# Set to a file outside the repo → contents appear
prefs="$T/house-rules.md"
printf '%s\n' "Prefer short findings." "Never invent file:line." > "$prefs"
export CONCLAVE_REVIEWER_PREFS="$prefs"
export ROOT
out=$(emit_reviewer_prefs)
case "$out" in
  *"(from CONCLAVE_REVIEWER_PREFS)"*) ok "set announces source";;
  *) bad "set missing source line: $out";;
esac
case "$out" in
  *"Prefer short findings."*) ok "set includes prefs body";;
  *) bad "set missing body";;
esac
case "$out" in
  *"None configured"*) bad "set should not print unset default";;
  *) ok "set omits unset default";;
esac

# Set but missing file → same as unset default
export CONCLAVE_REVIEWER_PREFS="$T/does-not-exist.md"
out=$(emit_reviewer_prefs)
case "$out" in
  *"None configured. Using the generic template defaults above."*) ok "missing file prints default";;
  *) bad "missing file default: $out";;
esac

# Refuse a prefs path inside the repo
export CONCLAVE_REVIEWER_PREFS="$ROOT/scripts/reviewer-output-template.md"
if out=$(emit_reviewer_prefs 2>"$T/err"); then
  bad "in-repo prefs path should fail"
else
  ok "in-repo prefs path refused"
fi

if [ "$fail" -ne 0 ]; then
  echo "test-reviewer-prefs FAILED"
  exit 1
fi
echo "test-reviewer-prefs OK"
