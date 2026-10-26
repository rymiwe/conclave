#!/usr/bin/env bash
# merge-gate.sh review + merge against a temp repo with stubbed gh and agy
# (no network, no quota): a normal diff passes and gh pr merge runs; one_way:
# yes, a one-way label, a missing verdict, and unreadable labels are refused.
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
cat > "$FAKE_AGY_STDIN"
jq -cn --rawfile r "$FAKE_REPLY" '{event:"init",init:{agent:"conclave-voter"}},{event:"result",result:{status:"SUCCESS",response:$r}}'
A
chmod +x "$T/bin/gh" "$T/bin/agy"
export MERGE_GATE_GH="$T/bin/gh" MERGE_GATE_AGY="$T/bin/agy"
export FAKE_GH_LOG="$T/gh.log" FAKE_AGY_STDIN="$T/agy.stdin" FAKE_REPLY="$T/reply.md"

R="$T/repo"; git init -q -b main "$R"
echo a > "$R/f"; git -C "$R" add f; git -C "$R" commit -qm base
git -C "$R" checkout -q -b feat; echo b >> "$R/f"; git -C "$R" commit -qam change
HEAD_SHA="$(git -C "$R" rev-parse feat)"
PR=https://github.com/o/r/pull/1

run() { # $1 case name, reply text in $2; leaves $T/$1/{review,merge}.out
  local d="$T/$1"; mkdir -p "$d"; printf '%s\n' "$2" > "$FAKE_REPLY"; : > "$FAKE_GH_LOG"
  "$ROOT/scripts/merge-gate.sh" review "$R" main feat "$PR" "$d/out" m > "$d/review.out" 2>&1; review_rc=$?
  "$ROOT/scripts/merge-gate.sh" merge "$PR" "$d/out" > "$d/merge.out" 2>&1; merge_rc=$?
  merge_out="$(cat "$d/merge.out")"; gh_log="$(cat "$FAKE_GH_LOG")"
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
FAKE_GH_LABELS_FAIL=1 run nolabels $'approve\none_way: no'
[ "$merge_rc" = 3 ] && ok "unreadable labels refused" || bad "unreadable labels rc $merge_rc"
hasnt "unreadable labels: no gh pr merge" "$gh_log" "pr merge"
out="$("$ROOT/scripts/merge-gate.sh" merge "$PR" "$T/nowhere" 2>&1)"; rc=$?
[ "$rc" = 3 ] && ok "no gate dir refused" || bad "no gate dir rc $rc"
has "no gate dir: reason" "$out" "no gate answer at"

exit "$fail"
