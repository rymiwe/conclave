#!/usr/bin/env bash
# Print the newest model ID per Conclave role, and the Astra-capable Codex
# binary, as shell-quoted assignments.
# Usage: scripts/resolve-models.sh
#        eval "$(scripts/resolve-models.sh)"
# Reads local listings only; spends no model allowance.
# Roles: Anthropic voter (launch alias), OpenAI voter and its binary,
# opt-in Grok voter (CLI default), Cursor Fable fallback, the Cursor
# stuck-builder (xhigh), the builder (newest Cursor Grok at high, Fast),
# and the newest Muse Wonder full model. Plain Wonder is not this pin.
# The chair is not resolved here: the Bot launches the newest Grok at high,
# Fast variant (bot-standing-instruction.md appendix).
# Exits 1 when CODEX or CODEX_REVIEW_MODEL is empty.
# CONCLAVE_CODEX_BIN, when set, replaces Codex discovery (tests).
# COUNCIL_CODEX_BIN is the deprecated name and still works. The new name
# wins when both are set. A real run leaves both unset.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/home-env.sh"
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/conclave-env.sh"

newest() { # $1 = regex with one capture group for the version; reads IDs on stdin
  sed -nE "s/$1/\1 &/p" | awk '{v=$1; gsub(/-/,".",v); print v, $2}' |
    sort -V | tail -1 | cut -d' ' -f2
}
emit() { printf '%s=%q\n' "$1" "$2"; }

cursor_ids=$(cursor-agent models 2>/dev/null | sed -E 's/\x1b\[[0-9;]*m//g' |
  grep -oE '^[a-z0-9.-]+')
codex_cache=~/.codex/models_cache.json

# Anthropic: a launch alias that tracks the flagship, not an ID. The ID is
# proven from modelUsage on the run's JSON, not from a preflight prompt.
emit CLAUDE_REVIEW_MODEL fable
# OpenAI on Codex: the best-ranked (lowest priority) listed Astra slug. Astra
# family only, so Sol or an unranked model can never stand in.
codex_model=$(jq -r '[.models[] | select(.visibility=="list" and .priority!=null
  and (.slug|test("-astra$")))] | min_by(.priority) | .slug // empty' \
  "$codex_cache" 2>/dev/null)
emit CODEX_REVIEW_MODEL "$codex_model"
# Codex binary. Candidates: every codex on PATH (type -aP) plus every
# ~/.npm/_npx/*/node_modules/.bin/codex. The version is the token after
# "codex-cli " on the --version line; other stdout lines are ignored. Stable
# means exactly N.N.N. Alpha means N.N.N-alpha... Anything else, including
# -beta and -rc, is rejected. Accept a stable version at or above 0.159.3
# (the first stable verified to run gpt-6-astra, on the executor, 2026-10-01).
# Accept an -alpha at or above 0.154 as the fallback. Reject stable 0.154.0
# (it rejects Astra). Pick the newest accepted stable; only if none, the
# newest accepted alpha. A candidate whose --version fails or prints no
# version token is skipped. CONCLAVE_CODEX_BIN (or COUNCIL_CODEX_BIN)
# replaces discovery so a test can point at a stub.
codex_bin=""
codex_best_stable="" codex_stable_bin=""
codex_best_alpha="" codex_alpha_bin=""
codex_at_least() { # $1 version, $2 floor. True when version >= floor.
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]
}
codex_version_token() {
  # The token after "codex-cli " on that line. Other stdout lines are ignored.
  printf '%s\n' "$1" | sed -nE 's/^codex-cli[[:space:]]+([^[:space:]]+).*/\1/p' | head -1
}
codex_consider() {
  local c="$1" ver_out token base
  [ -n "$c" ] || return 0
  ver_out=$("$c" --version 2>/dev/null) || return 0
  token="$(codex_version_token "$ver_out")"
  [ -n "$token" ] || return 0
  if printf '%s\n' "$token" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+-alpha'; then
    base=${token%%-*}
    codex_at_least "$base" 0.154.0 || return 0
    if [ -z "$codex_best_alpha" ] || [ "$(printf '%s\n%s\n' "$codex_best_alpha" "$token" | sort -V | tail -1)" = "$token" ]; then
      codex_best_alpha=$token
      codex_alpha_bin=$c
    fi
    return 0
  fi
  if printf '%s\n' "$token" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    codex_at_least "$token" 0.159.3 || return 0
    if [ -z "$codex_best_stable" ] || [ "$(printf '%s\n%s\n' "$codex_best_stable" "$token" | sort -V | tail -1)" = "$token" ]; then
      codex_best_stable=$token
      codex_stable_bin=$c
    fi
  fi
}
codex_bin_override="$(conclave_env CODEX_BIN)"
if [ -n "$codex_bin_override" ]; then
  codex_bin=$codex_bin_override
else
  while IFS= read -r c; do
    codex_consider "$c"
  done <<EOF
$(type -aP codex 2>/dev/null || true)
EOF
  for c in ~/.npm/_npx/*/node_modules/.bin/codex; do
    [ -e "$c" ] || continue
    codex_consider "$c"
  done
  if [ -n "$codex_stable_bin" ]; then
    codex_bin=$codex_stable_bin
  else
    codex_bin=$codex_alpha_bin
  fi
fi
emit CODEX "$codex_bin"
# xAI native: no pin; grok's default tracks its newest release.
emit GROK_NATIVE_MODEL "$(grok models 2>/dev/null |
  sed -nE 's/^Default model: ([A-Za-z0-9.-]+).*/\1/p')"
# Newest Wonder full id. Empty when the listing fails or has no full id; the leg skips.
# CONCLAVE_MUSE_CATALOG (COUNCIL_MUSE_CATALOG is the alias) reads a saved
# listing and does not start muse. Tests set it. A real run leaves it unset.
muse_catalog="$(conclave_env MUSE_CATALOG)"
muse_args=()
if [ -n "$muse_catalog" ]; then
  muse_args=(--catalog "$muse_catalog")
fi
muse_model="$(python3 "$(dirname "${BASH_SOURCE[0]}")/muse-models.py" "${muse_args[@]}" 2>/dev/null || true)"
emit MUSE_WONDER_MODEL "$muse_model"
# Cursor: frontier fallbacks only. No OpenAI.
# CURSOR_GROK is xhigh: the stuck-builder value.
# CURSOR_BUILDER is high, Fast variant: the builder role (Ryan, 2026-10-02:
# the builder is the limiting factor and Cursor Models has headroom). Voters
# and the stuck-builder stay on the standard variants. The chair is Fast
# too (2026-10-02), picked by the Bot's launch command, not here.
emit CURSOR_FABLE "$(newest '^claude-fable-([0-9-]+)-thinking-xhigh$' <<<"$cursor_ids")"
emit CURSOR_GROK "$(newest '^grok-([0-9.]+)-xhigh$' <<<"$cursor_ids")"
emit CURSOR_BUILDER "$(newest '^grok-([0-9.]+)-high-fast$' <<<"$cursor_ids")"

if [ -z "$codex_bin" ] || [ -z "$codex_model" ]; then
  echo "resolve-models: CODEX or CODEX_REVIEW_MODEL is empty" >&2
  exit 1
fi
