#!/usr/bin/env bash
# merge-gate.sh review + merge against a temp repo with stubbed gh and agy
# (no network, no quota): a normal diff passes and gh pr merge runs; one_way:
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
cat > "$T/bin/agy" <<'A'
#!/usr/bin/env bash
echo "agy $*" >> "$FAKE_AGY_LOG"
cat > "$FAKE_AGY_STDIN"
jq -cn --rawfile r "$FAKE_REPLY" '{event:"init",init:{agent:"conclave-voter"}},{event:"result",result:{status:"SUCCESS",response:$r}}'
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
chmod +x "$T/bin/gh" "$T/bin/agy" "$T/bin/seat-usage"
export MERGE_GATE_GH="$T/bin/gh" MERGE_GATE_AGY="$T/bin/agy" MERGE_GATE_SEAT_USAGE="$T/bin/seat-usage"
export FAKE_GH_LOG="$T/gh.log" FAKE_AGY_STDIN="$T/agy.stdin" FAKE_REPLY="$T/reply.md"
export FAKE_AGY_LOG="$T/agy.log" FAKE_SU_LOG="$T/su.log"
ROUTE_ARGON='[{"role": "review", "mode": "default", "runner": "agy", "reason": "ranked", "reviewers": [{"family": "argon", "vendor": "google", "model": "gemini-4-argon-high"}], "skipped": [{"family": "astra", "model": "gpt-6-astra", "code": "runner"}]}]'
export FAKE_ROUTE="$ROUTE_ARGON"

R="$T/repo"; git init -q -b main "$R"
echo a > "$R/f"; git -C "$R" add f; git -C "$R" commit -qm base
git -C "$R" checkout -q -b feat; echo b >> "$R/f"; git -C "$R" commit -qam change
HEAD_SHA="$(git -C "$R" rev-parse feat)"
PR=https://github.com/o/r/pull/1

run() { # $1 case name, reply text in $2; leaves $T/$1/{review,merge}.out
  local d="$T/$1"; mkdir -p "$d"; printf '%s\n' "$2" > "$FAKE_REPLY"; : > "$FAKE_GH_LOG"
  : > "$FAKE_AGY_LOG"; : > "$FAKE_SU_LOG"
  "$ROOT/scripts/merge-gate.sh" review "$R" main feat "$PR" "$d/out" xai > "$d/review.out" 2>&1; review_rc=$?
  "$ROOT/scripts/merge-gate.sh" merge "$PR" "$d/out" > "$d/merge.out" 2>&1; merge_rc=$?
  merge_out="$(cat "$d/merge.out")"; gh_log="$(cat "$FAKE_GH_LOG")"
  review_out="$(cat "$d/review.out")"; agy_log="$(cat "$FAKE_AGY_LOG")"; su_log="$(cat "$FAKE_SU_LOG")"
  echo "--- $1: review rc=$review_rc merge rc=$merge_rc"; sed 's/^/    /' "$d/merge.out"
}

# 1. normal diff: one_way: no, no label -> merges, pinned to the reviewed head.
run pass $'approve\n\nFindings:\nNone.\n\nChecked and found clean:\n- f\n\none_way: no'
[ "$review_rc" = 0 ] && ok "pass: review rc 0" || bad "pass: review rc $review_rc"
[ "$merge_rc" = 0 ] && ok "pass: merge rc 0" || bad "pass: merge rc $merge_rc"
has "pass: gh pr merge invoked squash at the reviewed sha" "$gh_log" "gh pr merge $PR --squash --match-head-commit $HEAD_SHA"
has "pass: labels checked" "$gh_log" "gh pr view $PR --json labels"
p="$(cat "$FAKE_AGY_STDIN")"
has "prompt asks for the one_way line" "$p" 'exactly `one_way: yes` or `one_way: no`'
has "prompt asks for one_way_at file:line" "$p" 'one_way_at: <file>:<line>'
has "prompt carries the diff" "$p" '+b'
has "verdict json written" "$(cat "$T/pass/out/gate-verdict.json")" '"one_way": "no"'
has "pass: router asked with the builder vendor and the agy runner" "$su_log" "seat-usage --route review --cached --json --builder-vendor xai --runner agy"
has "pass: agy runs the router's model" "$agy_log" "--model gemini-4-argon-high "
has "pass: review names the reviewer" "$review_out" "merge-gate: reviewer gemini-4-argon-high"
has "pass: route output kept" "$(cat "$T/pass/out/gate-route.json")" '"runner": "agy"'

# The router decides; when nothing agy can run qualifies, refuse before agy.
# Each refusal also clears the earlier verdict, so merge refuses too.
refused() { # $1 case, $2 expected text
  [ "$review_rc" = 3 ] && ok "$1: review rc 3" || bad "$1: review rc $review_rc"
  [ "$(printf '%s\n' "$review_out" | wc -l)" = 1 ] && ok "$1: one-line message" || bad "$1: not one line: $review_out"
  has "$1: reason" "$review_out" "$2"
  [ -z "$agy_log" ] && ok "$1: agy not called" || bad "$1: agy called: $agy_log"
  [ "$merge_rc" = 3 ] && ok "$1: merge refuses" || bad "$1: merge rc $merge_rc"
  hasnt "$1: no gh pr merge" "$gh_log" "pr merge"
}
mkdir -p "$T/stale/out"; cp "$T/pass/out/"gate* "$T/stale/out/"
FAKE_ROUTE='[{"role": "review", "mode": "ask", "runner": "agy", "reason": "need 1 agy-runnable review seat(s) outside Google; 0 eligible: ask", "reviewers": [], "skipped": []}]' \
  run stale $'approve\none_way: no'
refused "route asks" "merge-gate: refused: no agy-runnable reviewer: need 1 agy-runnable review seat(s) outside Google; 0 eligible: ask"
FAKE_ROUTE='[{"role": "review", "mode": "default", "reason": "ranked", "reviewers": [{"family": "astra", "vendor": "openai", "model": "gpt-6-astra"}]}]' \
  run oldroute $'approve\none_way: no'
refused "old seat-usage without --runner" "merge-gate: refused: seat-usage did not apply --runner agy; update quartermaster"
FAKE_ROUTE='' FAKE_ROUTE_RC=2 FAKE_ROUTE_ERR='unknown builder vendor nope; vendors: anthropic, google, meta, openai, xai' \
  run routefail $'approve\none_way: no'
refused "router exits nonzero" "merge-gate: refused: seat-usage --route review --runner agy failed: unknown builder vendor nope"
FAKE_ROUTE='not json' run notjson $'approve\none_way: no'
refused "router output not JSON" "merge-gate: refused: seat-usage output is not JSON"
FAKE_ROUTE='[{"role": "review", "mode": "default", "runner": "agy", "reviewers": [{"model": "--sandbox x"}]}]' \
  run badmodel $'approve\none_way: no'
refused "unusable model id" "merge-gate: refused: seat-usage reviewer has no usable model id"

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
