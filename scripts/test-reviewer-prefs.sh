#!/usr/bin/env bash
# CONCLAVE_REVIEWER_PREFS set vs unset (and missing file); sourcing must not
# change the caller's shell options.
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
fail=0
ok() { echo "ok $1"; }
bad() { echo "FAIL $1"; fail=1; }

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

# Sourcing must leave the caller's $- and set -o unchanged.
# Use a clean bash (no -euo) so a leaked `set -euo` from the sourced file
# would show up as a difference.
if bash -c '
  before_dollar=$-
  before_o=$(set +o | LC_ALL=C sort)
  # shellcheck source=scripts/reviewer-prefs.sh
  . "'"$ROOT"'/scripts/reviewer-prefs.sh"
  after_dollar=$-
  after_o=$(set +o | LC_ALL=C sort)
  if [ "$before_dollar" != "$after_dollar" ]; then
    printf "dollar before=%s after=%s\n" "$before_dollar" "$after_dollar" >&2
    exit 1
  fi
  if [ "$before_o" != "$after_o" ]; then
    printf "set -o differed\n" >&2
    diff -u <(printf "%s\n" "$before_o") <(printf "%s\n" "$after_o") >&2 || true
    exit 1
  fi
'; then
  ok "sourcing leaves caller \$- and set -o unchanged"
else
  bad "sourcing changed caller shell options"
fi

# shellcheck source=scripts/reviewer-prefs.sh
. "$ROOT/scripts/reviewer-prefs.sh"

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

# Set but missing file → defaults on stdout + warning on stderr
export CONCLAVE_REVIEWER_PREFS="$T/does-not-exist.md"
out=$(emit_reviewer_prefs 2>"$T/missing.err")
case "$out" in
  *"None configured. Using the generic template defaults above."*) ok "missing file prints default";;
  *) bad "missing file default: $out";;
esac
want="CONCLAVE_REVIEWER_PREFS set but not found: $T/does-not-exist.md; using defaults"
err=$(cat "$T/missing.err")
case "$err" in
  *"$want"*) ok "missing file warns on stderr";;
  *) bad "missing file stderr want=$want got=$err";;
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
