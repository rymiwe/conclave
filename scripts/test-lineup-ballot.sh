#!/usr/bin/env bash
# The default ballot is seat-usage --route lineup. CONCLAVE_SEAT_USAGE is the
# stub. A missing binary, a failed run, or mode ask keeps the fixed default.
# No live router and no live model call.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
CONCLAVE="$ROOT/scripts/conclave.sh"
work="$(mktemp -d)"
# conclave.sh clears ID when it is sourced. Set the ballot id after that.
ID=""
DIR=""
trap 'rm -rf "$work" ${DIR:+"$DIR"}' EXIT

passed=0
failed=0
check() { # $1 name, rest is the test command
  local name="$1"
  shift
  if "$@"; then
    passed=$((passed + 1))
    printf 'ok %s\n' "$name"
  else
    failed=$((failed + 1))
    printf 'FAIL %s\n' "$name"
  fi
}

mkdir -p "$work/bin" "$work/home/.codex" "$work/log"
SEAT_CALLED="$work/called"
SEAT_ARGV="$work/argv"
export SEAT_CALLED SEAT_ARGV
: >"$SEAT_ARGV"

cat >"$work/bin/seat-usage" <<'EOF'
#!/bin/sh
touch "$SEAT_CALLED"
if [ -n "${SEAT_ARGV:-}" ]; then
  printf '%s\n' "$*" >>"$SEAT_ARGV"
fi
if [ -n "${SEAT_STUB_ERR:-}" ]; then
  printf '%s\n' "$SEAT_STUB_ERR" >&2
fi
if [ "${SEAT_STUB_RC:-0}" != 0 ]; then
  exit "$SEAT_STUB_RC"
fi
cat "$SEAT_STUB_JSON"
EOF
cat >"$work/bin/codex" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "codex-cli 0.200.0"; exit 0; fi
exit 0
EOF
cat >"$work/bin/claude" <<'EOF'
#!/bin/sh
echo "claude 0"
exit 0
EOF
cat >"$work/bin/grok" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "models" ]; then echo "Default model: grok-test-1"; exit 0; fi
echo "grok 0"
exit 0
EOF
cat >"$work/bin/muse" <<'EOF'
#!/bin/sh
echo "muse 0"
exit 0
EOF
cat >"$work/bin/cursor-agent" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$work/bin/"*

cat >"$work/home/.codex/models_cache.json" <<'EOF'
{"models": [{"slug": "gpt-test-astra", "visibility": "list", "priority": 1}]}
EOF
cat >"$work/muse-catalog.json" <<'EOF'
{"models":[
  {"modelId":"muse-wonder-1.0","releaseDate":"2026-10-12"},
  {"modelId":"muse-wonder-full-1.0","releaseDate":"2026-10-12"}
]}
EOF

write_lineup() { # $1 dest, remaining args are families
  python3 - "$@" <<'PY'
import json, sys
dest = sys.argv[1]
families = sys.argv[2:]
doc = [{"role": "lineup", "mode": "default", "reason": "stub", "lineup": {
    "voters": [{"family": fam, "effort": "high", "seat": {"fable": "claude", "astra": "chatgpt", "grok": "grok", "muse": "muse"}[fam], "vendor": "v"} for fam in families]
}}]
with open(dest, "w", encoding="utf-8") as fh:
    json.dump(doc, fh)
PY
}

write_lineup "$work/all.json" fable astra grok muse
write_lineup "$work/no-fable.json" astra grok muse
write_lineup "$work/pair.json" fable astra
write_lineup "$work/one.json" muse
printf '%s\n' '[{"role":"lineup","mode":"ask","reason":"Fable reading unknown","lineup":null}]' >"$work/ask.json"
printf '%s\n' 'not json' >"$work/bad.json"
printf '%s\n' '[{"role":"lineup","mode":"default","reason":"stub","lineup":{"voters":[{"family":"astra","seat":"chatgpt"},{"family":"grok","seat":"grok"},{"family":"muse","seat":"muse"}]},"excluded":{"vendor":"anthropic","name":"Anthropic","family":"opus","why":"Opus built the diff"}}]' >"$work/anthropic-built.json"
# What seat-usage with --exclude-vendor prints for anthropic plus xai trailers.
printf '%s\n' '[{"role":"lineup","mode":"default","reason":"stub","lineup":{"voters":[{"family":"astra","seat":"chatgpt"},{"family":"muse","seat":"muse"}]},"skipped":[{"family":"fable","seat":"claude","vendor":"anthropic","code":"builder_vendor"},{"family":"grok","seat":"grok","vendor":"xai","code":"excluded_vendor"}]}]' >"$work/excluded.json"
# What a seat-usage too old for --exclude-vendor prints: grok still seated.
printf '%s\n' '[{"role":"lineup","mode":"default","reason":"stub","lineup":{"voters":[{"family":"astra","seat":"chatgpt"},{"family":"grok","seat":"grok"}]},"skipped":[{"family":"fable","seat":"claude","vendor":"anthropic","code":"builder_vendor"}]}]' >"$work/old.json"

COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"
unset COUNCIL_SOURCE_ONLY CONCLAVE_SOURCE_ONLY
# Argon is seated by Conclave on design and mixed. This file checks the lineup.
export CONCLAVE_ARGON=0
ID="lineupballot$$"
DIR="$ROOT/tmp/council/$ID"

use_router() { # $1 json, $2 exit code
  CONCLAVE_SEAT_USAGE="$work/bin/seat-usage"
  SEAT_STUB_JSON="$1"
  SEAT_STUB_RC="${2:-0}"
  rm -f "$SEAT_CALLED"
  export CONCLAVE_SEAT_USAGE SEAT_STUB_JSON SEAT_STUB_RC
}

apply() { # $1 kind, $2 claude sku
  KIND="$1"
  VOTERS="$DEFAULT_VOTERS"
  VOTERS_EXPLICIT=0
  BALLOT_SOURCE=""
  BALLOT_SOURCE_NOTE=""
  OPUS_LEG_SKIPPED=0
  BALLOT_ROUTE_WARNING=""
  CLAUDE_SKU="${2:-fable}"
  apply_kind_voters
}

source_is() { [ "$(ballot_source_line)" = "$1" ]; }
called() { [ -f "$SEAT_CALLED" ]; }
not_called() { [ ! -f "$SEAT_CALLED" ]; }

use_router "$work/all.json" 0
: >"$SEAT_ARGV"
apply design
check "design takes every lineup leg" [ "$VOTERS" = "claude,codex,grok,muse" ]
check "design names the lineup" source_is "ballot: lineup"
check "design called the router" called
check "design passes its kind" grep -qx -- '--route lineup --json --kind design' "$SEAT_ARGV"
check "design omits author" bash -c '! grep -q -- "--author" "$1"' _ "$SEAT_ARGV"

apply mixed
check "mixed takes every lineup leg" [ "$VOTERS" = "claude,codex,grok,muse" ]
check "mixed names the lineup" source_is "ballot: lineup"

apply audit
check "audit takes every lineup leg" [ "$VOTERS" = "claude,codex,grok,muse" ]
check "audit names the lineup" source_is "ballot: lineup"

: >"$SEAT_ARGV"
apply review
check "review takes the route's voters" [ "$VOTERS" = "claude,codex,grok,muse" ]
check "review passes its kind" grep -qx -- '--route lineup --json --kind review --ballot '"$ID" "$SEAT_ARGV"
check "review defaults the author" bash -c '! grep -q -- "--author" "$1"' _ "$SEAT_ARGV"
check "review names the lineup" source_is "ballot: lineup"
check "review does not add opus" [ "$OPUS_LEG_SKIPPED" -eq 0 ]

use_router "$work/no-fable.json" 0
apply review
check "review keeps the route when fable is absent" [ "$VOTERS" = "codex,grok,muse" ]

use_router "$work/anthropic-built.json" 0
apply review
check "review keeps the route when Anthropic built the diff" [ "$VOTERS" = "codex,grok,muse" ]
check "review names Anthropic as sitting out" [ "$(excluded_line)" = "excluded: Anthropic (Opus built the diff)" ]

use_router "$work/pair.json" 0
apply review opus
check "opus sku keeps the route pair" [ "$VOTERS" = "claude,codex" ]
check "opus sku does not invent a coverage leg" [ "$OPUS_LEG_SKIPPED" -eq 0 ]
check "opus sku still came from the lineup" source_is "ballot: lineup"

use_router "$work/ask.json" 0
KIND=design
VOTERS="grok,muse"
VOTERS_EXPLICIT=1
rm -f "$SEAT_CALLED"
apply_kind_voters
check "explicit voters win" [ "$VOTERS" = "grok,muse" ]
check "explicit voters skip the router" not_called
check "explicit voters name the flag" source_is "ballot: --voters"

use_router "$work/one.json" 0
apply design
check "one lineup leg is kept" [ "$VOTERS" = "muse" ]
two=0
if ( require_design_voters ) >/dev/null 2>&1; then
  two=1
fi
check "design still requires two legs" [ "$two" -eq 0 ] 

CONCLAVE_SEAT_USAGE="$work/missing-seat-usage"
export CONCLAVE_SEAT_USAGE
apply design
check "missing router uses the fixed pair" [ "$VOTERS" = "claude,codex" ]
check "missing router says so" source_is "ballot: fallback (seat-usage missing; fixed default)"

apply review
check "missing router keeps Fable and Astra" [ "$VOTERS" = "claude,codex" ]
check "missing review router says so" source_is "ballot: fallback (seat-usage missing; fixed default)"
check "missing review router warns" [ "$BALLOT_ROUTE_WARNING" = "warning: review route unavailable (seat-usage missing); using Fable and Astra" ]

use_router "$work/ask.json" 0
apply review
check "ask review uses Fable and Astra" [ "$VOTERS" = "claude,codex" ]
check "ask review says so" source_is "ballot: fallback (seat-usage asked; fixed default)"
check "ask review warns" [ "$BALLOT_ROUTE_WARNING" = "warning: review route unavailable (seat-usage asked); using Fable and Astra" ]

use_router "$work/all.json" 0
KIND=review
VOTERS="fable,astra"
VOTERS_EXPLICIT=1
rm -f "$SEAT_CALLED"
apply_kind_voters
check "voters fable,astra maps onto the legs" [ "$VOTERS" = "claude,codex" ]
check "voters override skips the router" not_called

python3 - "$work/review-route.json" <<'PY'
import json, sys
doc = [{"role": "lineup", "mode": "default", "reason": "stub", "lineup": {"voters": [
    {"family": "wonder", "seat": "muse", "effort": "high"},
    {"family": "argon", "seat": "google", "effort": "high"},
]}}]
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(doc, fh)
PY
use_router "$work/review-route.json" 0
apply review
check "review maps wonder and argon" [ "$VOTERS" = "muse,argon" ]
check "argon author vendor is google" [ "$(review_author_vendor argon)" = google ]

use_router "$work/all.json" 1
apply mixed
check "failed router uses the fixed pair" [ "$VOTERS" = "claude,codex" ]
check "failed router says so" source_is "ballot: fallback (seat-usage failed; fixed default)"
check "failed router was called" called

use_router "$work/ask.json" 0
apply audit
check "ask mode uses the fixed pair" [ "$VOTERS" = "claude,codex" ]
check "ask mode says so" source_is "ballot: fallback (seat-usage asked; fixed default)"

python3 - "$work/cursor.json" <<'PY'
import json, sys
doc = [{"role": "lineup", "mode": "default", "reason": "stub", "lineup": {"voters": [
    {"family": "fable", "seat": "cursor", "effort": "high"},
    {"family": "astra", "seat": "chatgpt", "effort": "high"},
    {"family": "grok", "seat": "grok", "effort": "high"},
    {"family": "wonder-full", "seat": "muse", "effort": "high"},
]}}]
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(doc, fh)
PY
python3 - "$work/down.json" <<'PY'
import json, sys
doc = [{"role": "lineup", "mode": "default", "reason": "stub", "lineup": {"voters": [
    {"family": "astra", "seat": "chatgpt", "effort": "high"},
    {"family": "grok", "seat": "grok", "effort": "high"},
    {"family": "wonder-full", "seat": "muse", "effort": "high"},
]}, "downgrade": {"family": "fable", "reason": "no frontier pool with room: approve a downgraded leg"}}]
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(doc, fh)
PY
use_router "$work/cursor.json" 0
CURSOR_FABLE="cursor-fable-test"
apply design
check "design launches cursor-fable when the seat is cursor" [ "$CLAUDE_SKU" = "cursor-fable" ]
check "cursor seat keeps the lineup voters" [ "$VOTERS" = "claude,codex,grok,muse" ]
check "resolved cursor id is not a downgrade prompt" [ -z "${BALLOT_DOWNGRADE_WHY:-}" ]
unset CURSOR_FABLE
apply design
check "unresolved cursor id prompts for a downgrade" [ "$BALLOT_DOWNGRADE_WHY" = "Cursor Fable id cannot be resolved: approve a downgraded leg" ]
check "unresolved cursor id does not fall back" [ "$VOTERS" = "claude,codex,grok,muse" ]

use_router "$work/down.json" 0
apply design
check "downgrade prompt keeps the other voters" [ "$VOTERS" = "codex,grok,muse" ]
check "downgrade prompt is not the fixed pair" [ "$VOTERS" != "claude,codex" ]
check "downgrade prompt names the approval" [ "$BALLOT_DOWNGRADE_WHY" = "no frontier pool with room: approve a downgraded leg" ]
check "downgrade prompt stays a lineup" source_is "ballot: lineup"

use_router "$work/bad.json" 0
apply design
check "bad json uses the fixed pair" [ "$VOTERS" = "claude,codex" ]
check "bad json says the router failed" source_is "ballot: fallback (seat-usage failed; fixed default)"

# Dry-run prints the same source into stdout, routing.md, and the lineup.
mkdir -p "$DIR"
cat >"$DIR/brief.md" <<'EOF'
kind: design
difficulty: low

# Goal
Pick a storage layout.

## Decision needed
Which layout.
EOF
git init -q -b main "$work/target"
echo t >"$work/target/f"
git -C "$work/target" add f
git -C "$work/target" commit -qm one

dry() {
  env -u COUNCIL_SKIP_UPDATE_CHECK -u COUNCIL_CODEX_BIN -u COUNCIL_SEAT_USAGE \
    -u CONCLAVE_VOTER -u COUNCIL_VOTER \
    HOME="$work/home" PATH="$work/bin:$PATH" \
    CONCLAVE_ARGON=0 \
    CONCLAVE_SKIP_UPDATE_CHECK=1 \
    CONCLAVE_CODEX_BIN="$work/bin/codex" \
    CONCLAVE_SEAT_USAGE="$CONCLAVE_SEAT_USAGE" \
    CONCLAVE_MUSE_CATALOG="$work/muse-catalog.json" \
    SEAT_STUB_JSON="$SEAT_STUB_JSON" \
    SEAT_STUB_RC="${SEAT_STUB_RC:-0}" \
    SEAT_CALLED="$work/dry-called" \
    bash "$CONCLAVE" "$@"
}

capture() {
  dry_ec=0
  out="$(dry "$@")" || dry_ec=$?
}

use_router "$work/all.json" 0
capture "$ID" --dry-run --repo "$work/target"
check "design dry-run exits 0" [ "$dry_ec" -eq 0 ]
check "design dry-run prints the lineup source" grep -qx '  ballot: lineup' <<<"$out"
check "design dry-run prints the voters" grep -qx '  voters: claude,codex,grok,muse' <<<"$out"
check "routing.md names the lineup" grep -qx 'ballot: lineup' "$DIR/preview/routing.md"
check "routing.md lists the voters" grep -qx 'voters: claude,codex,grok,muse' "$DIR/preview/routing.md"

rm -rf "$DIR/preview"
use_router "$work/cursor.json" 0
printf '%s\n' 'kind: design' 'difficulty: low' '' '# Goal' 'Pick a storage layout.' >"$DIR/brief.md"
capture "$ID" --dry-run --repo "$work/target"
check "cursor seat dry-run exits 0" [ "$dry_ec" -eq 0 ]
check "cursor seat dry-run is not review-only" bash -c '! grep -q "kind review only" <<<"$1"' _ "$out"
check "design routing sku is cursor-fable" grep -qx 'sku: cursor-fable' "$DIR/preview/routing.md"

rm -rf "$DIR/preview"
use_router "$work/ask.json" 0
capture "$ID" --dry-run --repo "$work/target"
check "failed reading dry-run exits 0" [ "$dry_ec" -eq 0 ]
check "failed reading dry-run prints claude,codex" grep -qx '  voters: claude,codex' <<<"$out"
check "failed reading dry-run is the ask fallback" grep -qx '  ballot: fallback (seat-usage asked; fixed default)' <<<"$out"

python3 - "$work/approved.json" <<'PY'
import json, sys
doc = [{"role": "lineup", "mode": "default", "reason": "stub", "lineup": {"voters": [
    {"family": "fable", "seat": "claude", "effort": "max", "downgraded": True},
    {"family": "astra", "seat": "chatgpt", "effort": "high"},
    {"family": "grok", "seat": "grok", "effort": "high"},
    {"family": "wonder-full", "seat": "muse", "effort": "high"},
]}}]
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(doc, fh)
PY
rm -rf "$DIR/preview"
use_router "$work/approved.json" 0
printf '%s\n' 'kind: design' 'difficulty: low' 'effort: max' '' '# Goal' 'Pick a storage layout.' >"$DIR/brief.md"
capture "$ID" --dry-run --repo "$work/target"
check "approved downgrade dry-run exits 0" [ "$dry_ec" -eq 0 ]
check "approved downgrade effort stays max" grep -q 'claude=.* effort=max ' <<<"$out"
check "approved downgrade routing keeps max" grep -qx 'effort: max' "$DIR/preview/routing.md"

rm -rf "$DIR/preview"
use_router "$work/all.json" 0
printf '%s\n' 'kind: review' 'difficulty: low' '' '# Goal' 'Check the change.' >"$DIR/brief.md"
capture "$ID" --dry-run --repo "$work/target" --whole
check "review dry-run exits 0" [ "$dry_ec" -eq 0 ]
check "review lineup names the source" grep -qx 'ballot: lineup' "$DIR/preview/lineup.md"
check "review lineup uses the route" grep -qx 'voters: claude,codex,grok,muse' "$DIR/preview/lineup.md"
check "review dry-run prints that lineup" grep -qx 'voters: claude,codex,grok,muse' <<<"$out"

rm -rf "$DIR/preview"
printf '%s\n' 'A short page.' >"$work/page.md"
printf '%s\n' 'kind: audit' 'difficulty: low' '' '# Goal' 'Read the page.' >"$DIR/brief.md"
capture "$ID" --dry-run --repo "$work/target" --target "$work/page.md" --target-type content
check "audit dry-run exits 0" [ "$dry_ec" -eq 0 ]
check "audit lineup names the source" grep -qx 'ballot: lineup' "$DIR/preview/lineup.md"
check "audit lineup lists the voters" grep -qx 'voters: claude,codex,grok,muse' "$DIR/preview/lineup.md"
check "audit dry-run prints that lineup" grep -qx 'voters: claude,codex,grok,muse' <<<"$out"

rm -rf "$DIR/preview"
printf '%s\n' 'kind: design' 'difficulty: low' '' '# Goal' 'Pick a storage layout.' >"$DIR/brief.md"
CONCLAVE_SEAT_USAGE="$work/missing-seat-usage"
export CONCLAVE_SEAT_USAGE
capture "$ID" --dry-run --repo "$work/target"
check "fallback dry-run exits 0" [ "$dry_ec" -eq 0 ]
check "fallback dry-run says so" grep -qx '  ballot: fallback (seat-usage missing; fixed default)' <<<"$out"
check "fallback routing.md says so" grep -qx 'ballot: fallback (seat-usage missing; fixed default)' "$DIR/preview/routing.md"
check "fallback routing.md keeps the pair" grep -qx 'voters: claude,codex' "$DIR/preview/routing.md"

rm -rf "$DIR/preview"
printf '%s\n' 'kind: review' 'difficulty: low' '' '# Goal' 'Check the change.' >"$DIR/brief.md"
CONCLAVE_SEAT_USAGE="$work/missing-seat-usage"
export CONCLAVE_SEAT_USAGE
warn_ec=0
warn_out="$(dry "$ID" --dry-run --repo "$work/target" --whole 2>&1)" || warn_ec=$?
check "review fallback dry-run exits 0" [ "$warn_ec" -eq 0 ]
check "review fallback warns on stderr" grep -qx 'warning: review route unavailable (seat-usage missing); using Fable and Astra' <<<"$warn_out"
check "review fallback lineup warns" grep -qx 'warning: review route unavailable (seat-usage missing); using Fable and Astra' "$DIR/preview/lineup.md"
check "review fallback keeps Fable and Astra" grep -qx 'voters: claude,codex' "$DIR/preview/lineup.md"

# The stub names who sat out. The script prints that on the lineup and in routing.md.
python3 - "$work/sat.json" <<'PY'
import json, sys
doc = [{"role": "lineup", "mode": "default", "reason": "stub",
        "excluded": {"vendor": "xai", "name": "xAI", "family": "grok", "why": "Grok built the diff"},
        "lineup": {"voters": [
            {"family": "fable", "seat": "claude", "effort": "high"},
            {"family": "astra", "seat": "chatgpt", "effort": "high"},
            {"family": "muse", "seat": "muse", "effort": "high"},
        ]}}]
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(doc, fh)
PY
rm -rf "$DIR/preview"
printf '%s\n' 'kind: review' 'author: grok' 'difficulty: low' '' '# Goal' 'Check the change.' >"$DIR/brief.md"
use_router "$work/sat.json" 0
: >"$SEAT_ARGV"
capture "$ID" --dry-run --repo "$work/target" --whole
check "author review exits 0" [ "$dry_ec" -eq 0 ]
check "author review passes the family" grep -qx -- '--route lineup --json --kind review --author grok --builder-vendor xai --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"
check "author review prints the exclusion" grep -qx 'excluded: xAI (Grok built the diff)' "$DIR/preview/lineup.md"
check "author review prints it on stdout" grep -qx 'excluded: xAI (Grok built the diff)' <<<"$out"
check "author review keeps the stub's voters" grep -qx 'voters: claude,codex,muse' "$DIR/preview/lineup.md"

python3 - "$work/open.json" <<'PY'
import json, sys
doc = [{"role": "lineup", "mode": "default", "reason": "stub",
        "excluded": {"vendor": None, "name": None, "family": None,
                     "why": "design judges a packet the chair framed"},
        "lineup": {"voters": [
            {"family": "fable", "seat": "claude", "effort": "high"},
            {"family": "astra", "seat": "chatgpt", "effort": "high"},
            {"family": "grok", "seat": "grok", "effort": "high"},
            {"family": "muse", "seat": "muse", "effort": "high"},
        ]}}]
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(doc, fh)
PY
rm -rf "$DIR/preview"
printf '%s\n' 'kind: design' 'difficulty: low' '' '# Goal' 'Pick a storage layout.' >"$DIR/brief.md"
use_router "$work/open.json" 0
: >"$SEAT_ARGV"
capture "$ID" --dry-run --repo "$work/target"
check "design exclusion exits 0" [ "$dry_ec" -eq 0 ]
check "design exclusion passes kind only" grep -qx -- '--route lineup --json --kind design' "$SEAT_ARGV"
check "design routing.md names who sat out" grep -qx 'excluded: none (design judges a packet the chair framed)' "$DIR/preview/routing.md"
check "design dry-run prints who sat out" grep -qx '  excluded: none (design judges a packet the chair framed)' <<<"$out"

rm -rf "$DIR/preview"
printf '%s\n' 'kind: audit' 'difficulty: low' '' '# Goal' 'Read the page.' >"$DIR/brief.md"
printf '%s\n' 'A short page.' >"$work/page.md"
use_router "$work/all.json" 0
: >"$SEAT_ARGV"
capture "$ID" --dry-run --repo "$work/target" --target "$work/page.md" --target-type content
check "audit omits author" bash -c '! grep -q -- "--author" "$1"' _ "$SEAT_ARGV"
check "audit dry-run still exits 0" [ "$dry_ec" -eq 0 ]

rm -rf "$DIR/preview"
printf '%s\n' 'kind: design' 'author: composer' 'difficulty: low' '' '# Goal' 'Pick a storage layout.' >"$DIR/brief.md"
bad_ec=0
bad_out="$(dry "$ID" --dry-run --repo "$work/target" 2>&1)" || bad_ec=$?
check "bad author exits" [ "$bad_ec" -ne 0 ]
check "bad author names the family" grep -q 'invalid author family: composer' <<<"$bad_out"

# PR author and commit trailers. gh pr view --json author,commits is stubbed.
# A model-naming Co-authored-by passes --builder-vendor. A User with none passes
# --author human. Cursor <cursoragent@cursor.com> does not name a model.
GH_PR_JSON="$work/pr.json"
GH_ARGV="$work/gh-argv"
export GH_PR_JSON GH_ARGV
: >"$GH_ARGV"
cat >"$work/bin/gh" <<'EOF'
#!/bin/sh
if [ -n "${GH_ARGV:-}" ]; then
  printf '%s\n' "$*" >>"$GH_ARGV"
fi
if [ "${1:-}" = pr ] && [ "${2:-}" = view ]; then
  cat "${GH_PR_JSON:?}"
  exit 0
fi
echo "unexpected gh: $*" >&2
exit 1
EOF
chmod +x "$work/bin/gh"
old_path="$PATH"
export PATH="$work/bin:$PATH"

write_pr_json() { # $1 is python that assigns doc
  python3 - "$GH_PR_JSON" "$1" <<'PY'
import json, sys
path, source = sys.argv[1], sys.argv[2]
ns = {}
exec(source, ns)
json.dump(ns["doc"], open(path, "w"), ensure_ascii=False)
PY
}

reset_pr_route() {
  REVIEW_PR_FACT_NUM=""
  REVIEW_PR_FACT_VENDOR=""
  REVIEW_PR_FACT_VENDORS=""
  REVIEW_PR_FACT_HUMAN=""
  REVIEW_PR_FACT_SKIP=""
  HEADER_AUTHOR=""
  HEADER_PR=""
  FLAG_PR=42
  REPO="$work/target"
  KIND=review
  : >"$SEAT_ARGV"
  : >"$GH_ARGV"
}

# $1 is python that assigns doc. $2 is the router stub json.
route_pr() {
  write_pr_json "$1"
  reset_pr_route
  use_router "${2:-$work/all.json}" 0
  apply review
}

route_pr 'doc = {
  "author": {"is_bot": False, "login": "rymiwe", "name": "Ryan Weiss", "type": "User"},
  "commits": [{"messageHeadline": "Seat the reviewer",
               "messageBody": "Co-Authored-By: Grok (via cursor-agent) <noreply@x.ai>\n"}],
}'
check "trailer review passes builder vendor" grep -qx -- '--route lineup --json --kind review --builder-vendor xai --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"
check "trailer review omits author" bash -c '! grep -q -- "--author" "$1"' _ "$SEAT_ARGV"
check "trailer review asked gh for author and commits" grep -qx 'pr view 42 --json author,commits' "$GH_ARGV"
check "trailer review calls gh once" [ "$(grep -c 'pr view' "$GH_ARGV")" -eq 1 ]
check "trailer review builder vendor is xai" [ "$(review_resolved_builder_vendor)" = xai ]
: >"$SEAT_ARGV"
: >"$GH_ARGV"
review_route_json >/dev/null
check "trailer re-route passes builder vendor" grep -qx -- '--route lineup --json --kind review --builder-vendor xai --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"
check "trailer re-route does not call gh again" [ ! -s "$GH_ARGV" ]

route_pr 'doc = {
  "author": {"is_bot": False, "login": "rymiwe", "name": "Ryan Weiss"},
  "commits": [{"messageHeadline": "Seat the reviewer",
               "messageBody": "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"}],
}'
check "opus trailer passes anthropic" grep -qx -- '--route lineup --json --kind review --builder-vendor anthropic --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"
check "opus trailer omits author" bash -c '! grep -q -- "--author" "$1"' _ "$SEAT_ARGV"

route_pr 'doc = {
  "author": {"is_bot": False, "login": "rymiwe", "name": "Ryan Weiss"},
  "commits": [{"messageHeadline": "Fix the seat",
               "messageBody": "Co-authored-by: Cursor <cursoragent@cursor.com>\n"}],
}'
check "human review passes author human" grep -qx -- '--route lineup --json --kind review --author human --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"
check "human review omits builder vendor" bash -c '! grep -q -- "--builder-vendor" "$1"' _ "$SEAT_ARGV"
check "human review builder vendor is empty" [ -z "$(review_resolved_builder_vendor)" ]
check "human review calls gh once" [ "$(grep -c 'pr view' "$GH_ARGV")" -eq 1 ]
: >"$SEAT_ARGV"
: >"$GH_ARGV"
review_route_json >/dev/null
check "human re-route passes author human" grep -qx -- '--route lineup --json --kind review --author human --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"
check "human re-route does not call gh again" [ ! -s "$GH_ARGV" ]
check "human re-route omits builder vendor" bash -c '! grep -q -- "--builder-vendor" "$1"' _ "$SEAT_ARGV"

route_pr 'doc = {
  "author": {"is_bot": False, "login": "rymiwe", "name": "Ryan Weiss"},
  "commits": [{"messageHeadline": "Seat the reviewer",
               "messageBody": "Co-Authored-By: Grok (via cursor-agent) <cursoragent@cursor.com>\n"}],
}'
check "family on the cursor mailbox passes builder vendor" grep -qx -- '--route lineup --json --kind review --builder-vendor xai --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"
check "family on the cursor mailbox omits author" bash -c '! grep -q -- "--author" "$1"' _ "$SEAT_ARGV"

route_pr 'doc = {
  "author": {"is_bot": False, "login": "rymiwe", "name": "Ryan Weiss", "type": "User"},
  "commits": [],
}'
check "type User with no trailer passes author human" grep -qx -- '--route lineup --json --kind review --author human --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"

route_pr 'doc = {
  "author": {"is_bot": True, "login": "cursor[bot]", "name": "Cursor", "type": "Bot"},
  "commits": [{"messageHeadline": "Fix",
               "messageBody": "Co-authored-by: Cursor <cursoragent@cursor.com>\n"}],
}'
check "bot review omits author" bash -c '! grep -q -- "--author" "$1"' _ "$SEAT_ARGV"
check "bot review omits builder vendor" bash -c '! grep -q -- "--builder-vendor" "$1"' _ "$SEAT_ARGV"

route_pr 'doc = {
  "author": {"is_bot": False, "login": "rymiwe", "name": "Ryan Weiss", "type": "User"},
  "commits": [
    {"messageHeadline": "One", "messageBody": "Co-Authored-By: Grok <noreply@x.ai>\n"},
    {"messageHeadline": "Two", "messageBody": "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"},
  ],
}' "$work/excluded.json"
check "disagreeing trailers omit author" bash -c '! grep -q -- "--author" "$1"' _ "$SEAT_ARGV"
# Ambiguous trailers fail closed: every vendor they name is excluded on every pool.
check "disagreeing trailers exclude every named vendor" grep -qx -- '--route lineup --json --kind review --builder-vendor anthropic --exclude-vendor anthropic --exclude-vendor xai --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"
check "disagreeing trailers pass no seat exclusion" bash -c '! grep -q -- "--exclude-seat" "$1"' _ "$SEAT_ARGV"
check "disagreeing trailers keep the router ballot" [ "$VOTERS" = "codex,muse" ]
check "disagreeing trailers resolve to the first vendor" [ "$(review_resolved_builder_vendor)" = anthropic ]
: >"$SEAT_ARGV"
review_route_json >/dev/null
check "disagreeing trailers re-route excludes every named vendor" grep -qx -- '--route lineup --json --kind review --builder-vendor anthropic --exclude-vendor anthropic --exclude-vendor xai --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"

route_pr 'doc = {
  "author": {"is_bot": False, "login": "rymiwe", "name": "Ryan Weiss", "type": "User"},
  "commits": [{"messageHeadline": "Seat",
               "messageBody": "Co-Authored-By: Opus and Grok <noreply@example.com>\n"}],
}' "$work/excluded.json"
check "one trailer naming two vendors omits author" bash -c '! grep -q -- "--author" "$1"' _ "$SEAT_ARGV"
check "one trailer naming two vendors excludes both" grep -qx -- '--route lineup --json --kind review --builder-vendor anthropic --exclude-vendor anthropic --exclude-vendor xai --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"

# Claude plus a co-author outside the known families is ambiguous. No
# anthropic voter, and one line says why.
write_pr_json 'doc = {
  "author": {"is_bot": False, "login": "rymiwe", "name": "Ryan Weiss", "type": "User"},
  "commits": [{"messageHeadline": "Seat",
               "messageBody": "Co-authored-by: Claude <noreply@anthropic.com>\nCo-authored-by: Jane Doe <jane@example.com>\n"}],
}'
reset_pr_route
use_router "$work/excluded.json" 0
apply review 2>"$work/amb.err"
check "claude plus unknown co-author excludes anthropic" grep -qx -- '--route lineup --json --kind review --builder-vendor anthropic --exclude-vendor anthropic --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"
check "claude plus unknown co-author omits author" bash -c '! grep -q -- "--author" "$1"' _ "$SEAT_ARGV"
check "claude plus unknown co-author builder vendor is anthropic" [ "$(review_resolved_builder_vendor)" = anthropic ]
check "ambiguous trailers print one line saying why" [ "$(grep -c 'trailers are ambiguous' "$work/amb.err")" -eq 1 ]
check "ambiguous line names the vendor" grep -q 'excluding every vendor they name (anthropic)' "$work/amb.err"

# A vendor seat-usage does not know stops the review (fail closed), the way
# merge-gate refuses with exit 3. seat-usage exits 2 and names it.
ambiguous_route() { # $1 vendors, $2 stub json, $3 stub rc, $4 stub stderr; prints the exit code
  local rc=0
  (
    review_pr_load_facts() { return 0; }
    reset_pr_route
    REVIEW_PR_FACT_VENDOR=ambiguous
    REVIEW_PR_FACT_VENDORS="$1"
    use_router "$2" "$3"
    SEAT_STUB_ERR="$4"
    export SEAT_STUB_ERR
    apply review
    exit 0
  ) >/dev/null 2>"$work/ambiguous.err" || rc=$?
  printf '%s\n' "$rc"
}
rc="$(ambiguous_route "anthropic mistral" "$work/all.json" 2 \
  "unknown vendor mistral; vendors: anthropic, google, meta, openai, xai (grok is an alias for xai)")"
check "unknown ambiguous vendor stops the review" [ "$rc" -eq 3 ]
check "unknown ambiguous vendor is passed to seat-usage" grep -q -- '--exclude-vendor mistral' "$SEAT_ARGV"
check "unknown ambiguous vendor error names the vendor" \
  grep -qx "conclave: refused: seat-usage does not know vendor 'mistral' named by ambiguous PR trailers; stopping the review" "$work/ambiguous.err"
check "unknown ambiguous vendor error is one line" [ "$(grep -c 'conclave: refused' "$work/ambiguous.err")" -eq 1 ]

# A seat-usage too old for --exclude-vendor ignores it and still exits 0.
# Its lineup does not report the exclusion, so the review stops.
rc="$(ambiguous_route "anthropic xai" "$work/old.json" 0 "")"
check "old seat-usage stops the review" [ "$rc" -eq 3 ]
check "old seat-usage error names the vendor and the flag" \
  grep -qx "conclave: refused: seat-usage did not report excluding vendor 'xai' named by ambiguous PR trailers (needs seat-usage with --exclude-vendor); stopping the review" "$work/ambiguous.err"
rc="$(ambiguous_route "anthropic xai" "$work/excluded.json" 0 "")"
check "seat-usage that reports the exclusion keeps the review" [ "$rc" -eq 0 ]
# Ask mode would keep the fixed Fable and Astra default, which seats a named
# vendor, so the review stops even when the exclusions are reported.
printf '%s\n' '[{"role":"lineup","mode":"ask","reason":"need 2 review seat(s) outside Anthropic, xAI; 1 eligible: ask Ryan","lineup":null,"skipped":[{"family":"fable","vendor":"anthropic","code":"builder_vendor"},{"family":"grok","vendor":"xai","code":"excluded_vendor"}]}]' >"$work/excluded-ask.json"
rc="$(ambiguous_route "anthropic xai" "$work/excluded-ask.json" 0 "")"
check "ask after exclusion stops an ambiguous review" [ "$rc" -eq 3 ]
check "ask after exclusion error says why" \
  grep -qx "conclave: refused: seat-usage seated no lineup outside the vendors ambiguous PR trailers named (anthropic, xai): need 2 review seat(s) outside Anthropic, xAI; 1 eligible: ask Ryan; stopping the review" "$work/ambiguous.err"
route_pr 'doc = {
  "author": {"is_bot": False, "login": "rymiwe", "name": "Ryan Weiss"},
  "commits": [{"messageHeadline": "One", "messageBody": "Co-Authored-By: Grok <noreply@x.ai>\n"}],
}' "$work/ask.json" 2>/dev/null
check "ask without ambiguous trailers keeps the fixed default" source_is "ballot: fallback (seat-usage asked; fixed default)"
# A seat-usage that fails or prints no JSON excluded nobody. The fixed
# fallback would seat a named vendor, so the review stops.
rc="$(ambiguous_route "anthropic xai" "$work/all.json" 1 "boom")"
check "failed seat-usage stops an ambiguous review" [ "$rc" -eq 3 ]
check "failed seat-usage error names the vendors and its stderr" \
  grep -qx "conclave: refused: seat-usage gave no lineup while ambiguous PR trailers named anthropic, xai (boom); stopping the review" "$work/ambiguous.err"
rc="$(ambiguous_route "anthropic xai" "$work/bad.json" 0 "")"
check "non-json seat-usage stops an ambiguous review" [ "$rc" -eq 3 ]
check "non-json seat-usage error names a vendor" grep -q "did not report excluding vendor 'anthropic'" "$work/ambiguous.err"
old_rc=0
(
  review_pr_load_facts() { return 0; }
  reset_pr_route
  REVIEW_PR_FACT_VENDOR=ambiguous
  REVIEW_PR_FACT_VENDORS="anthropic xai"
  use_router "$work/old.json" 0
  review_route_json >/dev/null
) 2>"$work/reroute.err" || old_rc=$?
check "old seat-usage re-route returns no lineup" [ "$old_rc" -ne 0 ]
check "old seat-usage re-route says why" grep -q "did not report excluding vendor 'xai'" "$work/reroute.err"
check "no hardcoded vendor seat map" bash -c '! grep -q "review_vendor_seat" "$1"' _ "$CONCLAVE"

write_pr_json 'doc = {
  "author": {"is_bot": False, "login": "rymiwe", "name": "Ryan Weiss", "type": "User"},
  "commits": [{"messageHeadline": "Seat",
               "messageBody": "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"}],
}'
reset_pr_route
HEADER_AUTHOR=grok
use_router "$work/all.json" 0
apply review
check "brief author wins over a trailer" grep -qx -- '--route lineup --json --kind review --author grok --builder-vendor xai --ballot '"$ID"' --repo '"$work/target" "$SEAT_ARGV"
check "brief author reseat vendor stays the family" [ "$(review_resolved_builder_vendor)" = xai ]

cat >"$work/bin/gh" <<'EOF'
#!/bin/sh
if [ -n "${GH_ARGV:-}" ]; then
  printf '%s\n' "$*" >>"$GH_ARGV"
fi
exit 1
EOF
chmod +x "$work/bin/gh"
reset_pr_route
use_router "$work/all.json" 0
apply review
check "failed gh omits author" bash -c '! grep -q -- "--author" "$1"' _ "$SEAT_ARGV"
check "failed gh omits builder vendor" bash -c '! grep -q -- "--builder-vendor" "$1"' _ "$SEAT_ARGV"
check "failed gh is asked once" [ "$(grep -c 'pr view' "$GH_ARGV")" -eq 1 ]

FLAG_PR=""
HEADER_AUTHOR=""
HEADER_PR=""
REVIEW_PR_FACT_NUM=""
REVIEW_PR_FACT_SKIP=""
export PATH="$old_path"

# Stubbed design run: seat-usage asks, claude exits 1, codex and argon vote,
# and a local Jev stub writes judgment.md. Then --judge on that ballot, whose
# voters.txt still names the missing voter.
JEV_PID=""
GATE_DIR=""
trap 'if [ -n "${JEV_PID:-}" ]; then kill "$JEV_PID" 2>/dev/null || true; fi; rm -rf "$work" ${DIR:+"$DIR"} ${GATE_DIR:+"$GATE_DIR"}' EXIT

mkdir -p "$work/gate/bin" "$work/gate/home/.codex"
printf '%s\n' '[{"role":"lineup","mode":"ask","reason":"stub asked","lineup":null}]' >"$work/gate/ask.json"
: >"$work/gate/seat.log"
printf '%s\n' '{"models": [{"slug": "gpt-test-astra", "visibility": "list", "priority": 1}]}' \
  >"$work/gate/home/.codex/models_cache.json"

cat >"$work/gate/bin/seat-usage" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$SEAT_LOG"
cat "$SEAT_JSON"
exit 0
EOF
cat >"$work/gate/bin/claude" <<'EOF'
#!/bin/sh
cat >/dev/null
touch "$CLAUDE_RAN"
echo "claude: exit 1" >&2
exit 1
EOF
cat >"$work/gate/bin/codex" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then
  echo "codex-cli 0.200.0"
  exit 0
fi
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then
    out="$a"
  fi
  prev="$a"
done
if [ -n "$out" ]; then
  cat >"$out" <<'BALLOT'
## Findings
codex finds the gate holds
## Approach
keep two vendors
## Would not do
stop the ballot
## Uncertainties
none
BALLOT
fi
exit 0
EOF
cat >"$work/gate/bin/agy" <<'EOF'
#!/bin/sh
for a in "$@"; do
  if [ "$a" = "/usage" ]; then
    exit 0
  fi
done
cat >/dev/null
printf '%s\n' '{"event":"init","init":{"agent":"conclave-voter"}}'
printf '%s\n' '{"event":"result","result":{"status":"SUCCESS","response":"## Findings\nargon finds the gate holds\n## Approach\nkeep two vendors\n## Would not do\nstop the ballot\n## Uncertainties\nnone\n"}}'
exit 0
EOF
cat >"$work/gate/bin/grok" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "models" ]; then
  echo "Default model: grok-test-1"
  exit 0
fi
exit 0
EOF
cat >"$work/gate/bin/cursor-agent" <<'EOF'
#!/bin/sh
if [ "${1:-}" = models ]; then
  printf '%s\n' claude-fable-9-9-thinking-xhigh grok-9.9-xhigh grok-9.9-xhigh-fast grok-9.9-high-fast
  exit 0
fi
touch "$CURSOR_RAN"
printf '%s\n' "$@" >"$CURSOR_ARGV"
exit 1
EOF
cat >"$work/gate/bin/jev-op" <<'EOF'
#!/bin/sh
printf '%s' 'stub-not-a-key'
EOF
cat >"$work/gate/jev-server.py" <<'EOF'
#!/usr/bin/env python3
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
count_path = Path(os.environ["JEV_COUNT"])
port_path = Path(os.environ["JEV_PORT_FILE"])

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        body = json.dumps({
            "model": "stub-jev",
            "answers": {
                "decision": {"choice": "no_automatic_decision", "confidence": 0.9},
                "same_approach_shape": {"noul": 0.2},
                "disagreements_only_minor": {"noul": 0.2},
            },
        }).encode()
        prev = 0
        if count_path.is_file():
            prev = int(count_path.read_text() or "0")
        count_path.write_text(str(prev + 1) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return

httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port_path.write_text(str(httpd.server_address[1]) + "\n")
httpd.serve_forever()
EOF
chmod +x "$work/gate/bin/"* "$work/gate/jev-server.py"
: >"$work/gate/jev.count"
JEV_COUNT="$work/gate/jev.count"
JEV_PORT_FILE="$work/gate/jev.port"
export JEV_COUNT JEV_PORT_FILE
python3 "$work/gate/jev-server.py" >/dev/null 2>&1 &
JEV_PID=$!
i=0
while [ "$i" -lt 50 ]; do
  [ -s "$work/gate/jev.port" ] && break
  i=$((i + 1))
  sleep 0.05
done
check "jev stub is listening" [ -s "$work/gate/jev.port" ]
GATE_ID="twovendor$$"
GATE_DIR="$ROOT/tmp/council/$GATE_ID"
mkdir -p "$GATE_DIR"
cat >"$GATE_DIR/brief.md" <<'EOF'
kind: design
difficulty: low

# Goal
Keep a two-vendor ballot.

## Decision needed
Proceed when two vendors voted.
EOF

run_gate() {
  env -u TYPESAFE_API_KEY -u JEV_API_KEY -u COUNCIL_JEV_OP -u COUNCIL_JEV_OP_REF \
    -u COUNCIL_SEAT_USAGE -u COUNCIL_CODEX_BIN -u COUNCIL_SKIP_UPDATE_CHECK \
    -u CONCLAVE_VOTER -u COUNCIL_VOTER \
    HOME="$work/gate/home" \
    PATH="$work/gate/bin:$PATH" \
    CONCLAVE_ARGON=1 \
    CONCLAVE_GITHUB_HTTPS=0 \
    CONCLAVE_SKIP_UPDATE_CHECK=1 \
    CONCLAVE_CODEX_BIN="$work/gate/bin/codex" \
    CONCLAVE_SEAT_USAGE="$work/gate/bin/seat-usage" \
    CONCLAVE_AGY="$work/gate/bin/agy" \
    CONCLAVE_MUSE_CATALOG="$work/muse-catalog.json" \
    CONCLAVE_JEV_OP="$work/gate/bin/jev-op" \
    CONCLAVE_JEV_OP_REF="op://test/stub" \
    TYPESAFE_BASE_URL="http://127.0.0.1:$(tr -d '[:space:]' <"$work/gate/jev.port")" \
    SEAT_LOG="$work/gate/seat.log" \
    SEAT_JSON="$work/gate/ask.json" \
    CLAUDE_RAN="$work/gate/claude.ran" \
    CURSOR_RAN="$work/gate/cursor.ran" \
    CURSOR_ARGV="$work/gate/cursor.argv" \
    SEAT_USAGE_RUNS="$work/gate/runs.jsonl" \
    bash "$CONCLAVE" "$@"
}

rm -f "$work/gate/claude.ran"
gate_ec=0
run_gate "$GATE_ID" --repo "$work/target" >"$work/gate/out" 2>"$work/gate/err" || gate_ec=$?
if [ "$gate_ec" -ne 0 ] || [ ! -s "$GATE_DIR/judgment.md" ]; then
  printf 'gate out:\n' >&2
  cat "$work/gate/out" >&2 || true
  printf 'gate err:\n' >&2
  cat "$work/gate/err" >&2 || true
  printf 'errors.md:\n' >&2
  cat "$GATE_DIR/errors.md" >&2 || true
  printf 'jev log:\n' >&2
  cat "$GATE_DIR/jev-judge.log" >&2 || true
fi
check "stubbed run exits 0" [ "$gate_ec" -eq 0 ]
check "stubbed claude CLI was not launched" [ ! -f "$work/gate/claude.ran" ]
check "stubbed fable leg called cursor-agent" [ -f "$work/gate/cursor.ran" ]
check "cursor-agent argv names the fable id" grep -qx claude-fable-9-9-thinking-xhigh "$work/gate/cursor.argv"
check "seat-usage ask was the route" grep -q -- '--route lineup' "$work/gate/seat.log"
check "stubbed run writes judgment.md" [ -s "$GATE_DIR/judgment.md" ]
check "errors.md names the missing voter" grep -q 'named voter missing: claude' "$GATE_DIR/errors.md"
check "judgment header names the missing voter" grep -qx 'missing_voters: claude' "$GATE_DIR/judgment.md"
check "voters.txt still names claude" grep -qx 'claude,codex,argon' "$GATE_DIR/voters.txt"
check "stubbed run called Jev" [ "$(tr -d '[:space:]' <"$work/gate/jev.count")" -ge 1 ]
jev_after_run="$(tr -d '[:space:]' <"$work/gate/jev.count")"
rm -f "$GATE_DIR/judgment.md" "$GATE_DIR/options.md"
gate_ec=0
run_gate "$GATE_ID" --judge >"$work/gate/judge.out" 2>"$work/gate/judge.err" || gate_ec=$?
if [ "$gate_ec" -ne 0 ] || [ ! -s "$GATE_DIR/judgment.md" ]; then
  printf 'judge out:\n' >&2
  cat "$work/gate/judge.out" >&2 || true
  printf 'judge err:\n' >&2
  cat "$work/gate/judge.err" >&2 || true
  printf 'errors.md:\n' >&2
  cat "$GATE_DIR/errors.md" >&2 || true
  printf 'jev log:\n' >&2
  cat "$GATE_DIR/jev-judge.log" >&2 || true
fi
check "--judge exits 0 with a missing named voter" [ "$gate_ec" -eq 0 ]
check "--judge writes judgment.md" [ -s "$GATE_DIR/judgment.md" ]
check "--judge header names the missing voter" grep -qx 'missing_voters: claude' "$GATE_DIR/judgment.md"
check "--judge called Jev" [ "$(tr -d '[:space:]' <"$work/gate/jev.count")" -gt "$jev_after_run" ]
kill "$JEV_PID" 2>/dev/null || true
JEV_PID=""
rm -rf "$GATE_DIR"
GATE_DIR=""

printf '%s passed, %s failed\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
