#!/usr/bin/env bash
# Ask mode: one prompt to each named voter, answers side by side and blinded.
# No live model call. Fake claude, codex, grok, and cursor-agent sit on PATH,
# and HOME is a temp dir holding a Codex model cache.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
CONCLAVE="$ROOT/scripts/conclave.sh"
ID="test-ask-mode-$$"
DIR="$ROOT/tmp/council/$ID"

work="$(mktemp -d)"
trap 'rm -rf "$work" "$DIR"' EXIT
mkdir -p "$work/bin" "$work/home/.codex" "$work/log"

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

cat >"$work/home/.codex/models_cache.json" <<'EOF'
{"models": [{"slug": "gpt-test-astra", "visibility": "list", "priority": 1}]}
EOF

cat >"$work/bin/cursor-agent" <<'EOF'
#!/bin/sh
exit 0
EOF

# Each fake saves its argv (one arg per line) and its prompt under FAKE_LOG.
cat >"$work/bin/claude" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" >"$FAKE_LOG/claude.argv"
pwd >"$FAKE_LOG/claude.cwd"
env | grep -E '^(TYPESAFE_API_KEY|JEV_API_KEY)=' >"$FAKE_LOG/claude.env" || true
cat >"$FAKE_LOG/claude.prompt"
printf 'Claude answer: CLEAN\n'
EOF

cat >"$work/bin/codex" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then echo "codex-cli 0.200.0"; exit 0; fi
printf '%s\n' "$@" >"$FAKE_LOG/codex.argv"
env | grep -E '^(TYPESAFE_API_KEY|JEV_API_KEY)=' >"$FAKE_LOG/codex.env" || true
cat >"$FAKE_LOG/codex.prompt"
out=""
while [ "$#" -gt 0 ]; do
  case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac
done
printf 'Astra answer: CLEAN\n' >"$out"
EOF

# FAKE_GROK_END is the outcome of the session's last turn_ended.
cat >"$work/bin/grok" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "models" ]; then echo "Default model: grok-test-1"; exit 0; fi
printf '%s\n' "$@" >"$FAKE_LOG/grok.argv"
env | grep -E '^(TYPESAFE_API_KEY|JEV_API_KEY)=' >"$FAKE_LOG/grok.env" || true
cwd="" pf=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --cwd) cwd="$2"; shift 2 ;;
    --prompt-file) pf="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cp "$pf" "$FAKE_LOG/grok.prompt"
sess="$CONCLAVE_GROK_SESSIONS/$(printf '%s' "$cwd" | sed 's|/|%2F|g')/01a1-fake"
mkdir -p "$sess"
printf '{"type":"turn_ended","outcome":"%s"}\n' "${FAKE_GROK_END:-completed}" >"$sess/events.jsonl"
printf 'Grok answer: CLEAN\n'
EOF
cat >"$work/bin/muse" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" >"$FAKE_LOG/muse.argv"
env | grep -E '^(TYPESAFE_API_KEY|JEV_API_KEY)=' >"$FAKE_LOG/muse.env" || true
pf=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --prompt-file) pf="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cp "$pf" "$FAKE_LOG/muse.prompt"
printf '%s\n' "{\"payload_type\":\"run.model.configured\",\"payload\":{\"model_id\":\"${FAKE_MUSE_MODEL}\"}}"
printf '%s\n' "{\"payload_type\":\"run.output.delta\",\"payload\":{\"text\":\"Muse answer: CLEAN\n\"}}"
EOF

cat >"$work/bin/seat-usage" <<'EOF'
#!/bin/sh
cat "$SEAT_STUB_JSON"
EOF

cat >"$work/muse-catalog.json" <<'EOF'
{"models":[
  {"modelId":"muse-spark-1.3","releaseDate":"2026-11-01"},
  {"modelId":"muse-wonder-1.0","releaseDate":"2026-10-12"},
  {"modelId":"muse-wonder-full-1.0","releaseDate":"2026-10-12"}
]}
EOF

cat >"$work/lean-low.json" <<'EOF'
{"model":"canned","answers":{"effort":{"choice":"low","confidence":0.8}}}
EOF
cat >"$work/lean-medium.json" <<'EOF'
{"model":"canned","answers":{"effort":{"choice":"medium","confidence":0.8}}}
EOF
cat >"$work/lean-bad.json" <<'EOF'
{"model":"canned","answers":{"effort":{"choice":"nope","confidence":0.2}}}
EOF

row() { # family pace mode effort
  printf '{"family":"%s","effort":"%s","pace":"%s","mode":"%s","vendor":"v","seat":"%s"}' \
    "$1" "$4" "$2" "$3" "$1"
}
cat >"$work/route-on.json" <<EOF
[{"role":"ask","mode":"default","reason":"Fable and Astra by headroom","voters":[
  $(row fable on default high),$(row astra on default high)
],"candidates":[
  $(row fable on default high),$(row astra on default high),$(row grok on default high),$(row muse under default high)
]}]
EOF
cat >"$work/route-ahead.json" <<EOF
[{"role":"ask","mode":"default","reason":"Fable and Astra by headroom","voters":[
  $(row fable on default high),$(row astra on default high)
],"candidates":[
  $(row fable on default high),$(row astra on default high),$(row grok ahead suggest low),$(row muse under burn xhigh)
]}]
EOF

chmod +x "$work/bin/"*

printf 'One comment. It reads fine.\n' >"$work/comments.md"

run() { # runs conclave.sh with the fakes; stdout+stderr to $work/out, exit code to $work/ec
  local ec=0 seat="$work/bin/seat-usage"
  if [ "${SEAT_STUB_OFF:-}" = 1 ]; then
    seat="$work/missing-seat-usage"
  fi
  env -u CONCLAVE_CODEX_BIN -u COUNCIL_CODEX_BIN \
    HOME="$work/home" PATH="$work/bin:$PATH" \
    CONCLAVE_CODEX_BIN="$work/bin/codex" \
    CONCLAVE_SKIP_UPDATE_CHECK=1 \
    CONCLAVE_GROK_SESSIONS="$work/sessions" \
    CONCLAVE_SEAT_USAGE="$seat" \
    CONCLAVE_MUSE_CATALOG="$work/muse-catalog.json" \
    SEAT_USAGE_RUNS="$work/runs.jsonl" \
    SEAT_STUB_JSON="${SEAT_STUB_JSON:-$work/route-on.json}" \
    MUSE_USAGE_PATH="$work/muse-usage.json" \
    FAKE_MUSE_MODEL="muse-wonder-1.0" \
    FAKE_LOG="$work/log" \
    TYPESAFE_API_KEY=secret-should-not-leak \
    "$@" --canned "${LEAN_CANNED:-$work/lean-low.json}" >"$work/out" 2>&1 || ec=$?
  printf '%s' "$ec" >"$work/ec"
}
ec() { [ "$(cat "$work/ec")" = "$1" ]; }
has() { grep -qF -- "$2" "$1"; }
hasnt() { ! grep -qF -- "$2" "$1"; }
line() { grep -qxF -- "$2" "$1"; }

# 1. Template with its default ballot (codex, grok). No brief exists.
run "$CONCLAVE" "$ID" --template tell-scan --input "$work/comments.md" --ask "Skip the heading lines."
OUT="$DIR/ask-tell-scan"
check "template run exits 0" ec 0
check "template run needs no brief" test ! -e "$DIR/brief.md"
check "template default ballot skips claude" test ! -e "$work/log/claude.argv"
check "prompt carries the template" has "$OUT/PROMPT.md" "Quote the exact span. Never paraphrase it."
check "prompt drops the voters: line" hasnt "$OUT/PROMPT.md" "voters:"
check "prompt carries the chair note" has "$OUT/PROMPT.md" "Skip the heading lines."
check "prompt inlines the input" has "$OUT/PROMPT.md" "One comment. It reads fine."
check "prompt marks the input as data" has "$OUT/PROMPT.md" "=== INPUT 1: comments.md ==="
check "codex got the same prompt" cmp -s "$OUT/PROMPT.md" "$work/log/codex.prompt"
check "grok got the same prompt" cmp -s "$OUT/PROMPT.md" "$work/log/grok.prompt"
check "codex model from the resolver" line "$work/log/codex.argv" "gpt-test-astra"
check "codex read-only sandbox" line "$work/log/codex.argv" "read-only"
check "codex effort defaults to low" line "$work/log/codex.argv" 'model_reasoning_effort="low"'
check "grok effort defaults to low" line "$work/log/grok.argv" "low"
check "grok has no tools" line "$work/log/grok.argv" ""
check "grok model from the resolver" line "$work/log/grok.argv" "grok-test-1"
check "answers side by side" has "$OUT/answers.md" "Astra answer: CLEAN"
check "answers include grok" has "$OUT/answers.md" "Grok answer: CLEAN"
check "two blinded legs" test "$(wc -l <"$OUT/blind/key.txt")" -eq 2
check "blinded legs redact vendor names" sh -c '! grep -qiE "astra|grok" "$1"/blind/leg-*.md' _ "$OUT"
check "errors.md says none" line "$OUT/errors.md" "(none)"
check "ask records the template efforts" python3 -c '
import json,sys
rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
ran={v["name"]: v["effort"] for v in rows[-1]["ran"]["voters"]}
sys.exit(0 if rows[-1]["kind"]=="ask" and ran=={"codex":"low","grok":"low"} else 1)
' "$work/runs.jsonl"

# 2. Grok exits 0 but its turn was cancelled: no answer, exit 1.
rm -rf "$work/log/"* "$work/sessions"
run env FAKE_GROK_END=cancelled "$CONCLAVE" "$ID" --template tell-scan --input "$work/comments.md"
check "cancelled grok turn exits 1" ec 1
check "cancelled grok answer is not kept" test ! -e "$OUT/answer-grok.md"
check "cancelled grok is named in errors.md" has "$OUT/errors.md" "last turn_ended is cancelled"
check "answers.md marks the missing leg" has "$OUT/answers.md" "(no answer; see errors.md)"
check "codex still answered" has "$OUT/answers.md" "Astra answer: CLEAN"

# 3. A plain question, explicit voters and effort.
rm -rf "$work/log/"* "$work/sessions"
run "$CONCLAVE" "$ID" --ask "Is this one task or two?" --voters claude,codex --effort high
QOUT="$DIR/ask-question"
check "question run exits 0" ec 0
check "question prompt" has "$QOUT/PROMPT.md" "Is this one task or two?"
check "claude effort high" line "$work/log/claude.argv" "high"
check "claude model is fable" line "$work/log/claude.argv" "fable"
check "claude has no tools" line "$work/log/claude.argv" ""
check "claude cwd is outside the repo" sh -c 'case "$(cat "$1")" in "$2"*) exit 1 ;; esac' _ "$work/log/claude.cwd" "$ROOT"
check "claude got the prompt" cmp -s "$QOUT/PROMPT.md" "$work/log/claude.prompt"
check "codex effort high" line "$work/log/codex.argv" 'model_reasoning_effort="high"'
check "grok not launched" test ! -e "$work/log/grok.argv"
check "Jev key not in any prompt" sh -c '! grep -rq secret-should-not-leak "$1"' _ "$work/log"

# 4. Dry-run writes the prompt under preview/ only and launches nothing.
rm -rf "$work/log/"* "$DIR"
run "$CONCLAVE" "$ID" --template cold-read --input "$work/comments.md" --dry-run
check "dry-run exits 0" ec 0
check "dry-run prompt under preview" has "$DIR/preview/ask-cold-read/PROMPT.md" "What did you have to re-read"
check "dry-run writes no answers dir" test ! -e "$DIR/ask-cold-read"
check "dry-run launches no voter" test -z "$(ls "$work/log")"

# 5. Rejections.
run "$CONCLAVE" "$ID" --template tell-scan
check "template without input fails" ec 1
check "template without input says why" has "$work/out" "needs --input"
run "$CONCLAVE" "$ID" --template nope --input "$work/comments.md"
check "unknown template names the real ones" has "$work/out" "have: cold-read, tell-scan"
run "$CONCLAVE" "$ID" --ask "q" --kind design
check "ask rejects ballot flags" has "$work/out" "ask mode takes only"
rm -rf "$work/log/"*
run "$CONCLAVE" "$ID" --ask "q" --voters claude,muse
check "ask accepts muse" ec 0
check "muse launch is tool-less" line "$work/log/muse.argv" "--disable-shell"
check "muse contributor mode is off" line "$work/log/muse.argv" "off"
check "muse effort follows the lean" line "$work/log/muse.argv" "low"
check "muse answer is kept" has "$DIR/ask-question/answers.md" "Muse answer: CLEAN"
check "Jev key not in muse env" sh -c '! grep -q secret-should-not-leak "$1"' _ "$work/log/muse.env"
run "$CONCLAVE" "$ID" --ask "q" --ask-file "$work/comments.md"
check "ask and ask-file are exclusive" has "$work/out" "cannot be combined"
run "$CONCLAVE" "$ID" --input "$work/comments.md"
check "input without ask fails" has "$work/out" "ask mode only"
run "$CONCLAVE" "$ID" --ask "q" --input "$work/missing.md"
check "missing input fails" has "$work/out" "not a regular file"
run "$CONCLAVE" "$ID" --ask "q" --effort max
check "effort max is refused" has "$work/out" "--effort wants low, med, or high"

# 6. A plain question takes the router's two voters. Lean low, pace on, stays low.
rm -rf "$work/log/"* "$DIR"
run "$CONCLAVE" "$ID" --ask "What is one task?"
check "routed question exits 0" ec 0
check "routed question launches claude" test -e "$work/log/claude.argv"
check "routed question launches codex" test -e "$work/log/codex.argv"
check "routed question skips grok" test ! -e "$work/log/grok.argv"
check "routed question skips muse" test ! -e "$work/log/muse.argv"

# 7. Pace steps the lean. Ahead goes down. Burn goes up. The router effort is not reused.
rm -rf "$work/log/"*
SEAT_STUB_JSON="$work/route-ahead.json" LEAN_CANNED="$work/lean-medium.json" \
  run "$CONCLAVE" "$ID" --template tell-scan --input "$work/comments.md"
check "ahead pool steps the lean down" line "$work/log/grok.argv" "low"
check "on-pace pool keeps the lean" line "$work/log/codex.argv" 'model_reasoning_effort="medium"'
rm -rf "$work/log/"*
SEAT_STUB_JSON="$work/route-ahead.json" LEAN_CANNED="$work/lean-medium.json" \
  run "$CONCLAVE" "$ID" --ask "q" --voters muse
check "a burning pool steps the lean up" line "$work/log/muse.argv" "high"
check "a burning pool does not reuse xhigh" sh -c '! grep -qx xhigh "$1"' _ "$work/log/muse.argv"

# 8. A bad lean uses the router effort and says so. A missing router keeps the lean.
rm -rf "$work/log/"*
LEAN_CANNED="$work/lean-bad.json" run "$CONCLAVE" "$ID" --template tell-scan --input "$work/comments.md"
check "failed lean uses the router effort" line "$work/log/codex.argv" 'model_reasoning_effort="high"'
check "failed lean says it used the router" has "$work/out" "effort lean unavailable; using the router"
rm -rf "$work/log/"*
SEAT_STUB_OFF=1 LEAN_CANNED="$work/lean-medium.json" \
  run "$CONCLAVE" "$ID" --ask "q" --voters codex
check "missing pace keeps the lean" line "$work/log/codex.argv" 'model_reasoning_effort="medium"'
check "missing pace says so" has "$work/out" "pace reading missing"

# 9. Dry-run does not append an outcome line.
before="$(wc -l <"$work/runs.jsonl")"
rm -rf "$work/log/"* "$DIR"
run "$CONCLAVE" "$ID" --template cold-read --input "$work/comments.md" --dry-run
after="$(wc -l <"$work/runs.jsonl")"
check "dry-run records nothing" test "$before" = "$after"

printf '%s passed, %s failed\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
