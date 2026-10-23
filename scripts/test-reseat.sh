#!/usr/bin/env bash
# A failed review voter is replaced once from seat-usage. No live model call.
# The stub router is the only seat-usage. A fake launch writes the report or the fail file.
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
mkdir -p "$work/bin" "$work/scratch"
export PATH="$work/bin:$PATH"
SEAT_LOG="$work/seat.log"
SEAT_JSON="$work/route.json"
export SEAT_LOG SEAT_JSON
: >"$SEAT_LOG"

cat >"$work/bin/seat-usage" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$SEAT_LOG"
case " $* " in
  *" --record-failure "*)
    if [ "${SEAT_RECORD_RC:-0}" != 0 ]; then
      exit "$SEAT_RECORD_RC"
    fi
    exit 0
    ;;
esac
if [ "${SEAT_ROUTE_RC:-0}" != 0 ]; then
  exit "$SEAT_ROUTE_RC"
fi
cat "$SEAT_JSON"
exit 0
EOF
cat >"$work/bin/grok" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$work/bin/seat-usage" "$work/bin/grok"

# The real leg launch is not what this test is measuring.
review_make_leg() {
  mkdir -p "$DIR/wt"
  printf '%s\n' "$DIR/wt"
}
review_run_grok() {
  if [ "${FAKE_GROK_FAIL:-0}" = 1 ]; then
    echo "grok: exit 1" >"$DIR/voter-grok.fail"
    return 1
  fi
  echo "grok report" >"$DIR/voter-grok-report.md"
  echo "grok: ok model_id=grok-test turn=completed" >"$DIR/voter-grok.model.txt"
  return 0
}
review_run_argon() {
  if [ "${FAKE_ARGON_FAIL:-0}" = 1 ]; then
    printf 'argon: skipped\nnot signed in\n' >"$DIR/voter-argon.skipped"
    return 1
  fi
  echo "argon report" >"$DIR/voter-argon.md"
  echo "argon: ok model=gemini-4-argon-high" >"$DIR/voter-argon.model.txt"
  return 0
}
review_write_muse_brief() {
  printf 'brief\n' >"$1"
}
review_write_claude_brief() {
  printf 'brief\n' >"$1"
}

reset_case() {
  DIR="$work/out-$1"
  rm -rf "$DIR"
  mkdir -p "$DIR" "$work/scratch"
  ERRORS_FILE="$DIR/errors.md"
  : >"$ERRORS_FILE"
  : >"$SEAT_LOG"
  SCRATCH="$work/scratch"
  REVIEW_LEG_REF="refs/heads/test"
  REVIEW_LEG_BASE="refs/heads/base"
  KIND=review
  VOTERS_EXPLICIT=0
  VOTERS="claude,codex"
  CLAUDE_SKU="fable"
  HEADER_AUTHOR=""
  ID="reseat-$1"
  REPO=""
  FAKE_GROK_FAIL=0
  FAKE_ARGON_FAIL=0
  SEAT_RECORD_RC=0
  SEAT_ROUTE_RC=0
  export FAKE_GROK_FAIL FAKE_ARGON_FAIL SEAT_RECORD_RC SEAT_ROUTE_RC
  export CONCLAVE_SEAT_USAGE="$work/bin/seat-usage"
  echo "codex report" >"$DIR/voter-codex.md"
  echo "codex: ok model=gpt-6-astra" >"$DIR/voter-codex.model.txt"
  printf '%s\n' '# Review lineup' 'voters: claude,codex' >"$DIR/lineup.md"
}

route_calls() {
  grep -c -- '--route' "$SEAT_LOG" || true
}

# 1. Non-zero exit. The working voter is OpenAI, so the replacement is Grok.
reset_case nonzero
echo "claude: exit 7" >"$DIR/voter-claude.fail"
cat >"$SEAT_JSON" <<'EOF'
{"mode":"default","role":"lineup","kind":"review","why":"ranked by pace then headroom","lineup":{"voters":[
  {"family":"grok","seat":"grok","vendor":"xai","model":"grok"},
  {"family":"astra","seat":"chatgpt","vendor":"openai","model":"gpt-6-astra"},
  {"family":"argon","seat":"google","vendor":"google","model":"gemini-4-argon-high"}
]}}
EOF
review_reseat_failed
grep -q 're-seat: claude failed (nonzero); replaced with grok' "$DIR/reseat.md" \
  || fail "nonzero: $(cat "$DIR/reseat.md" 2>/dev/null)"
grep -q 're-seat: grok produced a report' "$DIR/reseat.md" || fail "nonzero: no report note"
[ "$VOTERS" = "codex,grok" ] || fail "nonzero: voters=$VOTERS"
grep -q -- '--record-failure claude --reason nonzero' "$SEAT_LOG" || fail "nonzero: record: $(cat "$SEAT_LOG")"
review_report_present grok || fail "nonzero: grok report not counted"
review_report_present claude && fail "nonzero: failed claude still counted"
printf 'ok non-zero failure re-seats grok\n'

# 2. 429 / usage credits. Cursor Fable records the cursor seat.
reset_case credits
CLAUDE_SKU="cursor-fable"
echo "claude: Cursor exit 1" >"$DIR/voter-claude.fail"
echo "429 Fable 5.1 requires usage credits" >"$DIR/voter-claude.err"
cat >"$SEAT_JSON" <<'EOF'
{"mode":"default","why":"ranked","lineup":{"voters":[
  {"family":"argon","seat":"google","vendor":"google","model":"gemini-4-argon-high"},
  {"family":"astra","seat":"chatgpt","vendor":"openai","model":"gpt-6-astra"}
]}}
EOF
review_reseat_failed
grep -q 're-seat: claude failed (credits); replaced with argon' "$DIR/reseat.md" \
  || fail "credits: $(cat "$DIR/reseat.md")"
grep -q -- '--record-failure cursor --reason credits' "$SEAT_LOG" || fail "credits: record: $(cat "$SEAT_LOG")"
[ "$VOTERS" = "codex,argon" ] || fail "credits: voters=$VOTERS"
review_report_present argon || fail "credits: argon report not counted"
printf 'ok 429/credits re-seats argon and records the cursor seat\n'

# 3. Max-turns is a failure, not a finished review.
reset_case turns
VOTERS="grok,codex"
echo "grok: no report" >"$DIR/voter-grok.fail"
echo "The reviewer hit its turn limit and wrote no review file." >"$DIR/voter-grok.stdout.md"
cat >"$SEAT_JSON" <<'EOF'
{"mode":"default","why":"ranked","lineup":{"voters":[
  {"family":"argon","seat":"google","vendor":"google","model":"gemini-4-argon-high"},
  {"family":"astra","seat":"chatgpt","vendor":"openai","model":"gpt-6-astra"}
]}}
EOF
review_reseat_failed
grep -q 're-seat: grok failed (max_turns); replaced with argon' "$DIR/reseat.md" \
  || fail "turns: $(cat "$DIR/reseat.md")"
grep -q -- '--record-failure grok --reason max_turns' "$SEAT_LOG" || fail "turns: record: $(cat "$SEAT_LOG")"
printf 'ok max-turns re-seats the next seat\n'

# 4. Vendor constraint: the only offered seat shares the builder or the other voter.
reset_case vendor
HEADER_AUTHOR="fable"
echo "grok: exit 3" >"$DIR/voter-grok.fail"
VOTERS="grok,codex"
cat >"$SEAT_JSON" <<'EOF'
{"mode":"default","why":"ranked by pace then headroom","lineup":{"voters":[
  {"family":"fable","seat":"claude","vendor":"anthropic","model":"fable"},
  {"family":"astra","seat":"chatgpt","vendor":"openai","model":"gpt-6-astra"}
]}}
EOF
review_reseat_failed
grep -q 'could not replace grok (nonzero)' "$DIR/reseat.md" || fail "vendor: $(cat "$DIR/reseat.md")"
grep -q 'vendor must differ' "$DIR/reseat.md" || fail "vendor reason: $(cat "$DIR/reseat.md")"
[ "$VOTERS" = "grok,codex" ] || fail "vendor: voters changed to $VOTERS"
printf 'ok re-seat refuses the builder and the other voter\n'

# 5. No eligible replacement is a clear report, not a silent shorter council.
reset_case none
echo "claude: exit 2" >"$DIR/voter-claude.fail"
cat >"$SEAT_JSON" <<'EOF'
{"mode":"ask","reason":"need 2 review seat(s) outside xAI; 0 eligible: ask Ryan","skipped":[
  {"family":"wonder","code":"stale","reason":"Muse 5h reading 365m old: ask Ryan"}
]}
EOF
review_reseat_failed
grep -q 'could not replace claude (nonzero)' "$DIR/reseat.md" || fail "none: $(cat "$DIR/reseat.md")"
grep -q 'wonder:stale' "$DIR/reseat.md" || fail "none codes: $(cat "$DIR/reseat.md")"
grep -q 'no eligible replacement' "$DIR/reseat.md" || fail "none wording: $(cat "$DIR/reseat.md")"
[ "$VOTERS" = "claude,codex" ] || fail "none: voters=$VOTERS"
printf 'ok no eligible replacement is reported\n'

# 6. The replacement fails. It is not replaced again, and the route runs once.
reset_case once
FAKE_GROK_FAIL=1
echo "claude: exit 9" >"$DIR/voter-claude.fail"
cat >"$SEAT_JSON" <<'EOF'
{"mode":"default","why":"ranked","lineup":{"voters":[
  {"family":"grok","seat":"grok","vendor":"xai","model":"grok"},
  {"family":"astra","seat":"chatgpt","vendor":"openai","model":"gpt-6-astra"}
]}}
EOF
review_reseat_failed
[ "$(route_calls)" = 1 ] || fail "once: route calls=$(route_calls) log=$(cat "$SEAT_LOG")"
grep -q 'replaced with grok' "$DIR/reseat.md" || fail "once: $(cat "$DIR/reseat.md")"
grep -q 'not replacing again' "$DIR/reseat.md" || fail "once: no stop: $(cat "$DIR/reseat.md")"
n_replaced="$(grep -c 'replaced with' "$DIR/reseat.md")"
[ "$n_replaced" = 1 ] || fail "once: replacements=$n_replaced"
printf 'ok a failed replacement is not replaced again\n'

# An older seat-usage that rejects --record-failure does not stop the re-seat.
reset_case soft
SEAT_RECORD_RC=2
echo "claude: exit 4" >"$DIR/voter-claude.fail"
cat >"$SEAT_JSON" <<'EOF'
{"mode":"default","why":"ranked","lineup":{"voters":[
  {"family":"grok","seat":"grok","vendor":"xai","model":"grok"},
  {"family":"astra","seat":"chatgpt","vendor":"openai","model":"gpt-6-astra"}
]}}
EOF
review_reseat_failed
grep -q 'replaced with grok' "$DIR/reseat.md" || fail "soft: $(cat "$DIR/reseat.md")"
printf 'ok record-failure failure is soft\n'

# --voters is not rewritten.
reset_case explicit
VOTERS_EXPLICIT=1
echo "claude: exit 1" >"$DIR/voter-claude.fail"
review_reseat_failed
[ ! -s "$SEAT_LOG" ] || fail "explicit: seat-usage was called: $(cat "$SEAT_LOG")"
[ "$VOTERS" = "claude,codex" ] || fail "explicit: voters=$VOTERS"
printf 'ok explicit --voters is not re-seated\n'

# Both original voters failed. The route lists the other failure first.
# That voter must not be launched again, and the first failure must not come back.
reset_case both
rm -f "$DIR/voter-codex.md" "$DIR/voter-codex.model.txt"
echo "claude: exit 7" >"$DIR/voter-claude.fail"
echo "codex: exit 8" >"$DIR/voter-codex.fail"
cat >"$SEAT_JSON" <<'EOF'
{"mode":"default","why":"ranked","lineup":{"voters":[
  {"family":"astra","seat":"chatgpt","vendor":"openai","model":"gpt-6-astra"},
  {"family":"grok","seat":"grok","vendor":"xai","model":"grok"},
  {"family":"argon","seat":"google","vendor":"google","model":"gemini-4-argon-high"}
]}}
EOF
review_reseat_failed
grep -q 'replaced with grok' "$DIR/reseat.md" || fail "both: $(cat "$DIR/reseat.md")"
grep -q 'replaced with argon' "$DIR/reseat.md" || fail "both second: $(cat "$DIR/reseat.md")"
grep -q 'replaced with codex\|replaced with claude' "$DIR/reseat.md" && fail "both: reseated a failure: $(cat "$DIR/reseat.md")"
[ "$VOTERS" = "grok,argon" ] || fail "both: voters=$VOTERS"
grep -q -- '--exclude-seat claude' "$SEAT_LOG" || fail "both: exclude claude: $(cat "$SEAT_LOG")"
grep -q -- '--exclude-seat chatgpt' "$SEAT_LOG" || fail "both: exclude chatgpt: $(cat "$SEAT_LOG")"
printf 'ok both failures are excluded before either is replaced\n'

# A seated Argon that is not signed in is an auth failure, not a skipped optional leg.
reset_case auth
VOTERS="argon,codex"
printf 'argon: skipped\nnot signed in\n' >"$DIR/voter-argon.skipped"
cat >"$SEAT_JSON" <<'EOF'
{"mode":"default","why":"ranked","lineup":{"voters":[
  {"family":"grok","seat":"grok","vendor":"xai","model":"grok"},
  {"family":"astra","seat":"chatgpt","vendor":"openai","model":"gpt-6-astra"}
]}}
EOF
review_reseat_failed
grep -q 're-seat: argon failed (auth); replaced with grok' "$DIR/reseat.md" \
  || fail "auth: $(cat "$DIR/reseat.md")"
grep -q -- '--record-failure google --reason auth' "$SEAT_LOG" || fail "auth record: $(cat "$SEAT_LOG")"
[ "$VOTERS" = "codex,grok" ] || fail "auth: voters=$VOTERS"
printf 'ok argon not-signed-in re-seats and records auth\n'

# A route row with no vendor still cannot be the builder's family.
vendor_json='{"mode":"default","lineup":{"voters":[{"family":"fable","seat":"claude"},{"family":"grok","seat":"grok","vendor":"xai"}]}}'
picked="$(python3 "$ROOT/scripts/lineup.py" --replacement codex anthropic - - "$vendor_json")"
[ "$(printf '%s\n' "$picked" | sed -n '1p')" = ok ] || fail "vendorless: $picked"
[ "$(printf '%s\n' "$picked" | sed -n '2p')" = grok ] || fail "vendorless returned: $picked"
only='{"mode":"default","lineup":{"voters":[{"family":"fable","seat":"claude"}]}}'
none_picked="$(python3 "$ROOT/scripts/lineup.py" --replacement codex anthropic - - "$only")"
[ "$(printf '%s\n' "$none_picked" | sed -n '1p')" = none ] || fail "vendorless only: $none_picked"
printf 'ok a missing vendor does not bypass the builder\n'

# A policy skip is not a failed voter. A replacement Argon that is not signed
# in stays in the required count.
reset_case policy
VOTERS="claude,codex,argon"
printf 'argon: skipped\nargon: kind review seats this leg when the lineup or --voters names it\n' \
  >"$DIR/voter-argon.skipped"
echo "claude: exit 7" >"$DIR/voter-claude.fail"
cat >"$SEAT_JSON" <<'EOF'
{"mode":"default","why":"ranked","lineup":{"voters":[
  {"family":"grok","seat":"grok","vendor":"xai","model":"grok"}
]}}
EOF
review_reseat_failed
grep -q 'replaced with grok' "$DIR/reseat.md" || fail "policy: $(cat "$DIR/reseat.md")"
grep -q 'argon failed' "$DIR/reseat.md" && fail "policy reseated argon: $(cat "$DIR/reseat.md")"
grep -q -- '--record-failure google' "$SEAT_LOG" && fail "policy recorded argon: $(cat "$SEAT_LOG")"
printf 'ok a policy-skipped argon is not re-seated\n'

reset_case argonfail
FAKE_ARGON_FAIL=1
echo "claude: exit 7" >"$DIR/voter-claude.fail"
cat >"$SEAT_JSON" <<'EOF'
{"mode":"default","why":"ranked","lineup":{"voters":[
  {"family":"argon","seat":"google","vendor":"google","model":"gemini-4-argon-high"}
]}}
EOF
review_reseat_failed
grep -q 'replaced with argon' "$DIR/reseat.md" || fail "argonfail: $(cat "$DIR/reseat.md")"
grep -q 'also failed (auth)' "$DIR/reseat.md" || fail "argonfail reason: $(cat "$DIR/reseat.md")"
[ "$VOTERS" = "codex,argon" ] || fail "argonfail: voters=$VOTERS"
review_count_reports
[ "$REVIEW_REPORTS_EXPECTED" = 2 ] || fail "argonfail expected=$REVIEW_REPORTS_EXPECTED"
[ "$REVIEW_REPORTS_PRESENT" = 1 ] || fail "argonfail present=$REVIEW_REPORTS_PRESENT"
printf '%s\n' "$REVIEW_REPORTS_MISSING" | grep -q argon || fail "argonfail missing=$REVIEW_REPORTS_MISSING"
printf 'ok a failed replacement argon stays in the required count\n'

printf 'ok re-seat\n'
