#!/usr/bin/env bash
# review_toolchain_bins: the Codex leg's PATH gets the worktree's tool bin dirs.
# No network, no real mise or asdf: a stub mise answers by working directory.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() {
  printf 'FAIL %s\n' "$*" >&2
  exit 1
}

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/wt" "$work/other" "$work/home" "$work/bin"
HOME="$work/home"
unset ASDF_DATA_DIR

# Stub mise: prints two bin dirs only inside the worktree, and only when the
# worktree is trusted through MISE_TRUSTED_CONFIG_PATHS.
cat >"$work/bin/mise" <<EOF
#!/usr/bin/env bash
[ "\$1" = bin-paths ] || exit 2
[ "\$PWD" = "$work/wt" ] || exit 0
[ "\${MISE_TRUSTED_CONFIG_PATHS:-}" = "$work/wt" ] || { echo untrusted >&2; exit 1; }
printf '/m/ruby/bin\n\n/m/node/bin\n'
EOF
chmod +x "$work/bin/mise"

# 1. mise: both dirs, colon-joined, blank lines dropped.
got="$(COUNCIL_MISE="$work/bin/mise" review_toolchain_bins "$work/wt")"
[ "$got" = "/m/ruby/bin:/m/node/bin" ] || fail "mise bins: '$got'"

# 2. mise found on PATH, not just through COUNCIL_MISE.
got="$(PATH="$work/bin:$PATH" review_toolchain_bins "$work/wt")"
[ "$got" = "/m/ruby/bin:/m/node/bin" ] || fail "mise on PATH: '$got'"

# 3. mise prints nothing and no asdf: nothing.
got="$(COUNCIL_MISE="$work/bin/mise" review_toolchain_bins "$work/other")"
[ -z "$got" ] || fail "empty mise: '$got'"

# 4. asdf shims are the fallback when mise gives nothing.
mkdir -p "$work/home/.asdf/shims"
got="$(COUNCIL_MISE="$work/bin/mise" review_toolchain_bins "$work/other")"
[ "$got" = "$work/home/.asdf/shims" ] || fail "asdf fallback: '$got'"

# 5. A missing COUNCIL_MISE binary falls through to asdf, no error.
got="$(COUNCIL_MISE="$work/bin/nope" review_toolchain_bins "$work/wt")"
[ "$got" = "$work/home/.asdf/shims" ] || fail "missing mise: '$got'"

printf 'ok test-toolchain-path\n'
