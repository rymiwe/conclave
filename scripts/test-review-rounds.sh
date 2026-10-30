#!/usr/bin/env bash
# Review rounds on a synthetic repo: round 1 reviews the PR, rounds 2 and 3
# review only the delta since the last reviewed head and carry open findings,
# round 4 is refused with a park note. gh is a stub that fails if called.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }
work="$(mktemp -d)"
trap 'review_cleanup_worktrees; rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/council"
printf '#!/bin/sh\necho "gh $*" >>"%s/gh-calls"\nexit 1\n' "$work" >"$work/bin/gh"
chmod +x "$work/bin/gh"
export PATH="$work/bin:$PATH"
export CONCLAVE_ROUNDS_DIR="$work/rounds"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
g() { command git -c commit.gpgsign=false "$@"; }

g init -q --bare "$work/origin.git"
g init -q -b master "$work/app"
REPO="$work/app"
for f in a b c d; do printf '%s1\n' "$f" >"$REPO/$f.txt"; done
g -C "$REPO" add . && g -C "$REPO" commit -qm base
g -C "$REPO" remote add origin "$work/origin.git"
g -C "$REPO" push -q origin master
g -C "$REPO" checkout -qb feature

push_head() { # $1 = file to change; prints the new head
  printf 'change %s\n' "$(date +%s%N)" >>"$REPO/$1"
  g -C "$REPO" commit -qam "edit $1"
  g -C "$REPO" push -q -f origin "HEAD:refs/pull/7/head"
  g -C "$REPO" rev-parse HEAD
}

# One round as review_run sequences it, with gh pr view replaced by its values.
review_round() { # $1 = ballot id, $2 = 1 for --delta
  ID="$1" FLAG_DELTA="$2" FLAG_PR=7 FLAG_BASE="" FLAG_HEAD="" FLAG_WHOLE=0 HEADER_PR="" HEADER_BRANCH=""
  DIR="$work/council/$ID"
  mkdir -p "$DIR"
  BRIEF="$DIR/brief.md"
  printf 'Review the change.\n' >"$BRIEF"
  VOTERS="claude,codex"
  REVIEW_DELTA_BASE="" REVIEW_ROUND=""
  PR_URL=https://github.com/example/app/pull/7 PR_BASE=master
  PR_OID="$(g -C "$REPO" rev-parse HEAD)"
  review_parse_shortstat "$(g -C "$REPO" diff --shortstat master HEAD)"
  PR_ADD="$SS_INS" PR_DEL="$SS_DEL"
  review_target_mode
  python3 "$ROOT/scripts/review_rounds.py" snapshot --rounds-dir "$CONCLAVE_ROUNDS_DIR" --ballot-dir "$DIR"
  rm -f "$DIR/findings.json" "$DIR/round.json" "$DIR/carried-findings.md" "$DIR/review-files.txt"
  review_rounds_plan
  review_write_claude_brief "$DIR/voter-claude.brief.md" || fail "$REVIEW_BRIEF_REJECT"
  review_alloc_scratch
  review_build_and_prove
  review_rounds_record
  wt="$(printf '%s\n' "$REVIEW_WORKTREES" | head -1)"
  printf -- '--- %s: what the legs review (git diff HEAD --stat in a leg worktree)\n' "$ID"
  g -C "$wt" diff HEAD --stat
  printf -- '--- %s: %s\n' "$ID" "$DIFFSHAPE"
  review_cleanup_worktrees
}

same_files() { # $1 label, $2 want (newline list), $3 file
  [ "$(cat "$3")" = "$2" ] || fail "$1: reviewed files $(tr '\n' ' ' <"$3"), want $(printf '%s' "$2" | tr '\n' ' ')"
}

h1="$(push_head a.txt)"
review_round r1 0
same_files "round 1 reviews the whole PR" "a.txt" "$work/council/r1/review-files.txt"
cat >"$work/council/r1/findings.json" <<'JSON'
{"target": "pr 7", "findings": [
  {"id": "f1", "claim": "a.txt drops the last line on empty input", "file": "a.txt", "line": 1},
  {"id": "f2", "claim": "a.txt never handles the second case", "file": "a.txt", "line": 2}]}
JSON

h2="$(push_head b.txt)"
review_round r2 1
want="$(g -C "$REPO" diff --name-only "$h1" "$h2")"
printf -- '--- r2: git diff --name-only last-reviewed..new = %s..%s\n%s\n' "${h1:0:12}" "${h2:0:12}" "$want"
same_files "round 2 reviews only last-reviewed..new" "$want" "$work/council/r2/review-files.txt"
same_files "round 2 is not the whole PR" "b.txt" "$work/council/r2/review-files.txt"
jq -e --arg b "$h1" --arg h "$h2" '.round == 2 and .mode == "delta" and .base == $b and .head == $h' \
  "$work/council/r2/round.json" >/dev/null || fail "round 2 round.json: $(cat "$work/council/r2/round.json")"
[ "$(jq -r '[.carried_open[].id] | join(",")' "$work/council/r2/round.json")" = "r1-f1,r1-f2" ] \
  || fail "round 2 carries round 1 findings"
grep -q "changes since ${h1:0:12} only (review round 2)" "$work/council/r2/voter-claude.brief.md" \
  || fail "round 2 brief names the delta"
grep -q "Review the change." "$work/council/r2/voter-claude.brief.md" || fail "round 2 brief has the chair body"
grep -q "drops the last line" "$work/council/r2/voter-claude.brief.md" \
  && fail "the reviewer brief lists a carried finding"
grep -q "r1-f2" "$work/council/r2/carried-findings.md" || fail "chair context lists carried findings"
cat >"$work/council/r2/findings.json" <<'JSON'
{"target": "pr 7 delta", "resolved": ["r1-f1"], "findings": [
  {"id": "f1", "claim": "b.txt fix is applied on one path only", "file": "b.txt", "line": 1}]}
JSON

h3="$(push_head c.txt)"
review_round r3 1
same_files "round 3 reviews only its delta" "c.txt" "$work/council/r3/review-files.txt"
[ "$(jq -r '[.carried_open[].id] | join(",")' "$work/council/r3/round.json")" = "r1-f2,r2-f1" ] \
  || fail "round 3 carries what round 2 left open: $(jq -c '.carried_open' "$work/council/r3/round.json")"
printf '{"target": "pr 7 delta", "resolved": ["r2-f1"], "findings": [{"id": "f1", "claim": "c.txt loops on a blank line", "file": "c.txt", "line": 1}]}\n' \
  >"$work/council/r3/findings.json"

review_round r3 1 >/dev/null
[ "$(jq -r '.rounds | length' "$work/rounds/github.com_example_app_7.json")" = 3 ] || fail "a rerun of round 3 added a round"
printf '{"target": "pr 7 delta", "resolved": ["r2-f1"], "findings": [{"id": "f1", "claim": "c.txt loops on a blank line", "file": "c.txt", "line": 1}, {"id": "f2", "claim": "c.txt rerun found this", "file": "c.txt", "line": 2}]}\n' \
  >"$work/council/r3/findings.json"
out="$(python3 "$ROOT/scripts/review_rounds.py" plan --rounds-dir "$CONCLAVE_ROUNDS_DIR" \
  --pr-url https://github.com/example/app/pull/7 --ballot r1 --head "$h1" --mode pr)" \
  || fail "a rerun of round 1 after round 3 was refused"
[ "$out" = "1 - 1" ] || fail "a rerun of round 1 after round 3 is round 1, got '$out'"

h4="$(push_head d.txt)"
rc=0
( review_round r4 1 ) >"$work/r4.out" 2>&1 || rc=$?
printf -- '--- r4: exit %s\n' "$rc"
cat "$work/r4.out"
[ "$rc" -eq 3 ] || fail "round 4 exit $rc, want 3"
grep -q "refused round 4" "$work/r4.out" || fail "round 4 message"
[ ! -e "$work/council/r4/review-files.txt" ] || fail "round 4 built worktrees"
park="$work/rounds/github.com_example_app_7.park.md"
[ -s "$park" ] || fail "no park note"
printf -- '--- park note %s\n' "$park"
cat "$park"
for id in r1-f2 r3-f1 r3-f2; do grep -q "$id" "$park" || fail "park note misses $id"; done
for id in r1-f1 r2-f1; do grep -q "$id" "$park" && fail "park note lists resolved $id"; done
[ ! -e "$work/gh-calls" ] || fail "gh was called: $(cat "$work/gh-calls")"

# --delta refusals and the moved fork point, on a fresh PR log.
export CONCLAVE_ROUNDS_DIR="$work/rounds2"
rc=0; ( review_round d1 1 ) >"$work/d1.out" 2>&1 || rc=$?
[ "$rc" -ne 0 ] && grep -q "needs an earlier round" "$work/d1.out" || fail "delta with no round 1: $rc $(cat "$work/d1.out")"
review_round p1 0 >/dev/null
rc=0; ( review_round p2 1 ) >"$work/p2.out" 2>&1 || rc=$?
[ "$rc" -ne 0 ] && grep -q "nothing to review" "$work/p2.out" || fail "delta at the same head: $rc $(cat "$work/p2.out")"
g -C "$REPO" checkout -q master
printf 'upstream\n' >"$REPO/e.txt"; g -C "$REPO" add e.txt; g -C "$REPO" commit -qm upstream
g -C "$REPO" push -q origin master
g -C "$REPO" checkout -q feature
g -C "$REPO" rebase -q master
g -C "$REPO" push -q -f origin "HEAD:refs/pull/7/head"
rc=0; ( review_round p3 1 ) >"$work/p3.out" 2>&1 || rc=$?
[ "$rc" -ne 0 ] && grep -q "fork point from master moved" "$work/p3.out" || fail "rebased delta: $rc $(cat "$work/p3.out")"
grep -q "Revision recipes" "$work/p3.out" || fail "rebased delta names the manual recipe"

# The Astra title names the target in every mode.
REVIEW_MODE=pr FLAG_PR=7 REVIEW_DELTA_BASE=""
[ "$(review_codex_title)" = "7" ] || fail "pr title: $(review_codex_title)"
REVIEW_DELTA_BASE=0123456789abcdef
[ "$(review_codex_title)" = "7, changes since 0123456789ab" ] || fail "delta title: $(review_codex_title)"
REVIEW_MODE=refs FLAG_HEAD=topic FLAG_BASE=master REVIEW_DELTA_BASE=""
[ "$(review_codex_title)" = "topic against master" ] || fail "refs title: $(review_codex_title)"
REVIEW_MODE=whole
[ "$(review_codex_title)" = "whole codebase" ] || fail "whole title: $(review_codex_title)"

# The real entry point. gh names a head the repo lacks, so if the cap ever
# failed to refuse, the run would die at the fetch, before any leg.
cli="$work/rounds3"
mkdir -p "$cli"
jq -n '{pr_url: "https://github.com/example/app/pull/7", rounds: [range(1;4) |
  {round: ., ballot: "cli\(.)", mode: "pr", base: ("0" * 40), head: ("\(.)" * 40), carried_open: []}]}' \
  >"$cli/github.com_example_app_7.json"
cat >"$work/bin/gh" <<'SH'
#!/bin/sh
[ "$1 $2" = "pr view" ] || exit 1
case "$*" in *--json\ number*) ;; *) echo '[]'; exit 0 ;; esac
printf '{"number":7,"title":"t","url":"https://github.com/example/app/pull/7","headRefName":"feature","baseRefName":"master","state":"OPEN","headRefOid":"%040d","additions":1,"deletions":0,"changedFiles":1}\n' 9
SH
id="test-rounds-cli-$$"
mkdir -p "$ROOT/tmp/council/$id"
printf 'kind: review\n\nReview the change.\n' >"$ROOT/tmp/council/$id/brief.md"
rc=0
CONCLAVE_ROUNDS_DIR="$cli" CONCLAVE_PREFLIGHT=0 CONCLAVE_SKIP_UPDATE_CHECK=1 CONCLAVE_JEV_OP=/bin/false \
  "$ROOT/scripts/conclave.sh" "$id" --repo "$REPO" --pr 7 --delta --voters claude,codex >"$work/cli.out" 2>&1 || rc=$?
rm -rf "$ROOT/tmp/council/$id"
printf -- '--- conclave.sh round 4: exit %s\n' "$rc"
tail -3 "$work/cli.out"
[ "$rc" -eq 3 ] || fail "conclave.sh round 4 exit $rc: $(cat "$work/cli.out")"
[ -s "$cli/github.com_example_app_7.park.md" ] || fail "conclave.sh round 4 wrote no park note"
grep -q 'refused round 4' "$work/cli.out" || fail "conclave.sh round 4 message"

echo "test-review-rounds: all passed"
