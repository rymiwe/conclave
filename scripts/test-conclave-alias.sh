#!/usr/bin/env bash
# Alias and setting precedence.
# (a) council.sh and conclave.sh accept the same arguments.
# (b) only COUNCIL_* matches the old behavior.
# (c) only CONCLAVE_* works.
# (d) when both are set, CONCLAVE_* wins, including an empty value.
set -u
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
fail=0
check() {
  if [ "$2" = "$3" ]; then
    echo "ok $1"
  else
    echo "FAIL $1 (want '$3', got '$2')"
    fail=1
  fi
}
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

# shellcheck disable=SC1091
. "$ROOT/scripts/freshness-check.sh"

git init -q --bare -b main "$T/o.git"
git clone -q "$T/o.git" "$T/repo" 2>/dev/null
git -C "$T/repo" checkout -q -b main 2>/dev/null
echo a >"$T/repo/f"
git -C "$T/repo" add f && git -C "$T/repo" commit -qm one
git -C "$T/repo" remote set-url origin "$T/nonexistent.git"

# Subshells keep the sourced function. Assignments here do not leak.
fresh() { # remaining args are NAME=value pairs
  (
    unset CONCLAVE_SKIP_UPDATE_CHECK COUNCIL_SKIP_UPDATE_CHECK
    unset CONCLAVE_REQUIRE_FRESH COUNCIL_REQUIRE_FRESH
    export "$@"
    council_freshness_check "$T/repo" 2>&1
  )
}

# (b) only the old skip name is silent.
out="$(fresh COUNCIL_SKIP_UPDATE_CHECK=1)"
rc=$?
check "only COUNCIL_SKIP_UPDATE_CHECK skips" "$rc:$out" "0:"

# (c) only the new skip name is silent.
out="$(fresh CONCLAVE_SKIP_UPDATE_CHECK=1)"
rc=$?
check "only CONCLAVE_SKIP_UPDATE_CHECK skips" "$rc:$out" "0:"

# (d) CONCLAVE_=0 wins over COUNCIL_=1, so the check runs and the fetch fails open.
out="$(fresh CONCLAVE_SKIP_UPDATE_CHECK=0 COUNCIL_SKIP_UPDATE_CHECK=1)"
rc=$?
check "CONCLAVE_SKIP_UPDATE_CHECK=0 wins: rc 0" "$rc" 0
case "$out" in
  *WARNING*) echo "ok CONCLAVE_SKIP_UPDATE_CHECK=0 wins over COUNCIL_SKIP_UPDATE_CHECK=1" ;;
  *) echo "FAIL CONCLAVE skip 0 should warn, got: $out"; fail=1 ;;
esac

# (d) the new skip name wins over an explicit old 0.
out="$(fresh CONCLAVE_SKIP_UPDATE_CHECK=1 COUNCIL_SKIP_UPDATE_CHECK=0)"
rc=$?
check "CONCLAVE_SKIP_UPDATE_CHECK=1 wins over COUNCIL_SKIP_UPDATE_CHECK=0" "$rc:$out" "0:"

# (d) an empty new name is set, so it wins over the old skip.
out="$(fresh CONCLAVE_SKIP_UPDATE_CHECK= COUNCIL_SKIP_UPDATE_CHECK=1)"
rc=$?
check "empty CONCLAVE_SKIP_UPDATE_CHECK wins: rc 0" "$rc" 0
case "$out" in
  *WARNING*) echo "ok empty CONCLAVE_SKIP_UPDATE_CHECK wins over COUNCIL_SKIP_UPDATE_CHECK=1" ;;
  *) echo "FAIL empty CONCLAVE skip should warn, got: $out"; fail=1 ;;
esac

# (b) only the old require-fresh name stops, and the message names that variable.
out="$(fresh COUNCIL_REQUIRE_FRESH=1)"
rc=$?
check "only COUNCIL_REQUIRE_FRESH stops" "$rc" 1
case "$out" in
  *"COUNCIL_REQUIRE_FRESH=1; stopping"*) echo "ok require-fresh message names COUNCIL_REQUIRE_FRESH" ;;
  *) echo "FAIL require-fresh message: $out"; fail=1 ;;
esac

# (c) only the new require-fresh name stops, and the message names that variable.
out="$(fresh CONCLAVE_REQUIRE_FRESH=1)"
rc=$?
check "only CONCLAVE_REQUIRE_FRESH stops" "$rc" 1
case "$out" in
  *"CONCLAVE_REQUIRE_FRESH=1; stopping"*) echo "ok require-fresh message names CONCLAVE_REQUIRE_FRESH" ;;
  *) echo "FAIL require-fresh message: $out"; fail=1 ;;
esac

# (d) the new require-fresh name wins over an old 0.
out="$(fresh CONCLAVE_REQUIRE_FRESH=1 COUNCIL_REQUIRE_FRESH=0)"
rc=$?
check "CONCLAVE_REQUIRE_FRESH wins over COUNCIL_REQUIRE_FRESH=0" "$rc" 1
case "$out" in
  *"CONCLAVE_REQUIRE_FRESH=1; stopping"*) echo "ok winning require-fresh name is CONCLAVE_REQUIRE_FRESH" ;;
  *) echo "FAIL winning require-fresh message: $out"; fail=1 ;;
esac

# Codex binary override. A cache with one Astra slug, and two stub binaries.
mkdir -p "$T/home/.codex"
printf '%s\n' '{"models":[{"slug":"gpt-6-astra","visibility":"list","priority":1}]}' >"$T/home/.codex/models_cache.json"
cat >"$T/codex-old" <<'EOF'
#!/bin/sh
echo "codex-cli 0.159.3"
EOF
cat >"$T/codex-new" <<'EOF'
#!/bin/sh
echo "codex-cli 0.160.0"
EOF
chmod +x "$T/codex-old" "$T/codex-new"
codex_line() {
  printf '%s\n' "$1" | sed -n 's/^CODEX=//p'
}
old_q="$(printf '%q' "$T/codex-old")"
new_q="$(printf '%q' "$T/codex-new")"

out="$(env -u CONCLAVE_CODEX_BIN HOME="$T/home" COUNCIL_CODEX_BIN="$T/codex-old" bash "$ROOT/scripts/resolve-models.sh" 2>"$T/resolve.err")"
rc=$?
check "only COUNCIL_CODEX_BIN resolves" "$rc" 0
check "only COUNCIL_CODEX_BIN is the old stub" "$(codex_line "$out")" "$old_q"

out="$(env -u COUNCIL_CODEX_BIN HOME="$T/home" CONCLAVE_CODEX_BIN="$T/codex-new" bash "$ROOT/scripts/resolve-models.sh" 2>"$T/resolve.err")"
rc=$?
check "only CONCLAVE_CODEX_BIN resolves" "$rc" 0
check "only CONCLAVE_CODEX_BIN is the new stub" "$(codex_line "$out")" "$new_q"

out="$(env HOME="$T/home" CONCLAVE_CODEX_BIN="$T/codex-new" COUNCIL_CODEX_BIN="$T/codex-old" bash "$ROOT/scripts/resolve-models.sh" 2>"$T/resolve.err")"
rc=$?
check "both codex bins resolve" "$rc" 0
check "CONCLAVE_CODEX_BIN wins over COUNCIL_CODEX_BIN" "$(codex_line "$out")" "$new_q"

# Toolchain bin override, through the council.sh alias (sourced).
mkdir -p "$T/wt" "$T/bin"
cat >"$T/bin/mise-old" <<EOF
#!/usr/bin/env bash
[ "\$1" = bin-paths ] || exit 2
printf '/old/bin\n'
EOF
cat >"$T/bin/mise-new" <<EOF
#!/usr/bin/env bash
[ "\$1" = bin-paths ] || exit 2
printf '/new/bin\n'
EOF
chmod +x "$T/bin/mise-old" "$T/bin/mise-new"
run_mise() {
  # Assignments in "$@" are applied after the unsets, so only those names are set.
  env -u CONCLAVE_MISE -u COUNCIL_MISE "$@" COUNCIL_SOURCE_ONLY=1 bash -c \
    '. "$1"; review_toolchain_bins "$2"' bash "$ROOT/scripts/council.sh" "$T/wt"
}
got="$(run_mise COUNCIL_MISE="$T/bin/mise-old")"
check "only COUNCIL_MISE" "$got" "/old/bin"
got="$(run_mise CONCLAVE_MISE="$T/bin/mise-new")"
check "only CONCLAVE_MISE" "$got" "/new/bin"
got="$(run_mise CONCLAVE_MISE="$T/bin/mise-new" COUNCIL_MISE="$T/bin/mise-old")"
check "CONCLAVE_MISE wins over COUNCIL_MISE" "$got" "/new/bin"

# (a) help text is the same through both entry names.
help_new="$(bash "$ROOT/scripts/conclave.sh" help)"
help_old="$(bash "$ROOT/scripts/council.sh" help)"
check "council.sh help matches conclave.sh help" "$help_old" "$help_new"
case "$help_new" in
  *"Usage: scripts/conclave.sh"*) echo "ok help names conclave.sh" ;;
  *) echo "FAIL help does not name conclave.sh"; fail=1 ;;
esac
case "$help_new" in
  *"deprecated alias"*) echo "ok help names the council.sh alias" ;;
  *) echo "FAIL help does not name the alias"; fail=1 ;;
esac

# Sourced alias exports both voter names.
got="$(env -u COUNCIL_VOTER -u CONCLAVE_VOTER COUNCIL_SOURCE_ONLY=1 bash -c \
  ". '$ROOT/scripts/council.sh' >/dev/null 2>&1; printf '%s %s' \"\${COUNCIL_VOTER:-unset}\" \"\${CONCLAVE_VOTER:-unset}\"")"
check "sourced council.sh exports both voter names" "$got" "1 1"

# (a) dry-run through both names, same arguments, same stdout and preview.
id="alias-dry"
ballot="$ROOT/tmp/council/$id"
rm -rf "$ballot"
mkdir -p "$ballot"
cat >"$ballot/brief.md" <<'EOF'
kind: design
difficulty: low

# Conclave brief: alias dry run

## Goal
Preview routing through both entry names.

## Decision needed
None. This is a dry run.
EOF
git init -q -b main "$T/target"
echo t >"$T/target/f"
git -C "$T/target" add f && git -C "$T/target" commit -qm one

run_dry() {
  # $1 entry script. Skip the freshness fetch so this test cannot pull the clone.
  env -u CONCLAVE_SKIP_UPDATE_CHECK -u COUNCIL_SKIP_UPDATE_CHECK \
    -u CONCLAVE_CODEX_BIN -u COUNCIL_CODEX_BIN \
    -u CONCLAVE_SEAT_USAGE -u COUNCIL_SEAT_USAGE \
    HOME="$T/home" COUNCIL_SKIP_UPDATE_CHECK=1 COUNCIL_CODEX_BIN="$T/codex-old" \
    bash "$1" "$id" --dry-run --repo "$T/target"
}
out_new="$(cd "$T/target" && run_dry "$ROOT/scripts/conclave.sh" 2>"$T/dry-new.err")"
rc_new=$?
cp -a "$ballot/preview" "$T/preview-new"
rm -rf "$ballot/preview"
out_old="$(cd "$T/target" && run_dry "$ROOT/scripts/council.sh" 2>"$T/dry-old.err")"
rc_old=$?
check "conclave.sh dry-run exits 0" "$rc_new" 0
check "council.sh dry-run exits 0" "$rc_old" 0
check "dry-run stdout matches" "$out_old" "$out_new"
check "dry-run stderr matches" "$(cat "$T/dry-old.err")" "$(cat "$T/dry-new.err")"
check "dry-run routing.md matches" "$(cat "$ballot/preview/routing.md")" "$(cat "$T/preview-new/routing.md")"
case "$out_new" in
  *"Dry-run preview: tmp/council/$id/preview/"*) echo "ok dry-run names the preview directory" ;;
  *) echo "FAIL dry-run output: $out_new"; fail=1 ;;
esac

# Same dry-run with only CONCLAVE_* set, so the new names drive skip and the codex stub.
out_only="$(cd "$T/target" && env -u COUNCIL_SKIP_UPDATE_CHECK -u COUNCIL_CODEX_BIN \
  -u CONCLAVE_SKIP_UPDATE_CHECK -u CONCLAVE_CODEX_BIN \
  -u CONCLAVE_SEAT_USAGE -u COUNCIL_SEAT_USAGE \
  HOME="$T/home" CONCLAVE_SKIP_UPDATE_CHECK=1 CONCLAVE_CODEX_BIN="$T/codex-new" \
  bash "$ROOT/scripts/conclave.sh" "$id" --dry-run --repo "$T/target" 2>"$T/dry-only.err")"
rc_only=$?
check "dry-run with only CONCLAVE_* exits 0" "$rc_only" 0
# The codex model line differs because the stub version is not the model slug.
# Routing still names the same Astra slug from the cache. Compare kind and target.
case "$out_only" in
  *"kind=design difficulty=low"*) echo "ok CONCLAVE_* dry-run routes design/low" ;;
  *) echo "FAIL CONCLAVE_* dry-run output: $out_only"; fail=1 ;;
esac
case "$out_only" in
  *"$T/target"*) echo "ok CONCLAVE_* dry-run keeps the target" ;;
  *) echo "FAIL CONCLAVE_* dry-run target: $out_only"; fail=1 ;;
esac

rm -rf "$ballot"

[ "$fail" -eq 0 ] && echo ok
exit "$fail"
