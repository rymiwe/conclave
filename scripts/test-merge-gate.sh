#!/usr/bin/env bash
# merge-gate.sh review + merge against a temp repo with stubbed gh, seat-usage
# and every reviewer binary (no network, no quota): the gate runs the reviewer
# seat-usage names, read-only; a normal diff passes and gh pr merge runs; one_way:
# yes without a valid one-way ack, a one-way label, a missing or garbled verdict, and unreadable labels
# are refused; prose that only mentions one_way is not a verdict.
set -u
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
fail=0
ok() { echo "ok $1"; }
bad() { echo "FAIL $1"; fail=1; }
has() { case "$2" in *"$3"*) ok "$1";; *) bad "$1 (missing '$3' in: $2)";; esac; }
hasnt() { case "$2" in *"$3"*) bad "$1 (unexpected '$3' in: $2)";; *) ok "$1";; esac; }

mkdir -p "$T/bin"
cat > "$T/bin/gh" <<'G'
#!/usr/bin/env bash
echo "gh $*" >> "$FAKE_GH_LOG"
case "$1 $2 $*" in
  "pr view "*"--json commits"*) [ -n "${FAKE_COMMITS_FAIL:-}" ] && { echo "HTTP 401" >&2; exit 1; }
    # A default inside ${var:-...} swallows the JSON's closing brace.
    if [ -n "${FAKE_COMMITS+x}" ]; then printf '%s\n' "$FAKE_COMMITS"; else printf '%s\n' '{"commits":[]}'; fi ;;
  "pr view "*"--json labels"*) [ -n "${FAKE_GH_LABELS_FAIL:-}" ] && { echo "HTTP 401" >&2; exit 1; }
    printf '{"labels":[%s]}\n' "${FAKE_LABELS:-}";;
  "pr view "*"--json headRefOid"*) printf '%s\n' "${FAKE_PR_HEAD:-}";;
  "pr view "*) echo "Title: t"; echo; echo "body";;
  "pr merge "*) # FAKE_PR_HEAD is the PR's current head; a different --match-head-commit fails as GitHub does.
    m=""; prev=""; for a in "$@"; do [ "$prev" = --match-head-commit ] && m=$a; prev=$a; done
    if [ -n "${FAKE_PR_HEAD:-}" ] && [ "$m" != "$FAKE_PR_HEAD" ]; then
      echo "GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest)" >&2; exit 1
    fi
    echo "merged";;
  *) echo "unexpected gh $*" >&2; exit 9;;
esac
G
# Every reviewer stub logs "<name> <argv>" to $FAKE_RUN_LOG (a multi-line
# argument, the prompt, is logged as PROMPT) and answers with $FAKE_REPLY.
cat > "$T/bin/logargv" <<'L'
#!/usr/bin/env bash
line=$1; shift
for a in "$@"; do case "$a" in *$'\n'*) a=PROMPT;; esac; line="$line $a"; done
printf '%s\n' "$line" >> "$FAKE_RUN_LOG"
L
cat > "$T/bin/agy" <<'A'
#!/usr/bin/env bash
"$(dirname "$0")/logargv" agy "$@"
cat > "$FAKE_STDIN"
jq -cn --rawfile r "$FAKE_REPLY" '{event:"init",init:{agent:"conclave-voter"}},{event:"result",result:{status:"SUCCESS",response:$r}}'
A
cat > "$T/bin/cursor-agent" <<'A'
#!/usr/bin/env bash
if [ "$1" = models ]; then
  echo "cursor-agent models" >> "$FAKE_RUN_LOG"
  printf '%s\n' "${FAKE_CURSOR_MODELS:-}"; exit 0
fi
"$(dirname "$0")/logargv" cursor-agent "$@"
for a in "$@"; do last=$a; done
printf '%s\n' "$last" > "$FAKE_PROMPT_ARG"
cat > "$FAKE_STDIN"
jq -cn --rawfile r "$FAKE_REPLY" '{type:"system"},{type:"result",is_error:true,result:"partial"},{type:"result",is_error:false,result:$r},{type:"result",is_error:false,result:""}'
A
cat > "$T/bin/codex" <<'A'
#!/usr/bin/env bash
"$(dirname "$0")/logargv" codex "$@"
cat > "$FAKE_STDIN"
o=""; while [ "$#" -gt 0 ]; do [ "$1" = --output-last-message ] && o=$2; shift; done
cp "$FAKE_REPLY" "$o"; echo "codex progress noise"
A
cat > "$T/bin/claude" <<'A'
#!/usr/bin/env bash
"$(dirname "$0")/logargv" claude "$@"
for a in "$@"; do last=$a; done
printf '%s\n' "$last" > "$FAKE_PROMPT_ARG"
cat > "$FAKE_STDIN"
jq -cn --rawfile r "$FAKE_REPLY" '{type:"result",is_error:false,result:$r}'
A
cat > "$T/bin/muse" <<'A'
#!/usr/bin/env bash
"$(dirname "$0")/logargv" muse "$@"
while [ "$#" -gt 0 ]; do [ "$1" = --prompt-file ] && cp "$2" "$FAKE_STDIN"; shift; done
# muse exec --json is JSONL. The answer is joined run.output.delta text.
if [ -n "${FAKE_MUSE_TERMINAL:-}" ]; then
  jq -cn --rawfile r "$FAKE_REPLY" '{payload_type:"run.terminal.done",payload:{text:$r}}'
  exit 0
fi
jq -cn --rawfile r "$FAKE_REPLY" '{payload_type:"run.output.delta",payload:{text:$r}}'
A
# The router stub logs its argv and prints $FAKE_ROUTE (exit $FAKE_ROUTE_RC).
cat > "$T/bin/seat-usage" <<'S'
#!/usr/bin/env bash
echo "seat-usage $*" >> "$FAKE_SU_LOG"
echo "[seat-usage] pull-only cached 1m ago" >&2
[ -n "${FAKE_ROUTE_ERR:-}" ] && echo "$FAKE_ROUTE_ERR" >&2
# The over-cap re-ask excludes the agy vendor; FAKE_ROUTE_EXCLUDED answers it.
case " $* " in
  *" --exclude-vendor "*) [ -n "${FAKE_ROUTE_EXCLUDED:-}" ] && { printf '%s\n' "$FAKE_ROUTE_EXCLUDED"; exit 0; } ;;
esac
printf '%s\n' "$FAKE_ROUTE"
exit "${FAKE_ROUTE_RC:-0}"
S
chmod +x "$T/bin/"*
export MERGE_GATE_GH="$T/bin/gh" MERGE_GATE_AGY="$T/bin/agy" MERGE_GATE_SEAT_USAGE="$T/bin/seat-usage"
export MERGE_GATE_CURSOR_AGENT="$T/bin/cursor-agent" MERGE_GATE_CODEX="$T/bin/codex"
export MERGE_GATE_CLAUDE="$T/bin/claude" MERGE_GATE_MUSE="$T/bin/muse"
export FAKE_GH_LOG="$T/gh.log" FAKE_STDIN="$T/reviewer.stdin" FAKE_REPLY="$T/reply.md"
export FAKE_RUN_LOG="$T/run.log" FAKE_SU_LOG="$T/su.log" FAKE_PROMPT_ARG="$T/prompt.arg"
# The contract: top-level runner is the argv list, the same list on reviewers[0].runner.
route() { # $1 family, $2 vendor, $3 model, $4 runner argv as a JSON list, $5 skipped JSON list
  # builder_vendor is the caller's vendor (BUILDER, default xai), recorded for merge.
  printf '[{"role": "review", "mode": "default", "builder_vendor": "%s", "runner": %s, "reason": "ranked", "reviewers": [{"family": "%s", "vendor": "%s", "model": "%s", "runner": %s}], "skipped": %s}]' \
    "${BUILDER:-xai}" "$4" "$1" "$2" "$3" "$4" "${5:-[]}"
}
ROUTE_ARGON="$(route argon google gemini-4-argon-high '["agy","--model","gemini-4-argon-high"]')"
export FAKE_ROUTE="$ROUTE_ARGON"

R="$T/repo"; git init -q -b main "$R"
echo a > "$R/f"; git -C "$R" add f; git -C "$R" commit -qm base
git -C "$R" checkout -q -b feat; echo b >> "$R/f"; git -C "$R" commit -qam change
HEAD_SHA="$(git -C "$R" rev-parse feat)"
PR=https://github.com/o/r/pull/1

run() { # $1 case name, reply text in $2; leaves $T/$1/{review,merge}.out
  local d="$T/$1"; mkdir -p "$d"; printf '%s\n' "$2" > "$FAKE_REPLY"; : > "$FAKE_GH_LOG"
  : > "$FAKE_RUN_LOG"; : > "$FAKE_SU_LOG"; : > "$FAKE_STDIN"; : > "$FAKE_PROMPT_ARG"
  "$ROOT/scripts/merge-gate.sh" review "$R" main feat "$PR" "$d/out" "${BUILDER:-xai}" > "$d/review.out" 2>&1; review_rc=$?
  "$ROOT/scripts/merge-gate.sh" merge "$PR" "$d/out" > "$d/merge.out" 2>&1; merge_rc=$?
  merge_out="$(cat "$d/merge.out")"; gh_log="$(cat "$FAKE_GH_LOG")"
  review_out="$(cat "$d/review.out")"; su_log="$(cat "$FAKE_SU_LOG")"
  run_log="$(cat "$FAKE_RUN_LOG")"; agy_log="$(grep '^agy ' "$FAKE_RUN_LOG")"
  echo "--- $1: review rc=$review_rc merge rc=$merge_rc"; sed 's/^/    /' "$d/merge.out"
}

# 1. normal diff: one_way: no, no label -> merges, pinned to the reviewed head.
run pass $'approve\n\nFindings:\nNone.\n\nChecked and found clean:\n- f\n\none_way: no'
[ "$review_rc" = 0 ] && ok "pass: review rc 0" || bad "pass: review rc $review_rc"
[ "$merge_rc" = 0 ] && ok "pass: merge rc 0" || bad "pass: merge rc $merge_rc"
has "pass: gh pr merge invoked squash at the reviewed sha" "$gh_log" "gh pr merge $PR --squash --match-head-commit $HEAD_SHA"
has "pass: labels checked" "$gh_log" "gh pr view $PR --json labels"
p="$(cat "$FAKE_STDIN")"
has "prompt asks for the one_way line" "$p" 'exactly `one_way: yes` or `one_way: no`'
has "prompt asks for one_way_at file:line" "$p" 'one_way_at: <file>:<line>'
has "prompt carries the diff" "$p" '+b'
has "verdict json written" "$(cat "$T/pass/out/gate-verdict.json")" '"one_way": "no"'
has "pass: router asked with the builder vendor" "$su_log" "seat-usage --route review --cached --json --builder-vendor xai"
hasnt "pass: router not asked for a runner" "$su_log" "--runner"
has "pass: agy runs the router's model" "$agy_log" "--model gemini-4-argon-high "
has "pass: agy runs sandboxed" "$agy_log" "--sandbox"
has "pass: review names the reviewer and runner" "$review_out" "merge-gate: reviewer gemini-4-argon-high on agy"
hasnt "pass: log does not say --runner agy" "$review_out" "--runner agy"
# seat-usage's answer is kept apart from the one field review adds.
[ "$(jq -cS 'map(del(.vendor_source))' "$T/pass/out/gate-route.json")" = "$(jq -cS . <<<"$ROUTE_ARGON")" ] \
  && ok "pass: route output kept" || bad "pass: route output changed beyond vendor_source"
has "pass: route records the builder vendor" "$(cat "$T/pass/out/gate-route.json")" '"builder_vendor": "xai"'
has "pass: no trailer notes the caller" "$review_out" "merge-gate: note: no model trailer on the PR commits; builder vendor xai is the caller's"
hasnt "pass: merge prints no vendor reason" "$merge_out" "recorded reviewer vendor"
hasnt "pass: merge prints no missing route" "$merge_out" "gate-route.json"

# The router decides; when it asks, fails, or gives no launch argv, refuse
# before any reviewer runs. Each refusal also clears the earlier verdict, so
# merge refuses too.
refused() { # $1 case, $2 expected text
  local body
  # A no-trailer note may precede a later refusal. The refusal itself stays one line.
  body=$(printf '%s\n' "$review_out" | grep -v '^merge-gate: note: ' || true)
  [ "$review_rc" = 3 ] && ok "$1: review rc 3" || bad "$1: review rc $review_rc"
  [ "$(printf '%s\n' "$body" | wc -l)" = 1 ] && ok "$1: one-line message" || bad "$1: not one line: $review_out"
  has "$1: reason" "$review_out" "$2"
  [ -z "$run_log" ] && ok "$1: no reviewer launched" || bad "$1: reviewer launched: $run_log"
  [ "$merge_rc" = 3 ] && ok "$1: merge refuses" || bad "$1: merge rc $merge_rc"
  hasnt "$1: no gh pr merge" "$gh_log" "pr merge"
}
mkdir -p "$T/stale/out"; cp "$T/pass/out/"gate* "$T/stale/out/"
FAKE_ROUTE='[{"role": "review", "mode": "ask", "runner": null, "reason": "need 1 review seat(s)\n  outside xAI; 0 eligible: ask", "reviewers": [], "skipped": []}]' \
  run stale $'approve\none_way: no'
refused "route asks" "merge-gate: refused: no eligible reviewer: need 1 review seat(s) outside xAI; 0 eligible: ask"
OLD="seat-usage is too old to return a review runner; update quartermaster"
FAKE_ROUTE='[{"role": "review", "mode": "default", "reason": "ranked", "reviewers": [{"family": "astra", "vendor": "openai", "model": "gpt-6-astra"}]}]' \
  run oldroute $'approve\none_way: no'
refused "old seat-usage with no runner" "merge-gate: refused: $OLD"
FAKE_ROUTE='[{"role": "review", "mode": "default", "runner": "agy", "reason": "ranked", "reviewers": [{"family": "argon", "vendor": "google", "model": "gemini-4-argon-high"}]}]' \
  run oldagy $'approve\none_way: no'
refused "old seat-usage with runner agy as a string" "merge-gate: refused: $OLD"
FAKE_ROUTE='' FAKE_ROUTE_RC=2 FAKE_ROUTE_ERR='unknown builder vendor nope; vendors: anthropic, google, meta, openai, xai' \
  run routefail $'approve\none_way: no'
refused "router exits nonzero" "merge-gate: refused: seat-usage --route review failed: unknown builder vendor nope"
FAKE_ROUTE='not json' run notjson $'approve\none_way: no'
refused "router output not JSON" "merge-gate: refused: seat-usage output is not JSON"
FAKE_ROUTE="$(route argon google '--sandbox x' '["agy"]')" run badmodel $'approve\none_way: no'
refused "unusable model id" "merge-gate: refused: seat-usage reviewer has no usable model id"
FAKE_ROUTE="$(route argon google gemini-4-argon-high '["agy","--model","x;rm -rf /"]')" run badargv $'approve\none_way: no'
refused "shell metacharacter in the runner" "merge-gate: refused: seat-usage runner is not a launch command"
FAKE_ROUTE="$(route argon google gemini-4-argon-high '["agy","--model","bad model"]')" run badargvmodel $'approve\none_way: no'
refused "argv model not a single token" "merge-gate: refused: seat-usage reviewer has no usable model id"
FAKE_ROUTE="$(route astra openai gpt-6-astra '["/opt/bin/aider","--model","gpt-6-astra"]')" run unknown $'approve\none_way: no'
refused "unknown runner" "merge-gate: refused: no read-only launch for runner aider"
FAKE_ROUTE="$(route fable anthropic fable '["cursor-agent","--model","fable"]')" FAKE_CURSOR_MODELS='gpt-5' \
  run nofable $'approve\none_way: no'
has "fable alias unresolved: models listed" "$run_log" "cursor-agent models"
run_log="$(grep -v '^cursor-agent models$' "$FAKE_RUN_LOG")"
refused "fable alias unresolved" "merge-gate: refused: cursor-agent models lists no fable id"

# Other runners: each launches read-only and its answer becomes the verdict.
ANSWER=$'approve\n\nFindings:\nNone.\n\none_way: no'
passes() { # $1 case, $2 runner name
  [ "$review_rc" = 0 ] && ok "$1: review rc 0" || bad "$1: review rc $review_rc"
  [ "$merge_rc" = 0 ] && ok "$1: merges" || bad "$1: merge rc $merge_rc"
  has "$1: gh pr merge at the reviewed sha" "$gh_log" "gh pr merge $PR --squash --match-head-commit $HEAD_SHA"
  has "$1: verdict from the $2 answer" "$(cat "$T/$1/out/gate-verdict.json")" '"one_way": "no"'
  has "$1: gate.md is the answer" "$(cat "$T/$1/out/gate.md")" "one_way: no"
  [ -z "$agy_log" ] && ok "$1: agy not called" || bad "$1: agy called: $agy_log"
  has "$1: review names the runner" "$review_out" " on $2 "
}
FAKE_ROUTE="$(route astra openai gpt-6-astra '["codex","exec","-m","gpt-6-astra"]' \
  '[{"family": "argon", "model": "gemini-4-argon-high", "code": "capped"}]')" run codex "$ANSWER"
passes codex codex
has "codex: argv has -s read-only" "$run_log" "-s read-only"
has "codex: model from -m" "$run_log" "codex exec -m gpt-6-astra "
hasnt "codex: not codex review" "$run_log" "codex review"
has "codex: prompt on stdin carries the diff" "$(cat "$FAKE_STDIN")" '+b'
has "codex: prompt has the one_way line" "$(cat "$FAKE_STDIN")" 'exactly `one_way: yes` or `one_way: no`'
# Reviewer vendor is xai. A same-vendor route is refused, so this builder is anthropic.
BUILDER=anthropic FAKE_ROUTE="$(BUILDER=anthropic route grok xai grok-4.7-xhigh-fast '["cursor-agent","--model","grok-4.7-xhigh-fast"]')" run cursor "$ANSWER"
passes cursor cursor-agent
has "cursor: sandbox enabled" "$run_log" "--sandbox enabled"
hasnt "cursor: no --force" "$run_log" "--force"
has "cursor: slug used as given" "$run_log" "--model grok-4.7-xhigh-fast"
hasnt "cursor: no model listing for a slug" "$run_log" "cursor-agent models"
hasnt "cursor: workspace is not the repo" "$run_log" "--workspace $R"
has "cursor: prompt on stdin carries the diff" "$(cat "$FAKE_STDIN")" '+b'
has "cursor: prompt on stdin has the one_way line" "$(cat "$FAKE_STDIN")" 'exactly `one_way: yes` or `one_way: no`'
hasnt "cursor: prompt is not an argument" "$(cat "$FAKE_PROMPT_ARG")" '+b'
FAKE_ROUTE="$(route fable anthropic fable '["cursor-agent","--model","fable"]')" \
  FAKE_CURSOR_MODELS=$'claude-fable-8-thinking-xhigh - Fable 8\nclaude-fable-9-thinking-xhigh - Fable 9\nclaude-fable-9-thinking-high\ngrok-4.7-xhigh' \
  run cursorfable "$ANSWER"
passes cursorfable cursor-agent
has "cursor fable: resolved to the newest id" "$run_log" "--model claude-fable-9-thinking-xhigh"
# Reviewer vendor is xai, so this builder is anthropic.
BUILDER=anthropic FAKE_ROUTE="$(BUILDER=anthropic route grok xai grok '["cursor-agent","--model","grok"]')" \
  FAKE_CURSOR_MODELS=$'grok-4.7-xhigh\ngrok-4.7-xhigh-fast\ngrok-4.6-xhigh-fast\ngrok-4.7-high-fast' \
  run cursorgrok "$ANSWER"
passes cursorgrok cursor-agent
has "cursor grok: resolved to the newest xhigh Fast id" "$run_log" "--model grok-4.7-xhigh-fast"
FAKE_ROUTE="$(route fable anthropic fable-5 '["claude","--model","fable-5"]')" run claude "$ANSWER"
passes claude claude
has "claude: dontAsk" "$run_log" "--permission-mode dontAsk"
has "claude: model" "$run_log" "--model fable-5"
has "claude: prompt on stdin carries the diff" "$(cat "$FAKE_STDIN")" '+b'
has "claude: prompt on stdin has the one_way line" "$(cat "$FAKE_STDIN")" 'exactly `one_way: yes` or `one_way: no`'
hasnt "claude: prompt is not an argument" "$(cat "$FAKE_PROMPT_ARG")" '+b'
FAKE_ROUTE="$(route muse meta muse-wonder-2 '["muse","exec","--model","muse-wonder-2"]')" run muse "$ANSWER"
passes muse muse
has "muse: --disable-shell" "$run_log" "--disable-shell"
has "muse: --disable-write" "$run_log" "--disable-write"
has "muse: model" "$run_log" "--model muse-wonder-2 "
has "muse: answer is not the JSONL wrapper" "$(cat "$T/muse/out/gate.md")" "approve"
hasnt "muse: gate.md is not a JSON line" "$(cat "$T/muse/out/gate.md")" "payload_type"
FAKE_MUSE_TERMINAL=1 FAKE_ROUTE="$(route muse meta muse-wonder-2 '["muse","exec","--model","muse-wonder-2"]')" \
  run museterm "$ANSWER"
passes museterm muse
has "muse terminal: answer comes from run.terminal" "$(cat "$T/museterm/out/gate.md")" "one_way: no"
hasnt "muse terminal: gate.md is not a JSON line" "$(cat "$T/museterm/out/gate.md")" "payload_type"

# A prompt over 128KB (MAX_ARG_STRLEN) cannot be one argv string: execve fails
# with "Argument list too long". The stubs are real executables, so an argv
# prompt would fail the launch here too. Every runner must get it by file or stdin.
git -C "$R" checkout -q -b big main
python3 -c 'print("\n".join("line %05d %s" % (i, "x" * 50) for i in range(3000)))' > "$R/big.txt"
git -C "$R" add big.txt; git -C "$R" commit -qm big
git -C "$R" checkout -q feat
big() { # $1 case, $2 runner argv JSON, $3 family, $4 vendor, $5 model
  local d="$T/big-$1"; mkdir -p "$d"; printf '%s\n' "$ANSWER" > "$FAKE_REPLY"
  : > "$FAKE_RUN_LOG"; : > "$FAKE_STDIN"; : > "$FAKE_PROMPT_ARG"
  local builder=xai; [ "$4" = xai ] && builder=anthropic
  FAKE_ROUTE="$(BUILDER=$builder route "$3" "$4" "$5" "$2")" "$ROOT/scripts/merge-gate.sh" review "$R" main big "$PR" "$d/out" "$builder" \
    > "$d/review.out" 2>&1; review_rc=$?
  review_out="$(cat "$d/review.out")"; run_log="$(cat "$FAKE_RUN_LOG")"
  [ "$review_rc" = 0 ] && ok "big $1: review rc 0" || bad "big $1: review rc $review_rc: $review_out"
  [ "$(wc -c < "$d/out/gate-prompt.md")" -gt 131072 ] && ok "big $1: prompt is over 128KB" || bad "big $1: prompt only $(wc -c < "$d/out/gate-prompt.md") bytes"
  # agy takes the prompt as the content of one stream-json user event.
  { [ "$1" = agy ] && jq -j '.message.content' "$FAKE_STDIN" || cat "$FAKE_STDIN"; } | cmp -s "$d/out/gate-prompt.md" - && ok "big $1: reviewer received the whole prompt" || bad "big $1: reviewer got $(wc -c < "$FAKE_STDIN") bytes, not the whole prompt"
  has "big $1: verdict" "$(cat "$d/out/gate-verdict.json" 2>/dev/null)" '"one_way": "no"'
  # logargv logs any multi-line argument, which the prompt always is, as PROMPT.
  if grep -qE ' PROMPT( |$)' "$FAKE_RUN_LOG"; then bad "big $1: a multi-line argument was passed"; else ok "big $1: no multi-line argument"; fi
  [ "$(awk '{ if (length($0) > m) m = length($0) } END { print m + 0 }' "$FAKE_RUN_LOG")" -lt 4096 ] \
    && ok "big $1: no argument carries the prompt" || bad "big $1: long argv logged"
}
big cursor '["cursor-agent","--model","grok-4.7-xhigh-fast"]' grok xai grok-4.7-xhigh-fast
big claude '["claude","--model","fable-5"]' fable anthropic fable-5
big codex '["codex","exec","-m","gpt-6-astra"]' astra openai gpt-6-astra
# The other runners above took this prompt over the agy cap; agy needs a raised cap.
CONCLAVE_ARGON_MAX_BYTES=400000 big agy '["agy","--model","gemini-4-argon-high"]' argon google gemini-4-argon-high
big muse '["muse","exec","--model","muse-wonder-2"]' muse meta muse-wonder-2

# agy silently stops reading near 190 KB, so over CONCLAVE_ARGON_MAX_BYTES agy
# never runs. The gate asks seat-usage again without agy's vendor, keeping the
# builder vendor, and reviews on the seat that route names.
overcap() { # $1 case; FAKE_ROUTE_EXCLUDED answers the re-ask
  local d="$T/$1"; mkdir -p "$d"; printf '%s\n' "$ANSWER" > "$FAKE_REPLY"
  : > "$FAKE_RUN_LOG"; : > "$FAKE_SU_LOG"; : > "$FAKE_STDIN"; : > "$FAKE_GH_LOG"
  "$ROOT/scripts/merge-gate.sh" review "$R" main big "$PR" "$d/out" xai > "$d/review.out" 2> "$d/review.err"; review_rc=$?
  size=$(wc -c < "$d/out/gate-prompt.md"); su_log="$(cat "$FAKE_SU_LOG")"; run_log="$(cat "$FAKE_RUN_LOG")"
  review_out="$(cat "$d/review.out")"; review_err="$(cat "$d/review.err")"
  "$ROOT/scripts/merge-gate.sh" merge "$PR" "$d/out" > "$d/merge.out" 2>&1; merge_rc=$?
  gh_log="$(cat "$FAKE_GH_LOG")"
}
reason_of() { echo "gate prompt is $size bytes, over the agy cap of 150000 bytes (agy silently stops reading near 190 KB)"; }

# (1) Over cap with an eligible non-google seat: swap, log it, review, merge.
FAKE_ROUTE_EXCLUDED="$(route astra openai gpt-6-astra '["codex","exec","-m","gpt-6-astra"]' \
  '[{"family":"argon","code":"excluded_vendor"},{"family":"muse","code":"capped"},{"family":"astra-stale","code":"stale"}]')" \
  overcap agy-swap
d="$T/agy-swap"
[ "$review_rc" = 0 ] && ok "swap: review rc 0" || bad "swap: review rc $review_rc: $review_out $review_err"
[ -z "$(grep '^agy ' "$FAKE_RUN_LOG")" ] && ok "swap: agy not called" || bad "swap: agy called"
has "swap: codex reviewed" "$run_log" "codex exec -m gpt-6-astra"
has "swap: first ask keeps the builder vendor" "$(sed -n 1p "$FAKE_SU_LOG")" "seat-usage --route review --cached --json --builder-vendor xai"
has "swap: re-ask keeps the builder vendor and excludes google" "$(sed -n 2p "$FAKE_SU_LOG")" \
  "seat-usage --route review --cached --json --builder-vendor xai --exclude-vendor google"
[ "$(wc -l < "$FAKE_SU_LOG")" = 2 ] && ok "swap: two seat-usage asks" || bad "swap: asks: $su_log"
has "swap: says why" "$review_out" "merge-gate: $(reason_of); asking seat-usage again without google"
has "swap: says where" "$review_out" "merge-gate: swapped reviewer to gpt-6-astra on codex"
jq -e --arg r "$(reason_of)" '.[0].swap == {from: {runner: "agy", model: "gemini-4-argon-high", vendor: "google"}, excluded_vendor: "google", reason: $r}
  and .[0].reviewers[0].vendor == "openai" and .[0].builder_vendor == "xai" and .[0].vendor_source == "caller"' \
  "$d/out/gate-route.json" > /dev/null && ok "swap: gate-route.json records the swap and the new reviewer" \
  || bad "swap: gate-route.json: $(cat "$d/out/gate-route.json")"
jq -e '.[0].reviewers[0].vendor == "google" and (.[0].swap == null)' "$d/out/gate-route-first.json" > /dev/null \
  && ok "swap: the first route is kept" || bad "swap: first route: $(cat "$d/out/gate-route-first.json" 2>/dev/null)"
has "swap: gate.md notes the swap" "$(cat "$d/out/gate.md")" \
  "merge-gate swap: reviewer gemini-4-argon-high on agy (google) -> gpt-6-astra on codex; $(reason_of); seat-usage asked again with --exclude-vendor google and --builder-vendor xai"
has "swap: verdict still parses" "$(cat "$d/out/gate-verdict.json")" '"verdict": "approve"'
[ "$merge_rc" = 0 ] && ok "swap: merge rc 0" || bad "swap: merge rc $merge_rc: $(cat "$d/merge.out")"
has "swap: merged at the reviewed sha" "$gh_log" "--squash --match-head-commit $(git -C "$R" rev-parse big)"

# (2) Over cap and nothing fresh and uncapped is left: ask Ryan (exit 4), no reviewer, no verdict.
ASK='[{"role": "review", "mode": "ask", "builder_vendor": "xai", "runner": null, "reason": "need 1 review seat(s) outside xai, google; 0 eligible: ask Ryan", "reviewers": [], "skipped": [{"family":"astra","code":"stale"},{"family":"muse","code":"capped"}]}]'
FAKE_ROUTE_EXCLUDED="$ASK" overcap agy-ask
d="$T/agy-ask"
[ "$review_rc" = 4 ] && ok "ask: review rc 4" || bad "ask: review rc $review_rc: $review_err"
[ "$(wc -l < "$d/review.err")" = 1 ] && ok "ask: one stderr line" || bad "ask: stderr: $review_err"
has "ask: asks Ryan and says why" "$review_err" \
  "merge-gate: ask Ryan: $(reason_of), and with google excluded there is no eligible reviewer: need 1 review seat(s) outside xai, google; 0 eligible: ask Ryan"
hasnt "ask: not a plain refusal" "$review_err" "refused"
[ -z "$run_log" ] && ok "ask: no reviewer ran" || bad "ask: ran: $run_log"
[ ! -e "$d/out/gate-verdict.json" ] && [ ! -e "$d/out/gate.md" ] && ok "ask: no verdict written" || bad "ask: verdict written"
jq -e '.[0].mode == "ask" and .[0].swap.excluded_vendor == "google"' "$d/out/gate-route.json" > /dev/null \
  && ok "ask: gate-route.json records the attempted swap" || bad "ask: gate-route.json: $(cat "$d/out/gate-route.json")"
[ "$merge_rc" != 0 ] && ok "ask: merge refuses" || bad "ask: merge rc 0"
hasnt "ask: no gh pr merge" "$gh_log" "pr merge"

# (3) A re-ask that still names agy refuses (exit 3) instead of truncating.
overcap agy-again
[ "$review_rc" = 3 ] && ok "agy again: review rc 3" || bad "agy again: review rc $review_rc"
has "agy again: says so" "$review_err" "merge-gate: refused: $(reason_of), and the route without google still names agy"
[ -z "$(grep '^agy ' "$FAKE_RUN_LOG")" ] && ok "agy again: agy not called" || bad "agy again: agy called"
[ "$merge_rc" != 0 ] && ok "agy again: merge refuses" || bad "agy again: merge rc 0"
d="$T/agy-undercap"; mkdir -p "$d"; : > "$FAKE_RUN_LOG"
CONCLAVE_ARGON_MAX_BYTES=$size "$ROOT/scripts/merge-gate.sh" review "$R" main big "$PR" "$d/out" xai > "$d/review.out" 2>&1; review_rc=$?
[ "$review_rc" = 0 ] && ok "agy at the cap: review rc 0" || bad "agy at the cap: review rc $review_rc: $(cat "$d/review.out")"
has "agy at the cap: agy called" "$(cat "$FAKE_RUN_LOG")" "agy --model gemini-4-argon-high"
has "agy at the cap: verdict" "$(cat "$d/out/gate-verdict.json" 2>/dev/null)" '"verdict": "approve"'

# 2. one_way: yes -> refused, names the reason and the file:line, no merge.
run yes $'approve\n\nFindings:\nNone.\n\n**one_way: yes**\none_way_at: db/migrate/002_drop.sql:3 drops column users.email'
[ "$merge_rc" = 3 ] && ok "yes: merge rc 3" || bad "yes: merge rc $merge_rc"
has "yes: reason" "$merge_out" "reason: gate verdict is one_way: yes"
has "yes: file:line" "$merge_out" "one-way step: db/migrate/002_drop.sql:3 drops column users.email"
has "yes: needs Ryan's yes" "$merge_out" "needs Ryan's explicit yes recorded in $T/yes/out/acks/one-way-ack.json"
hasnt "yes: no gh pr merge" "$gh_log" "pr merge"

# 3. one-way label with a clean verdict -> refused.
FAKE_LABELS='{"name":"docs"},{"name":"one-way"}' run label $'approve\n\none_way: no'
[ "$merge_rc" = 3 ] && ok "label: merge rc 3" || bad "label: merge rc $merge_rc"
has "label: reason" "$merge_out" 'reason: the PR has the `one-way` label'
has "label: needs Ryan's yes" "$merge_out" "needs Ryan's explicit yes recorded in $T/label/out/acks/one-way-ack.json"
hasnt "label: no gh pr merge" "$gh_log" "pr merge"

# 4. missing verdict line -> refused (fail closed).
run missing $'approve\n\nFindings:\nNone.'
[ "$merge_rc" = 3 ] && ok "missing: merge rc 3" || bad "missing: merge rc $merge_rc"
has "missing: reason" "$merge_out" "gate verdict is missing or unparseable: no one_way: yes|no line"
hasnt "missing: no gh pr merge" "$gh_log" "pr merge"

# Fail-closed edges.
run conflict $'approve\none_way: no\none_way: yes\none_way_at: a.sql:1 x'
[ "$merge_rc" = 3 ] && ok "conflicting lines refused" || bad "conflicting rc $merge_rc"
run malformed $'approve\none_way: maybe'
has "malformed refused" "$merge_out" "unparseable one_way line"
run fenced $'approve\n```\none_way: no\n```'
has "a one_way line inside a code fence does not count" "$merge_out" "no one_way: yes|no line"
run empty $'approve\none_way:'
has "one_way: with no value refused" "$merge_out" "unparseable one_way line: 'one_way:'"
hasnt "one_way: with no value: no gh pr merge" "$gh_log" "pr merge"

# Prose that mentions one_way is not a verdict.
run prose-ok $'approve\n\nChecked and found clean:\n- `one_way` parsing in scripts/merge_gate.py\none_way checks look fine\n\none_way: no'
[ "$merge_rc" = 0 ] && ok "prose one_way lines plus one_way: no merges" || bad "prose-ok merge rc $merge_rc"
has "prose-ok: gh pr merge invoked" "$gh_log" "gh pr merge $PR --squash --match-head-commit $HEAD_SHA"
run prose-only $'approve\n\nChecked and found clean:\n- `one_way` parsing in scripts/merge_gate.py\none_way checks look fine'
[ "$merge_rc" = 3 ] && ok "prose-only: merge rc 3" || bad "prose-only merge rc $merge_rc"
has "prose-only refuses as missing" "$merge_out" "no one_way: yes|no line in the gate answer"
hasnt "prose-only: no gh pr merge" "$gh_log" "pr merge"
run maybe $'approve\n- `one_way` parsing looks fine\none_way: maybe'
[ "$merge_rc" = 3 ] && ok "maybe: merge rc 3" || bad "maybe merge rc $merge_rc"
has "maybe refuses as unparseable" "$merge_out" "unparseable one_way line: 'one_way: maybe'"
hasnt "maybe: no gh pr merge" "$gh_log" "pr merge"
FAKE_GH_LABELS_FAIL=1 run nolabels $'approve\none_way: no'
[ "$merge_rc" = 3 ] && ok "unreadable labels refused" || bad "unreadable labels rc $merge_rc"
hasnt "unreadable labels: no gh pr merge" "$gh_log" "pr merge"
out="$("$ROOT/scripts/merge-gate.sh" merge "$PR" "$T/nowhere" 2>&1)"; rc=$?
[ "$rc" = 3 ] && ok "no gate dir refused" || bad "no gate dir rc $rc"
has "no gate dir: reason" "$out" "no gate answer at"
# The PR head moved after the gate: gh refuses --match-head-commit, merge exits
# nonzero, and nothing reports merged. FAKE_PR_HEAD is the PR's current head.
MOVED=0123456789abcdef0123456789abcdef01234567
FAKE_PR_HEAD=$MOVED run headmoved $'approve\n\nFindings:\nNone.\n\none_way: no'
has "head moved: gh pr merge pinned to the reviewed sha" "$gh_log" "gh pr merge $PR --squash --match-head-commit $HEAD_SHA"
[ "$merge_rc" != 0 ] && ok "head moved: merge exits nonzero ($merge_rc)" || bad "head moved: merge rc $merge_rc"
has "head moved: gh's refusal is shown" "$merge_out" "Head branch was modified"
hasnt "head moved: nothing reports merged" "$merge_out" "merged"
FAKE_PR_HEAD=$HEAD_SHA run headsame $'approve\n\nFindings:\nNone.\n\none_way: no'
[ "$merge_rc" = 0 ] && ok "head unchanged: merge rc 0" || bad "head unchanged: merge rc $merge_rc"
has "head unchanged: merged" "$merge_out" "merged"

# One-way acknowledgement. A one_way: yes verdict merges only with a valid
# <out>/acks/one-way-ack.json written by a bot other than the builder.
YES=$'approve\n\nFindings:\nNone.\n\none_way: yes\none_way_at: db/migrate/002_drop.sql:3 drops column users.email\none_way_at: app/mail.py:9 emails every user'
ackgate() { # $1 case, $2 reply, $3 builder bot (empty for none): review only
  local d="$T/$1"; mkdir -p "$d"; printf '%s\n' "$2" > "$FAKE_REPLY"; : > "$FAKE_GH_LOG"
  "$ROOT/scripts/merge-gate.sh" review "$R" main feat "$PR" "$d/out" xai ${3:+"$3"} > "$d/review.out" 2>&1
}
ack() { # $1 case, $2 jq filter applied to a well-formed ack copied from gate-verdict.json
  local o="$T/$1/out"; mkdir -p "$o/acks"
  jq --arg pr "$PR" --arg h "$HEAD_SHA" '{pr_url: $pr, head_sha: $h, one_way_items: .items,
      ryan_words: "Yes, merge #1", source: {chat: "Practice Paul", message_or_card_id: "m-42"},
      writer: "Practice Paul", written_at: "2026-10-27T18:44:00-07:00"}' "$o/gate-verdict.json" \
    | jq "${2:-.}" > "$o/acks/one-way-ack.json"
}
amerge() { # $1 case, then extra merge args; leaves merge_rc, merge_out, gh_log
  : > "$FAKE_GH_LOG"
  "$ROOT/scripts/merge-gate.sh" merge "${@:2}" "$PR" "$T/$1/out" > "$T/$1/merge.out" 2>&1; merge_rc=$?
  merge_out="$(cat "$T/$1/merge.out")"; gh_log="$(cat "$FAKE_GH_LOG")"
  echo "--- $1: merge rc=$merge_rc"; sed 's/^/    /' "$T/$1/merge.out"
}
ackrefused() { # $1 case label, $2 expected reason
  [ "$merge_rc" = 3 ] && ok "$1: merge rc 3" || bad "$1: merge rc $merge_rc"
  has "$1: reason" "$merge_out" "$2"
  hasnt "$1: no gh pr merge" "$gh_log" "pr merge"
}
export FAKE_PR_HEAD=$HEAD_SHA

ackgate ack-ok "$YES" Ada; ack ack-ok
has "ack-ok: review records the builder bot" "$(cat "$T/ack-ok/out/builder-bot.txt" 2>&1)" "Ada"
amerge ack-ok
[ "$merge_rc" = 0 ] && ok "ack-ok: merge rc 0" || bad "ack-ok: merge rc $merge_rc"
has "ack-ok: gh pr merge pinned to the reviewed sha" "$gh_log" "gh pr merge $PR --squash --match-head-commit $HEAD_SHA"
has "ack-ok: live head read" "$gh_log" "gh pr view $PR --json headRefOid"
[ ! -e "$T/ack-ok/out/acks/one-way-ack.json" ] && ok "ack-ok: ack moved out of acks/" || bad "ack-ok: ack still in acks/"
used=$(ls "$T/ack-ok/out/acks/used/" 2>/dev/null)
case "$used" in [0-9]*T[0-9]*Z-one-way-ack.json) ok "ack-ok: ack moved to acks/used/$used";; *) bad "ack-ok: acks/used holds '$used'";; esac
note="$(cat "$T/ack-ok/out/merge-note.md" 2>&1)"
has "ack-ok: note quotes Ryan's words" "$note" "Yes, merge #1"
has "ack-ok: note names the source" "$note" "Practice Paul message m-42"
has "ack-ok: note names the writer" "$note" "written by Practice Paul"
has "ack-ok: note lists a one-way step" "$note" "app/mail.py:9 emails every user"
has "ack-ok: stdout carries the note" "$merge_out" "Yes, merge #1"
ack_merges=$(grep '^gh pr merge' "$FAKE_GH_LOG")
[ "$(grep -c '^gh pr merge' "$FAKE_GH_LOG")" = 1 ] && ok "ack-ok: exactly one gh pr merge call" || bad "ack-ok: gh pr merge calls: $ack_merges"
run onemerge-no $'approve\n\nFindings:\nNone.\n\none_way: no'
no_merges=$(grep '^gh pr merge' "$FAKE_GH_LOG")
[ "$(grep -c '^gh pr merge' "$FAKE_GH_LOG")" = 1 ] && ok "one_way: no: exactly one gh pr merge call" || bad "one_way: no: gh pr merge calls: $no_merges"
[ -n "$ack_merges" ] && [ "$ack_merges" = "$no_merges" ] && ok "the ack path and the one_way: no path make the same gh pr merge call" \
  || bad "merge calls differ: ack '$ack_merges' vs no '$no_merges'"

amerge ack-ok
ackrefused "ack-used" "acks/used/ holds 1 used ack, and a used ack cannot authorize a merge again"
cp "$T/ack-ok/out/acks/used/$used" "$T/ack-ok/out/acks/used/copy-one-way-ack.json"
amerge ack-ok
ackrefused "ack-used-copy" "no one-way ack at $T/ack-ok/out/acks/one-way-ack.json"

ackgate ack-missing "$YES" Ada; amerge ack-missing
ackrefused "ack-missing" "no one-way ack at $T/ack-missing/out/acks/one-way-ack.json"
has "ack-missing: one_way reason kept" "$merge_out" "reason: gate verdict is one_way: yes"
has "ack-missing: names the ack" "$merge_out" "acks/one-way-ack.json"

ackgate ack-notjson "$YES" Ada; mkdir -p "$T/ack-notjson/out/acks"; echo '{oops' > "$T/ack-notjson/out/acks/one-way-ack.json"
amerge ack-notjson; ackrefused "ack-notjson" "one-way ack is malformed: not JSON"
ackgate ack-shortsha "$YES" Ada; ack ack-shortsha '.head_sha = "149b5b2"'
amerge ack-shortsha; ackrefused "ack-shortsha" "one-way ack is malformed: head_sha is not a 40-hex sha"
ackgate ack-nowords "$YES" Ada; ack ack-nowords 'del(.ryan_words)'
amerge ack-nowords; ackrefused "ack-nowords" "one-way ack is malformed: ryan_words is missing or not a non-empty string"
ackgate ack-nooffset "$YES" Ada; ack ack-nooffset '.written_at = "2026-10-27T18:44:00"'
amerge ack-nooffset; ackrefused "ack-nooffset" "one-way ack is malformed: written_at is not an ISO timestamp with a UTC offset"
ackgate ack-badsource "$YES" Ada; ack ack-badsource '.source = {chat: "Practice Paul"}'
amerge ack-badsource; ackrefused "ack-badsource" "one-way ack is malformed: source.message_or_card_id is missing or not a non-empty string"
ackgate ack-baditem "$YES" Ada; ack ack-baditem '.one_way_items = [{at: "a.sql:1"}]'
amerge ack-baditem; ackrefused "ack-baditem" "one-way ack is malformed: one_way_items[0] is not {at, what} strings"

ackgate ack-wrongpr "$YES" Ada; ack ack-wrongpr '.pr_url = "https://github.com/o/r/pull/2"'
amerge ack-wrongpr; ackrefused "ack-wrongpr" "one-way ack is for https://github.com/o/r/pull/2, not $PR"

MOVED=0123456789abcdef0123456789abcdef01234567
ackgate ack-gatehead "$YES" Ada; ack ack-gatehead ".head_sha = \"$MOVED\""
amerge ack-gatehead; ackrefused "ack-gatehead" "one-way ack head_sha $MOVED is not the reviewed head $HEAD_SHA (gate-head.txt)"

ackgate ack-livehead "$YES" Ada; ack ack-livehead
FAKE_PR_HEAD=$MOVED amerge ack-livehead
ackrefused "ack-livehead" "the PR head is now $MOVED, not the reviewed head $HEAD_SHA"
[ -e "$T/ack-livehead/out/acks/one-way-ack.json" ] && ok "ack-livehead: ack stays unused" || bad "ack-livehead: ack moved"

ackgate ack-missingitem "$YES" Ada; ack ack-missingitem '.one_way_items |= .[:1]'
amerge ack-missingitem; ackrefused "ack-missingitem" "one-way step missing from the ack: app/mail.py:9 emails every user"
ackgate ack-extraitem "$YES" Ada; ack ack-extraitem '.one_way_items += [{at: "x.py:1", what: "posts to chat"}]'
amerge ack-extraitem; ackrefused "ack-extraitem" "one-way ack lists a step the gate did not: x.py:1 posts to chat"
ackgate ack-editeditems "$YES" Ada; ack ack-editeditems '.one_way_items |= .[:1]'
v="$T/ack-editeditems/out/gate-verdict.json"; jq -c '.items |= .[:1]' "$v" > "$v.tmp" && mv "$v.tmp" "$v"
amerge ack-editeditems; ackrefused "ack-editeditems" "gate-verdict.json one_way items differ from gate.md"

ackgate ack-rc "${YES/approve/request-changes}" Ada; ack ack-rc
amerge ack-rc; ackrefused "ack-rc" "reason: gate verdict is request-changes, not approve"

ackgate ack-builder "$YES" Ada; ack ack-builder '.writer = "  ada "'
amerge ack-builder; ackrefused "ack-builder" "one-way ack writer '  ada ' is the builder bot Ada"

ackgate ack-nobuilder "$YES" ""; ack ack-nobuilder
[ ! -e "$T/ack-nobuilder/out/builder-bot.txt" ] && ok "ack-nobuilder: no builder-bot.txt" || bad "ack-nobuilder: builder-bot.txt written"
amerge ack-nobuilder; ackrefused "ack-nobuilder" "the builder bot is unknown"
amerge ack-nobuilder --builder-bot Ada
[ "$merge_rc" = 0 ] && ok "ack-nobuilder: --builder-bot Ada merges an older gate dir" || bad "ack-nobuilder: --builder-bot rc $merge_rc"
ackgate ack-flagconflict "$YES" Ada; ack ack-flagconflict
amerge ack-flagconflict --builder-bot Jules
ackrefused "ack-flagconflict" "--builder-bot Jules does not match builder-bot.txt (Ada)"
ackgate ack-flagwriter "$YES" ""; ack ack-flagwriter
amerge ack-flagwriter --builder-bot "practice paul"
ackrefused "ack-flagwriter" "one-way ack writer 'Practice Paul' is the builder bot practice paul"

ackgate ack-label "$YES" Ada; ack ack-label
FAKE_LABELS='{"name":"one-way"}' amerge ack-label
[ "$merge_rc" = 0 ] && ok "ack-label: a valid ack merges a one-way-labelled PR" || bad "ack-label: rc $merge_rc"
ackgate ack-label-bad "$YES" Ada; ack ack-label-bad '.writer = "Ada"'
FAKE_LABELS='{"name":"one-way"}' amerge ack-label-bad
ackrefused "ack-label-bad" 'reason: the PR has the `one-way` label'
unset FAKE_PR_HEAD

# Verdict parsing: the value must end at end of line or a non-lowercase character.
parse_of() { printf '%s\n' "$1" > "$T/parse.md"; python3 "$ROOT/scripts/merge_gate.py" parse "$T/parse.md"; }
# The reviewer's verdict word (approve / request-changes / comment) is recorded
# in gate-verdict.json, and merge refuses unless that record says approve.
run rc-verdict $'request-changes\n\nFindings:\n1. Severity: bug. File:line: f:2. Description: x. Suggestion: y.\n\none_way: no'
has "request-changes: verdict recorded" "$(cat "$T/rc-verdict/out/gate-verdict.json")" '"verdict": "request-changes"'
[ "$merge_rc" = 3 ] && ok "request-changes: merge rc 3" || bad "request-changes: merge rc $merge_rc"
has "request-changes: reason" "$merge_out" "reason: gate verdict is request-changes, not approve"
hasnt "request-changes: no gh pr merge" "$gh_log" "pr merge"
run comment-verdict $'comment\n\nFindings:\nNone.\n\none_way: no'
has "comment: verdict recorded" "$(cat "$T/comment-verdict/out/gate-verdict.json")" '"verdict": "comment"'
[ "$merge_rc" = 3 ] && ok "comment: merge rc 3" || bad "comment: merge rc $merge_rc"
hasnt "comment: no gh pr merge" "$gh_log" "pr merge"
run approve-verdict $'approve\n\nFindings:\nNone.\n\none_way: no'
has "approve: verdict recorded" "$(cat "$T/approve-verdict/out/gate-verdict.json")" '"verdict": "approve"'
[ "$merge_rc" = 0 ] && ok "approve: merge rc 0" || bad "approve: merge rc $merge_rc"
has "approve: gh pr merge at the reviewed sha" "$gh_log" "gh pr merge $PR --squash --match-head-commit $HEAD_SHA"
# A gate-verdict.json with no verdict field (written before this field existed) refuses.
d="$T/approve-verdict/out"
jq -c 'del(.verdict)' "$d/gate-verdict.json" > "$d/v.tmp" && mv "$d/v.tmp" "$d/gate-verdict.json"
: > "$FAKE_GH_LOG"
out="$("$ROOT/scripts/merge-gate.sh" merge "$PR" "$d" 2>&1)"; rc=$?
[ "$rc" = 3 ] && ok "no verdict field: merge rc 3" || bad "no verdict field: merge rc $rc"
has "no verdict field: reason" "$out" "reason: gate-verdict.json records no verdict"
hasnt "no verdict field: no gh pr merge" "$(cat "$FAKE_GH_LOG")" "pr merge"
rm -f "$d/gate-verdict.json"; : > "$FAKE_GH_LOG"
out="$("$ROOT/scripts/merge-gate.sh" merge "$PR" "$d" 2>&1)"; rc=$?
[ "$rc" = 3 ] && ok "no gate-verdict.json: merge rc 3" || bad "no gate-verdict.json: merge rc $rc"
has "no gate-verdict.json: reason" "$out" "reason: no gate-verdict.json at"
hasnt "no gate-verdict.json: no gh pr merge" "$(cat "$FAKE_GH_LOG")" "pr merge"
# An answer with no verdict line, or two different ones, records null and refuses.
run noverdict $'Findings:\nNone.\n\none_way: no'
has "no verdict line: verdict null" "$(cat "$T/noverdict/out/gate-verdict.json")" '"verdict": null'
[ "$merge_rc" = 3 ] && ok "no verdict line: merge rc 3" || bad "no verdict line: merge rc $merge_rc"
has "no verdict line: reason" "$merge_out" "no approve|request-changes|comment line in the gate answer"
hasnt "no verdict line: no gh pr merge" "$gh_log" "pr merge"
run twoverdicts $'approve\n\nFindings:\nNone.\n\nrequest-changes\n\none_way: no'
[ "$merge_rc" = 3 ] && ok "conflicting verdicts: merge rc 3" || bad "conflicting verdicts: merge rc $merge_rc"
has "conflicting verdicts: reason" "$merge_out" "conflicting verdict lines: approve, request-changes"
# A gate-verdict.json that disagrees with gate.md refuses.
run edited $'request-changes\n\nFindings:\nNone.\n\none_way: no'
d="$T/edited/out"
jq -c '.verdict = "approve"' "$d/gate-verdict.json" > "$d/v.tmp" && mv "$d/v.tmp" "$d/gate-verdict.json"
: > "$FAKE_GH_LOG"
out="$("$ROOT/scripts/merge-gate.sh" merge "$PR" "$d" 2>&1)"; rc=$?
[ "$rc" = 3 ] && ok "edited gate-verdict.json: merge rc 3" || bad "edited gate-verdict.json: merge rc $rc"
has "edited gate-verdict.json: reason" "$out" "gate-verdict.json says approve but gate.md parses as request-changes"
hasnt "edited gate-verdict.json: no gh pr merge" "$(cat "$FAKE_GH_LOG")" "pr merge"
# Verdict line parsing, the same rules as the one_way value.
has "preamble then approve on its own line" "$(parse_of $'I will read the diff first.\napprove\n\none_way: no')" '"verdict": "approve"'
has "bold **approve** parses" "$(parse_of $'**approve**\none_way: no')" '"verdict": "approve"'
has "glued approveFindings: parses" "$(parse_of $'approveFindings:\nNone.\none_way: no')" '"verdict": "approve"'
has "fenced approve does not count" "$(parse_of $'```\napprove\n```\none_way: no')" '"verdict": null'
for l in 'I approve of this' 'approved' 'approve, mostly' 'Approve' 'request-changes please' 'comment: this is a note' 'approve. Ship it.'; do
  has "'$l' is not a verdict" "$(parse_of "$l"$'\none_way: no')" '"verdict": null'
done
has "verdict error leaves one_way error alone" "$(parse_of $'approve\none_way: no')" '"error": null'
# A name that starts with a verdict word is not a verdict (gate-2 on #46).
v="$(parse_of $'request-changes:\n\n- `approveRequest` skips the head check.\n\none_way: no')"
has "bullet approveRequest under request-changes: parses request-changes" "$v" '"verdict": "request-changes"'
has "bullet approveRequest under request-changes: no verdict error" "$v" '"verdict_error": null'
v="$(parse_of $'approve\n\nFindings:\n- `commentOn` ignores a null author\n\none_way: no')"
has "approve plus bullet commentOn parses approve" "$v" '"verdict": "approve"'
has "approve plus bullet commentOn: no verdict error" "$v" '"verdict_error": null'
v="$(parse_of $'The change is not ready.\n\napproveRequest() skips the null check.\n\none_way: no')"
has "prose approveRequest() with no verdict line parses null" "$v" '"verdict": null'
has "glued approveFindings (no colon) parses" "$(parse_of $'approveFindings\nNone.\none_way: no')" '"verdict": "approve"'
has "approve. alone parses" "$(parse_of $'approve.\none_way: no')" '"verdict": "approve"'
run camel $'The change is not ready.\n\napproveRequest() skips the null check.\n\none_way: no'
[ "$merge_rc" = 3 ] && ok "prose approveRequest(): merge rc 3" || bad "prose approveRequest(): merge rc $merge_rc"
has "prose approveRequest(): reason" "$merge_out" "gate-verdict.json records no verdict"
hasnt "prose approveRequest(): no gh pr merge" "$gh_log" "pr merge"

GLUED=$'approve\n\nFindings:\nNone.\n\none_way: noThe start-order stress run was killed after it deadlocked, so it produced no result.'
has "glued 'one_way: noThe ...' parses as no" "$(parse_of "$GLUED")" '"one_way": "no"'
has "glued parse has no error" "$(parse_of "$GLUED")" '"error": null'
for w in nothing nope none noted yesterday; do
  v="$(parse_of $'approve\none_way: '"$w")"
  hasnt "one_way: $w is not a verdict" "$v" '"one_way": "no"'
  hasnt "one_way: $w is not yes" "$v" '"one_way": "yes"'
  has "one_way: $w is unparseable" "$v" "unparseable one_way line: 'one_way: $w'"
done
has "plain one_way: yes parses" "$(parse_of $'approve\none_way: yes\none_way_at: a.sql:1 x')" '"one_way": "yes"'
has "plain one_way: no parses" "$(parse_of $'approve\none_way: no')" '"one_way": "no"'
has "bold **one_way: no** parses" "$(parse_of $'approve\n**one_way: no**')" '"one_way": "no"'
has "uppercase value one_way: NO is unparseable" "$(parse_of $'approve\none_way: NO')" "unparseable one_way line: 'one_way: NO'"
for l in 'one_way: no longer reversible' 'one_way: no, actually yes' 'one_way: yes; actually no' 'one_way: yes please'; do
  v="$(parse_of $'approve\n'"$l")"
  has "'$l' is unparseable" "$v" "unparseable one_way line: '$l'"
  has "'$l' gives no verdict" "$v" '"one_way": null'
done
has "'one_way: no. The start-order...' parses as no" "$(parse_of $'approve\none_way: no. The start-order stress run was killed.')" '"one_way": "no"'
has "'one_way: no  ' with trailing spaces parses as no" "$(parse_of $'approve\none_way: no  ')" '"one_way": "no"'

# cursor-agent joins assistant turns with no separator in its result string.
asst() { jq -cn --arg t "$1" '{type:"assistant",message:{role:"assistant",content:[{type:"text",text:$t}]}}'; }
C1='I will read the diff before judging it.'
C2=$'approve\n\nFindings:\nNone.\n\none_way: no'
{ echo '{"type":"system"}'; asst "$C1"; asst "$C2"
  jq -cn --arg r "$C1$C2" '{type:"result",subtype:"success",is_error:false,result:$r}'; } > "$T/two.jsonl"
python3 "$ROOT/scripts/merge_gate.py" cursor-result "$T/two.jsonl" "$T/two.md" > /dev/null
[ "$(sed -n 1p "$T/two.md")" = "$C1" ] && ok "two assistant chunks: first chunk on its own line" || bad "two chunks line 1: $(sed -n 1p "$T/two.md")"
[ "$(sed -n 2p "$T/two.md")" = approve ] && ok "two assistant chunks: approve on its own line" || bad "two chunks line 2: $(sed -n 2p "$T/two.md")"
has "two assistant chunks: verdict parses" "$(python3 "$ROOT/scripts/merge_gate.py" parse "$T/two.md")" '"one_way": "no"'
{ asst "something else"; jq -cn '{type:"result",is_error:false,result:"the result string"}'; } > "$T/mismatch.jsonl"
python3 "$ROOT/scripts/merge_gate.py" cursor-result "$T/mismatch.jsonl" "$T/mismatch.md" > /dev/null
[ "$(cat "$T/mismatch.md")" = "the result string" ] && ok "assistant text that is not the result falls back to the result" || bad "mismatch: $(cat "$T/mismatch.md")"
{ asst "$C1"; asst "$C2"; jq -cn '{type:"result",is_error:true,result:"boom"}'; } > "$T/err.jsonl"
python3 "$ROOT/scripts/merge_gate.py" cursor-result "$T/err.jsonl" "$T/err.md" > /dev/null 2>&1; rc=$?
[ "$rc" = 1 ] && ok "error result with assistant text is no answer" || bad "error result rc $rc"

# Builder vendor is checked against commit trailers before seat-usage, and
# merge re-checks the vendors recorded on gate-route.json.
GROK_COMMITS='{"commits":[{"messageHeadline":"c","messageBody":"Co-authored-by: Grok <a@b>\n"}]}'
OPUS_COMMITS='{"commits":[{"messageHeadline":"c","messageBody":"Co-authored-by: Opus 4 <a@b>\n"}]}'
SEVERAL_COMMITS='{"commits":[{"messageHeadline":"a","messageBody":"Co-authored-by: Grok <a@b>\n"},{"messageHeadline":"b","messageBody":"Co-authored-by: Opus 4 <c@d>\n"}]}'

mkdir -p "$T/mismatch/out"; echo stale > "$T/mismatch/out/gate.md"
FAKE_COMMITS="$OPUS_COMMITS" FAKE_ROUTE="$ROUTE_ARGON" run mismatch "$ANSWER"
refused "mismatch" "merge-gate: refused: builder vendor xai does not match the PR commit trailers (anthropic)"
[ -z "$su_log" ] && ok "mismatch: seat-usage not called" || bad "mismatch: seat-usage ran: $su_log"
has "mismatch: commits were read" "$gh_log" "--json commits"
hasnt "mismatch: no note" "$review_out" "merge-gate: note:"
[ ! -e "$T/mismatch/out/gate.md" ] && ok "mismatch: earlier verdict removed" || bad "mismatch: gate.md remains"

FAKE_COMMITS="$GROK_COMMITS" FAKE_ROUTE="$ROUTE_ARGON" run match "$ANSWER"
[ "$review_rc" = 0 ] && ok "match: review rc 0" || bad "match: review rc $review_rc"
[ "$merge_rc" = 0 ] && ok "match: merge rc 0" || bad "match: merge rc $merge_rc"
has "match: gh pr merge" "$gh_log" "gh pr merge $PR --squash --match-head-commit $HEAD_SHA"
has "match: seat-usage got xai" "$su_log" "--builder-vendor xai"
hasnt "match: no note" "$review_out" "merge-gate: note:"
hasnt "match: merge prints no vendor reason" "$merge_out" "recorded reviewer vendor"

FAKE_COMMITS="$SEVERAL_COMMITS" FAKE_ROUTE="$ROUTE_ARGON" run several "$ANSWER"
refused "several" "merge-gate: refused: the PR commit trailers name several vendors or an unrecognised co-author (anthropic, xai), so no single builder vendor can be verified"
[ -z "$su_log" ] && ok "several: seat-usage not called" || bad "several: seat-usage ran: $su_log"
hasnt "several: no note" "$review_out" "merge-gate: note:"

FAKE_COMMITS='{"commits":[]}' BUILDER=anthropic \
  FAKE_ROUTE="$(BUILDER=anthropic route argon google gemini-4-argon-high '["agy","--model","gemini-4-argon-high"]')" \
  run notrailer "$ANSWER"
[ "$review_rc" = 0 ] && ok "no trailer: review rc 0" || bad "no trailer: review rc $review_rc"
[ "$merge_rc" = 0 ] && ok "no trailer: merge rc 0" || bad "no trailer: merge rc $merge_rc"
has "no trailer: note names the caller" "$review_out" "merge-gate: note: no model trailer on the PR commits; builder vendor anthropic is the caller's"
has "no trailer: seat-usage got the caller" "$su_log" "--builder-vendor anthropic"
has "no trailer: gh pr merge" "$gh_log" "pr merge"
hasnt "no trailer: merge prints no vendor reason" "$merge_out" "recorded reviewer vendor"

FAKE_COMMITS="$GROK_COMMITS" BUILDER=grok FAKE_ROUTE="$ROUTE_ARGON" run grokalias "$ANSWER"
[ "$review_rc" = 0 ] && ok "grok alias: review rc 0" || bad "grok alias: review rc $review_rc"
[ "$merge_rc" = 0 ] && ok "grok alias: merge rc 0" || bad "grok alias: merge rc $merge_rc"
has "grok alias: seat-usage got xai" "$su_log" "--builder-vendor xai"
hasnt "grok alias: seat-usage did not get grok" "$su_log" "--builder-vendor grok"
hasnt "grok alias: no note" "$review_out" "merge-gate: note:"
has "grok alias: gh pr merge" "$gh_log" "pr merge"

UNKNOWN_COMMITS='{"commits":[{"messageHeadline":"c","messageBody":"Co-authored-by: Jane Doe <jane@example.com>\n"}]}'
FAKE_COMMITS="$UNKNOWN_COMMITS" BUILDER=anthropic \
  FAKE_ROUTE="$(BUILDER=anthropic route argon google gemini-4-argon-high '["agy","--model","gemini-4-argon-high"]')" \
  run unknowntrailer "$ANSWER"
[ "$review_rc" = 0 ] && ok "unknown trailer: review rc 0" || bad "unknown trailer: review rc $review_rc"
has "unknown trailer: note names the co-author" "$review_out" "merge-gate: note: co-author jane doe names no known model family; builder vendor anthropic is the caller's"
hasnt "unknown trailer: not the no-trailer note" "$review_out" "no model trailer"
has "unknown trailer: summary says vendor_source=caller" "$review_out" "vendor_source=caller"
has "unknown trailer: route records vendor_source caller" "$(cat "$T/unknowntrailer/out/gate-route.json")" '"vendor_source": "caller"'

# Amend 2: Claude and the anthropic.com domain are the anthropic vendor.
CLAUDE_COMMITS='{"commits":[{"messageHeadline":"c","messageBody":"Co-authored-by: Claude <noreply@anthropic.com>\n"}]}'
FAKE_COMMITS="$CLAUDE_COMMITS" BUILDER=anthropic \
  FAKE_ROUTE="$(BUILDER=anthropic route argon google gemini-4-argon-high '["agy","--model","gemini-4-argon-high"]')" \
  run claudetrailer "$ANSWER"
[ "$review_rc" = 0 ] && ok "claude trailer: review rc 0" || bad "claude trailer: review rc $review_rc"
hasnt "claude trailer: no note" "$review_out" "merge-gate: note:"
has "claude trailer: summary says vendor_source=trailer" "$review_out" "vendor_source=trailer"
has "claude trailer: route records vendor_source trailer" "$(cat "$T/claudetrailer/out/gate-route.json")" '"vendor_source": "trailer"'
FAKE_COMMITS="$CLAUDE_COMMITS" FAKE_ROUTE="$ROUTE_ARGON" run claudemismatch "$ANSWER"
refused "claude mismatch" "merge-gate: refused: builder vendor xai does not match the PR commit trailers (anthropic)"
[ -z "$su_log" ] && ok "claude mismatch: seat-usage not called" || bad "claude mismatch: seat-usage ran: $su_log"

# Amend 3: a co-author outside the known families beside a known family is ambiguous.
MIXED_COMMITS='{"commits":[{"messageHeadline":"a","messageBody":"Co-authored-by: Opus 4 <a@b>\n"},{"messageHeadline":"b","messageBody":"Co-authored-by: Jane Doe <jane@example.com>\n"}]}'
FAKE_COMMITS="$MIXED_COMMITS" BUILDER=anthropic \
  FAKE_ROUTE="$(BUILDER=anthropic route argon google gemini-4-argon-high '["agy","--model","gemini-4-argon-high"]')" \
  run mixed "$ANSWER"
refused "mixed" "merge-gate: refused: the PR commit trailers name several vendors or an unrecognised co-author (anthropic, unrecognised co-author jane doe), so no single builder vendor can be verified"
[ -z "$su_log" ] && ok "mixed: seat-usage not called" || bad "mixed: seat-usage ran: $su_log"
[ -z "$run_log" ] && ok "mixed: no reviewer" || bad "mixed: reviewer launched"

# Amend 4: no trailer passes the caller's value through and records where it came from.
has "no trailer: summary says vendor_source=caller" "$(cat "$T/notrailer/review.out")" "vendor_source=caller"
has "no trailer: route records vendor_source caller" "$(cat "$T/notrailer/out/gate-route.json")" '"vendor_source": "caller"'
has "match: summary says vendor_source=trailer" "$(cat "$T/match/review.out")" "vendor_source=trailer"
has "match: route records vendor_source trailer" "$(cat "$T/match/out/gate-route.json")" '"vendor_source": "trailer"'

# Amend 1: the trailers are the PR author's to write, and the docs say so.
has "README documents the trailer risk" "$(cat "$ROOT/README.md")" "The PR author controls the trailers"
has "merge-gate.sh documents the trailer risk" "$(cat "$ROOT/scripts/merge-gate.sh")" "The PR author controls the trailers"

FAKE_COMMITS_FAIL=1 FAKE_ROUTE="$ROUTE_ARGON" run commitsfail "$ANSWER"
refused "commits read failed" "merge-gate: refused: could not read the PR commits for $PR: HTTP 401"
[ -z "$su_log" ] && ok "commits read failed: seat-usage not called" || bad "commits read failed: seat-usage ran: $su_log"
hasnt "commits read failed: no note" "$review_out" "merge-gate: note:"
[ -z "$run_log" ] && ok "commits read failed: no reviewer" || bad "commits read failed: reviewer launched"

FAKE_COMMITS="$GROK_COMMITS" \
  FAKE_ROUTE="$(route grok xai grok-4.7-xhigh '["cursor-agent","--model","grok-4.7-xhigh"]')" \
  run samevendor "$ANSWER"
refused "same vendor" "merge-gate: refused: the recorded reviewer vendor xai is the builder's vendor xai (gate-route.json)"
[ -n "$su_log" ] && ok "same vendor: seat-usage ran" || bad "same vendor: seat-usage did not run"
hasnt "same vendor: no note" "$review_out" "merge-gate: note:"
[ -z "$run_log" ] && ok "same vendor: no reviewer" || bad "same vendor: reviewer launched: $run_log"

merge_saved() { # $1 name, dir already prepared
  : > "$FAKE_GH_LOG"
  "$ROOT/scripts/merge-gate.sh" merge "$PR" "$T/$1" > "$T/$1.merge" 2>&1
  vmerge_rc=$?
  vmerge_out=$(cat "$T/$1.merge")
  vgh=$(cat "$FAKE_GH_LOG")
  [ "$vmerge_rc" = 3 ] && ok "$1: merge rc 3" || bad "$1: merge rc $vmerge_rc"
  hasnt "$1: no gh pr merge" "$vgh" "pr merge"
}
rm -rf "$T/route-equal"; cp -a "$T/pass/out" "$T/route-equal"
python3 - "$T/route-equal/gate-route.json" <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p))
data[0]["builder_vendor"] = "grok"
data[0]["reviewers"][0]["vendor"] = "xai"
json.dump(data, open(p, "w"))
PY
merge_saved route-equal
has "route-equal: same vendor" "$vmerge_out" "reason: the recorded reviewer vendor xai is the builder's vendor xai (gate-route.json)"
has "route-equal: nothing merged" "$vmerge_out" "Nothing was merged."
hasnt "route-equal: verdict still stands" "$vmerge_out" "no gate answer"

rm -rf "$T/route-missing"; cp -a "$T/pass/out" "$T/route-missing"
rm "$T/route-missing/gate-route.json"
merge_saved route-missing
has "route-missing: names the file" "$vmerge_out" "reason: no gate-route.json at"
hasnt "route-missing: verdict still stands" "$vmerge_out" "no gate answer"

rm -rf "$T/route-nobuilder"; cp -a "$T/pass/out" "$T/route-nobuilder"
python3 - "$T/route-nobuilder/gate-route.json" <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p))
del data[0]["builder_vendor"]
json.dump(data, open(p, "w"))
PY
merge_saved route-nobuilder
has "route-nobuilder: missing builder" "$vmerge_out" "reason: no recorded builder vendor (gate-route.json)"
hasnt "route-nobuilder: reviewer was recorded" "$vmerge_out" "no recorded reviewer vendor"

rm -rf "$T/route-bad"; cp -a "$T/pass/out" "$T/route-bad"
printf '{\n' > "$T/route-bad/gate-route.json"
merge_saved route-bad
has "route-bad: unreadable" "$vmerge_out" "reason: gate-route.json is unreadable"

python3 - "$ROOT/scripts" <<'PY' || fail=1
import sys
sys.path.insert(0, sys.argv[1])
from trailer_vendor import normalize_vendor, vendor_from_commits

def doc(*bodies):
    return {"commits": [{"messageHeadline": "h", "messageBody": b} for b in bodies]}

fail = 0
def check(name, got, want):
    global fail
    if got != want:
        print(f"FAIL {name}: got {got!r} want {want!r}")
        fail = 1
    else:
        print(f"ok {name}")

check("one grok", vendor_from_commits(doc("Co-authored-by: Grok <a@b>\n")), "xai")
check("several", vendor_from_commits(doc(
    "Co-authored-by: Grok <a@b>\n",
    "Co-authored-by: Opus <c@d>\n")), "ambiguous")
check("one line two vendors", vendor_from_commits(doc(
    "Co-authored-by: Opus and Grok <a@b>\n")), "ambiguous")
check("none", vendor_from_commits(doc("just a fix\n")), "")
check("cursor only", vendor_from_commits(doc(
    "Co-authored-by: Cursor <cursoragent@cursor.com>\n")), "")
check("opus 4", vendor_from_commits(doc("Co-authored-by: Opus 4 <a@b>\n")), "anthropic")
check("opus4 is not a family", vendor_from_commits(doc("Co-authored-by: opus4 <a@b>\n")), "")
check("myopus is not a family", vendor_from_commits(doc("Co-authored-by: myopus <a@b>\n")), "")
check("case", vendor_from_commits(doc("Co-Authored-By: FABLE <a@b>\n")), "anthropic")
check("unknown jane", __import__("trailer_vendor").unmatched_names(doc("Co-authored-by: Jane Doe <a@b>\n")), ["jane doe"])
check("claude", vendor_from_commits(doc("Co-authored-by: Claude <noreply@anthropic.com>\n")), "anthropic")
check("claude code", vendor_from_commits(doc("Co-authored-by: Claude Code <a@b>\n")), "anthropic")
check("anthropic.com domain", vendor_from_commits(doc("Co-authored-by: Jane Doe <jane@anthropic.com>\n")), "anthropic")
check("lookalike domain", vendor_from_commits(doc("Co-authored-by: Jane Doe <jane@notanthropic.com>\n")), "")
check("domain suffix trick", vendor_from_commits(doc("Co-authored-by: Jane Doe <jane@anthropic.com.evil.test>\n")), "")
check("claudette is not a family", vendor_from_commits(doc("Co-authored-by: Claudette <a@b>\n")), "")
check("known plus unknown is ambiguous", vendor_from_commits(doc(
    "Co-authored-by: Opus 4 <a@b>\n", "Co-authored-by: Jane Doe <j@example.com>\n")), "ambiguous")
check("known plus cursor is not ambiguous", vendor_from_commits(doc(
    "Co-authored-by: Opus 4 <a@b>\n", "Co-authored-by: Cursor Agent <cursoragent@cursor.com>\n")), "anthropic")
check("unknown alone is not a vendor", vendor_from_commits(doc("Co-authored-by: Jane Doe <j@example.com>\n")), "")
check("unknown skips cursor", __import__("trailer_vendor").unmatched_names(doc("Co-authored-by: Cursor <a@b>\n")), [])
check("normalize grok", normalize_vendor("Grok"), "xai")
check("normalize xai", normalize_vendor("XAI"), "xai")
sys.exit(fail)
PY

exit "$fail"
