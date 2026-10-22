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

printf '%s passed, %s failed\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
