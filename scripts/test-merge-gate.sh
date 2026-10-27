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
jq -cn --rawfile r "$FAKE_REPLY" '{type:"result",is_error:false,result:$r}'
A
cat > "$T/bin/muse" <<'A'
#!/usr/bin/env bash
"$(dirname "$0")/logargv" muse "$@"
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
  printf '[{"role": "review", "mode": "default", "runner": %s, "reason": "ranked", "reviewers": [{"family": "%s", "vendor": "%s", "model": "%s", "runner": %s}], "skipped": %s}]' \
    "$4" "$1" "$2" "$3" "$4" "${5:-[]}"
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
  "$ROOT/scripts/merge-gate.sh" review "$R" main feat "$PR" "$d/out" xai > "$d/review.out" 2>&1; review_rc=$?
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

# The router decides; when it asks, fails, or gives no launch argv, refuse
# before any reviewer runs. Each refusal also clears the earlier verdict, so
# merge refuses too.
refused() { # $1 case, $2 expected text
  [ "$review_rc" = 3 ] && ok "$1: review rc 3" || bad "$1: review rc $review_rc"
  [ "$(printf '%s\n' "$review_out" | wc -l)" = 1 ] && ok "$1: one-line message" || bad "$1: not one line: $review_out"
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
FAKE_ROUTE="$(route grok xai grok-4.7-xhigh '["cursor-agent","--model","grok-4.7-xhigh"]')" run cursor "$ANSWER"
passes cursor cursor-agent
has "cursor: sandbox enabled" "$run_log" "--sandbox enabled"
hasnt "cursor: no --force" "$run_log" "--force"
has "cursor: slug used as given" "$run_log" "--model grok-4.7-xhigh "
hasnt "cursor: no model listing for a slug" "$run_log" "cursor-agent models"
hasnt "cursor: workspace is not the repo" "$run_log" "--workspace $R"
has "cursor: prompt argument carries the diff" "$(cat "$FAKE_PROMPT_ARG")" '+b'
FAKE_ROUTE="$(route fable anthropic fable '["cursor-agent","--model","fable"]')" \
  FAKE_CURSOR_MODELS=$'claude-fable-8-thinking-xhigh - Fable 8\nclaude-fable-9-thinking-xhigh - Fable 9\nclaude-fable-9-thinking-high\ngrok-4.7-xhigh' \
  run cursorfable "$ANSWER"
passes cursorfable cursor-agent
has "cursor fable: resolved to the newest id" "$run_log" "--model claude-fable-9-thinking-xhigh "
FAKE_ROUTE="$(route fable anthropic fable-5 '["claude","--model","fable-5"]')" run claude "$ANSWER"
passes claude claude
has "claude: dontAsk" "$run_log" "--permission-mode dontAsk"
has "claude: model" "$run_log" "--model fable-5 "
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

exit "$fail"
