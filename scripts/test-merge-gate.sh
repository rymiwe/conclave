#!/usr/bin/env bash
# merge-gate.sh review + merge against a temp repo with stubbed gh, seat-usage
# and every reviewer binary (no network, no quota): the gate runs the reviewer
# seat-usage names, read-only; a normal diff passes and gh pr merge runs; one_way:
# yes, a one-way label, a missing or garbled verdict, and unreadable labels
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
  "pr view "*) echo "Title: t"; echo; echo "body";;
  "pr merge "*) echo "merged";;
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
has "pass: route output kept" "$(cat "$T/pass/out/gate-route.json")" '"runner": ["agy","--model","gemini-4-argon-high"]'
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
  FAKE_ROUTE="$(route "$3" "$4" "$5" "$2")" "$ROOT/scripts/merge-gate.sh" review "$R" main big "$PR" "$d/out" xai \
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
big agy '["agy","--model","gemini-4-argon-high"]' argon google gemini-4-argon-high
big muse '["muse","exec","--model","muse-wonder-2"]' muse meta muse-wonder-2

# 2. one_way: yes -> refused, names the reason and the file:line, no merge.
run yes $'approve\n\nFindings:\nNone.\n\n**one_way: yes**\none_way_at: db/migrate/002_drop.sql:3 drops column users.email'
[ "$merge_rc" = 3 ] && ok "yes: merge rc 3" || bad "yes: merge rc $merge_rc"
has "yes: reason" "$merge_out" "reason: gate verdict is one_way: yes"
has "yes: file:line" "$merge_out" "one-way step: db/migrate/002_drop.sql:3 drops column users.email"
has "yes: needs Ryan's yes" "$merge_out" "needs Ryan's explicit yes in a separate PR"
hasnt "yes: no gh pr merge" "$gh_log" "pr merge"

# 3. one-way label with a clean verdict -> refused.
FAKE_LABELS='{"name":"docs"},{"name":"one-way"}' run label $'approve\n\none_way: no'
[ "$merge_rc" = 3 ] && ok "label: merge rc 3" || bad "label: merge rc $merge_rc"
has "label: reason" "$merge_out" 'reason: the PR has the `one-way` label'
has "label: needs Ryan's yes" "$merge_out" "needs Ryan's explicit yes in a separate PR"
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

# Verdict parsing: the value must end at end of line or a non-lowercase character.
parse_of() { printf '%s\n' "$1" > "$T/parse.md"; python3 "$ROOT/scripts/merge_gate.py" parse "$T/parse.md"; }
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
refused "several" "merge-gate: refused: the PR commit trailers name several vendors (anthropic, xai), so no single builder vendor can be verified"
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

CLAUDE_COMMITS='{"commits":[{"messageHeadline":"c","messageBody":"Co-authored-by: Claude <noreply@anthropic.com>\n"}]}'
FAKE_COMMITS="$CLAUDE_COMMITS" BUILDER=anthropic \
  FAKE_ROUTE="$(BUILDER=anthropic route argon google gemini-4-argon-high '["agy","--model","gemini-4-argon-high"]')" \
  run unknowntrailer "$ANSWER"
[ "$review_rc" = 0 ] && ok "unknown trailer: review rc 0" || bad "unknown trailer: review rc $review_rc"
has "unknown trailer: note names the co-author" "$review_out" "merge-gate: note: co-author claude names no known model family; builder vendor anthropic is the caller's"
hasnt "unknown trailer: not the no-trailer note" "$review_out" "no model trailer"

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
check("unknown claude", __import__("trailer_vendor").unmatched_names(doc("Co-authored-by: Claude <a@b>\n")), ["claude"])
check("unknown skips cursor", __import__("trailer_vendor").unmatched_names(doc("Co-authored-by: Cursor <a@b>\n")), [])
check("normalize grok", normalize_vendor("Grok"), "xai")
check("normalize xai", normalize_vendor("XAI"), "xai")
sys.exit(fail)
PY

exit "$fail"
