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
mkdir -p "$work/bin" "$work/council" "$work/home/.codex"
printf '#!/bin/sh\necho "gh $*" >>"%s/gh-calls"\nexit 1\n' "$work" >"$work/bin/gh"
# Stubs only. A codex with no version line is skipped, and a later real
# binary on PATH wins, so CONCLAVE_CODEX_BIN names this one. The catalog
# is what muse-models.py reads; it does not start muse.
cat >"$work/bin/codex" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "codex-cli 0.200.0"; exit 0; fi
exit 0
EOF
cat >"$work/bin/claude" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "claude 0"; exit 0; fi
exit 0
EOF
cat >"$work/bin/grok" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "models" ]; then echo "Default model: grok-test-1"; exit 0; fi
if [ "${1:-}" = "--version" ]; then echo "grok 0"; exit 0; fi
exit 0
EOF
cat >"$work/bin/cursor-agent" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "models" ]; then
  printf '%s\n' claude-fable-9-9-thinking-xhigh grok-9.9-xhigh grok-9.9-xhigh-fast grok-9.9-high-fast
  exit 0
fi
if [ "${1:-}" = "--version" ]; then echo "cursor-agent 0"; exit 0; fi
exit 0
EOF
cat >"$work/bin/muse" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "muse 0"; exit 0; fi
exit 0
EOF
chmod +x "$work/bin/gh" "$work/bin/codex" "$work/bin/claude" "$work/bin/grok" "$work/bin/cursor-agent" "$work/bin/muse"
cat >"$work/home/.codex/models_cache.json" <<'EOF'
{"models": [{"slug": "gpt-test-astra", "visibility": "list", "priority": 1}]}
EOF
printf '[user]\n\tname = t\n\temail = t@example.com\n' >"$work/home/.gitconfig"
cat >"$work/muse-catalog.json" <<'EOF'
{"models":[
  {"modelId":"muse-spark-1.3","releaseDate":"2026-11-01"},
  {"modelId":"muse-wonder-1.0","releaseDate":"2026-10-12"},
  {"modelId":"muse-wonder-full-1.0","releaseDate":"2026-10-12"}
]}
EOF
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

fake_legs() { # $1 = ok or fail; what the legs left in $DIR
  local v
  for v in claude codex; do
    rm -f "$DIR/voter-$v.fail" "$DIR/voter-$v.md" "$DIR/voter-$v.json" "$DIR/voter-$v.model.txt"
    if [ "$1" = ok ]; then
      printf '%s: ok\n' "$v" >"$DIR/voter-$v.model.txt"
      printf 'report\n' >"$(review_report_file "$v")"
    else
      printf 'leg failed\n' >"$DIR/voter-$v.fail"
    fi
  done
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
  rm -f "$DIR/findings.json" "$DIR/round.json" "$DIR/carried-findings.md" "$DIR/review-files.txt" "$DIR/round-refused.md"
  review_rounds_plan
  review_write_claude_brief "$DIR/voter-claude.brief.md" || fail "$REVIEW_BRIEF_REJECT"
  review_alloc_scratch
  review_build_and_prove
  review_rounds_files
  review_rounds_plan recheck
  fake_legs "${LEGS:-ok}"
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
[ "$out" = "1 - 1 $h3 3" ] || fail "a rerun of round 1 after round 3 is round 1, got '$out'"

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

# A launch where every leg failed: same count, same delta base, and the next
# --delta still reviews everything since the last completed round.
export CONCLAVE_ROUNDS_DIR="$work/rounds-fail"
log="$CONCLAVE_ROUNDS_DIR/github.com_example_app_7.json"
review_round f1 0 >/dev/null
hf1="$(g -C "$REPO" rev-parse HEAD)"
cp "$log" "$work/before.json"
hf2="$(push_head a.txt)"
LEGS=fail review_round f2 1 >"$work/f2.out" 2>&1
cmp "$log" "$work/before.json" || fail "a failed launch changed the round log"
printf -- '--- failed launch\n'; grep -i 'not recorded' "$work/f2.out"
[ ! -e "$work/council/f2/round.json" ] || fail "a failed launch wrote round.json"
hf3="$(push_head b.txt)"
review_round f3 1 >/dev/null
same_files "the delta after a failed launch covers it" "$(printf 'a.txt\nb.txt')" "$work/council/f3/review-files.txt"
jq -e --arg b "$hf1" '.round == 2 and .base == $b' "$work/council/f3/round.json" >/dev/null \
  || fail "after a failed launch: $(cat "$work/council/f3/round.json")"
printf -- '--- after the failed launch: round 2 base %s (last completed head), files: %s\n' \
  "${hf1:0:12}" "$(tr '\n' ' ' <"$work/council/f3/review-files.txt")"

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

# Two concurrent ballots with 2 rounds recorded, both past plan: one takes
# round 3, the other is refused.
seed_two() { # $1 = rounds dir; a log with 2 recorded rounds
  mkdir -p "$1"
  jq -n '{pr_url: "https://github.com/example/app/pull/7", rounds: [range(1;3) |
    {round: ., ballot: "seed\(.)", mode: "pr", base: ("0" * 40), head: ("\(.)" * 40), carried_open: []}]}' \
    >"$1/github.com_example_app_7.json"
}
trials=25
for trial in $(seq "$trials"); do
  export CONCLAVE_ROUNDS_DIR="$work/rounds-c$trial"
  seed_two "$CONCLAVE_ROUNDS_DIR"
  mkfifo "$work/go"
  exec 9<>"$work/go"
  a="c$trial-a" b="c$trial-b"
  ( read -r _ <&9; review_round "$a" 0 ) >"$work/$a.out" 2>&1 & pa=$!
  ( read -r _ <&9; review_round "$b" 0 ) >"$work/$b.out" 2>&1 & pb=$!
  printf 'go\ngo\n' >&9
  ra=0; rb=0; wait "$pa" || ra=$?; wait "$pb" || rb=$?
  exec 9>&-
  rm -f "$work/go"
  case "$ra $rb" in
    "0 3") won="$a" lost="$b" ;;
    "3 0") won="$b" lost="$a" ;;
    *) fail "concurrent ballots exit $ra $rb: $(cat "$work/$a.out" "$work/$b.out")" ;;
  esac
  [ "$(jq -c '[(.rounds | length), .rounds[-1].round, .rounds[-1].ballot]' "$CONCLAVE_ROUNDS_DIR/github.com_example_app_7.json")" = "[3,3,\"$won\"]" ] \
    || fail "round 3: $(cat "$CONCLAVE_ROUNDS_DIR/github.com_example_app_7.json")"
  park="$CONCLAVE_ROUNDS_DIR/github.com_example_app_7.park.md"
  [ -s "$park" ] || fail "refused ballot wrote no park note"
  grep -q "ballot $lost" "$park" || fail "park note names another ballot"
  grep -q "ballot $won" "$park" || fail "park note does not name the winning round"
done
printf -- '--- concurrent ballots, %s trials; last trial: %s exit 0 round 3, %s exit 3\n' "$trials" "$won" "$lost"
printf -- '--- refused ballot output\n'; tail -2 "$work/$lost.out"
printf -- '--- park note %s\n' "$park"; cat "$park"

# A cap refusal at record: exit 3 from review_rounds_record, and both notes.
export CONCLAVE_ROUNDS_DIR="$work/rounds-rec"
mkdir -p "$CONCLAVE_ROUNDS_DIR"
jq -n '{pr_url: "https://github.com/example/app/pull/7", rounds: [range(1;4) |
  {round: ., ballot: "seed\(.)", mode: "pr", base: ("0" * 40), head: ("\(.)" * 40), carried_open: []}]}' \
  >"$CONCLAVE_ROUNDS_DIR/github.com_example_app_7.json"
ID=late DIR="$work/council/late" REVIEW_WORKTREES="$REPO" REVIEW_DELTA_BASE="" PR_URL=https://github.com/example/app/pull/7
REVIEW_LEG_BASE="$(g -C "$REPO" rev-parse master)" REVIEW_HEAD_OID="$(g -C "$REPO" rev-parse HEAD)"
REVIEW_PLANNED_LATEST="$(printf '2%.0s' $(seq 40))" REVIEW_PLANNED_ROUNDS=2
mkdir -p "$DIR"
printf 'a.txt\n' >"$DIR/review-files.txt"
fake_legs ok
rc=0; ( review_rounds_record ) >"$work/late.out" 2>&1 || rc=$?
[ "$rc" -eq 3 ] || fail "cap at record exit $rc: $(cat "$work/late.out")"
[ -s "$CONCLAVE_ROUNDS_DIR/github.com_example_app_7.park.md" ] || fail "cap at record wrote no park note"
grep -q "round 3 (ballot seed3)" "$DIR/round-refused.md" || fail "the refused ballot's note: $(cat "$DIR/round-refused.md")"
printf -- '--- cap at record: exit %s\n' "$rc"; cat "$work/late.out"; cat "$DIR/round-refused.md"

# The real entry point. gh names a head the repo lacks, so if the cap ever
# failed to refuse, the run would die at the fetch, before any leg.
# resolve-models runs first. HOME, CONCLAVE_CODEX_BIN, and the catalog
# keep that off the real vendor CLIs.
cli="$work/rounds3"
mkdir -p "$cli"
jq -n '{pr_url: "https://github.com/example/app/pull/7", rounds: [range(1;4) |
  {round: ., ballot: "cli\(.)", mode: "pr", base: ("0" * 40), head: ("\(.)" * 40), carried_open: []}]}' \
  >"$cli/github.com_example_app_7.json"
cat >"$work/bin/gh" <<'SH'
#!/bin/sh
[ "$1 $2" = "pr view" ] || exit 1
case "$*" in *--json\ number*) ;; *) echo '{}'; exit 0 ;; esac
printf '{"number":7,"title":"t","url":"https://github.com/example/app/pull/7","headRefName":"feature","baseRefName":"master","state":"OPEN","headRefOid":"%040d","additions":1,"deletions":0,"changedFiles":1}\n' 9
SH
id="test-rounds-cli-$$"
mkdir -p "$ROOT/tmp/council/$id"
printf 'kind: review\n\nReview the change.\n' >"$ROOT/tmp/council/$id/brief.md"
rc=0
env -u COUNCIL_CODEX_BIN \
  HOME="$work/home" \
  CONCLAVE_CODEX_BIN="$work/bin/codex" \
  CONCLAVE_MUSE_CATALOG="$work/muse-catalog.json" \
  CONCLAVE_ROUNDS_DIR="$cli" CONCLAVE_PREFLIGHT=0 CONCLAVE_SKIP_UPDATE_CHECK=1 CONCLAVE_JEV_OP=/bin/false \
  "$ROOT/scripts/conclave.sh" "$id" --repo "$REPO" --pr 7 --delta --voters claude,codex >"$work/cli.out" 2>&1 || rc=$?
rm -rf "$ROOT/tmp/council/$id"
printf -- '--- conclave.sh round 4: exit %s\n' "$rc"
tail -3 "$work/cli.out"
[ "$rc" -eq 3 ] || fail "conclave.sh round 4 exit $rc: $(cat "$work/cli.out")"
[ -s "$cli/github.com_example_app_7.park.md" ] || fail "conclave.sh round 4 wrote no park note"
grep -q 'refused round 4' "$work/cli.out" || fail "conclave.sh round 4 message"

# A capped PR with no --voters. The stub lineup demands a downgrade approval,
# so a run that reached seating would die there. The cap must refuse first.
cap="$work/rounds-cap"
mkdir -p "$cap"
cp "$cli/github.com_example_app_7.json" "$cap/"
cat >"$work/bin/seat-usage" <<EOF2
#!/bin/sh
echo "seat-usage \$*" >>"$work/seat-calls"
printf '%s\n' '[{"role":"lineup","mode":"default","reason":"stub","lineup":{"voters":[{"family":"astra","seat":"chatgpt","vendor":"openai"}]},"downgrade":{"family":"fable","reason":"stub: approve a downgraded leg"}}]'
EOF2
chmod +x "$work/bin/seat-usage"
id="test-rounds-cap-$$"
mkdir -p "$ROOT/tmp/council/$id"
printf 'kind: review\n\nReview the change.\n' >"$ROOT/tmp/council/$id/brief.md"
cap_runs="$work/cap-runs.jsonl"
cap_run() {
  env -u COUNCIL_CODEX_BIN -u COUNCIL_SEAT_USAGE \
    HOME="$work/home" \
    SEAT_USAGE_RUNS="$cap_runs" \
    CONCLAVE_CODEX_BIN="$work/bin/codex" \
    CONCLAVE_MUSE_CATALOG="$work/muse-catalog.json" \
    CONCLAVE_SEAT_USAGE="$work/bin/seat-usage" \
    CONCLAVE_ROUNDS_DIR="$cap" CONCLAVE_SKIP_UPDATE_CHECK=1 CONCLAVE_JEV_OP=/bin/false \
    "$ROOT/scripts/conclave.sh" "$id" --repo "$REPO" --pr 7 --delta "$@" >"$work/cap.out" 2>&1
}
rc=0; cap_run --dry-run || rc=$?
printf -- '--- conclave.sh capped PR, --dry-run: exit %s\n' "$rc"
[ "$rc" -eq 3 ] || fail "capped dry-run exit $rc: $(cat "$work/cap.out")"
[ ! -e "$cap_runs" ] || fail "capped dry-run wrote the runs log: $(cat "$cap_runs")"
rc=0; cap_run || rc=$?
rm -rf "$ROOT/tmp/council/$id"
printf -- '--- conclave.sh capped PR, no --voters: exit %s\n' "$rc"
tail -3 "$work/cap.out"
[ "$rc" -eq 3 ] || fail "capped PR exit $rc: $(cat "$work/cap.out")"
[ -s "$cap/github.com_example_app_7.park.md" ] || fail "capped PR wrote no park note"
grep -q 'refused round 4' "$work/cap.out" || fail "capped PR message"
[ ! -e "$work/seat-calls" ] || fail "capped PR reached seat-usage: $(cat "$work/seat-calls")"
if grep -q 'approve a downgraded leg' "$work/cap.out"; then fail "capped PR asked for a downgrade"; fi
[ -s "$cap_runs" ] || fail "capped PR wrote no runs log"
printf -- '--- capped PR runs log\n'; cat "$cap_runs"
[ "$(wc -l <"$cap_runs")" -eq 1 ] || fail "capped PR runs log: $(cat "$cap_runs")"
jq -e --arg id "$id" --arg head "$(printf '%040d' 9)" \
  '.phase == "parked" and .pr_url == "https://github.com/example/app/pull/7" and .head == $head and .task_id == $id and (.run_id | startswith($id + "-"))' \
  "$cap_runs" >/dev/null || fail "capped PR parked line: $(cat "$cap_runs")"

# The same gate on a PR under the cap writes nothing. Not in a subshell: the
# writer skips subshells, which would pass this for the wrong reason.
ID=under FLAG_PR=7 FLAG_DELTA=0 FLAG_BASE="" FLAG_HEAD="" FLAG_WHOLE=0 HEADER_PR="" HEADER_BRANCH="" DRY_RUN=0
DIR="$work/council/under"
SEAT_USAGE_RUNS="$work/under-runs.jsonl" CONCLAVE_ROUNDS_DIR="$work/rounds-under" review_rounds_cap_gate >/dev/null
[ ! -e "$work/under-runs.jsonl" ] || fail "a run under the cap wrote the runs log: $(cat "$work/under-runs.jsonl")"

# review_run with review_launch replaced, so no voter leg starts. The lineup
# still asks claude and cursor-agent for --version; those hit the stubs.
# Every leg failing leaves the log unwritten. A leg returning records after the legs.
head="$(g -C "$REPO" rev-parse HEAD)"
g -C "$REPO" push -q -f origin "HEAD:refs/heads/feature"
review_parse_shortstat "$(g -C "$REPO" diff --shortstat master HEAD)"
sed -i "s/%040d/$head/; s/ 9\$/ /; s/\"additions\":1/\"additions\":$SS_INS/; s/\"deletions\":0/\"deletions\":$SS_DEL/; s/\"changedFiles\":1/\"changedFiles\":$SS_FILES/" "$work/bin/gh"
review_reseat_failed() { :; }
review_launch() { [ ! -e "$(review_rounds_dir)/github.com_example_app_7.json" ] || fail "recorded before the legs"; fake_legs "$LEGS"; }
export CONCLAVE_ROUNDS_DIR="$work/rounds5" CONCLAVE_PREFLIGHT=0
run_review() { # $1 = ballot id, $2 = ok or fail
  (
    unset COUNCIL_CODEX_BIN
    export HOME="$work/home"
    export CONCLAVE_CODEX_BIN="$work/bin/codex"
    export CONCLAVE_MUSE_CATALOG="$work/muse-catalog.json"
    ID="$1" LEGS="$2" KIND=review VOTERS=claude,codex VOTERS_EXPLICIT=1 FLAG_REPO="$REPO" FLAG_PR=7 FLAG_DELTA=0
    FLAG_BASE="" FLAG_HEAD="" FLAG_WHOLE=0 HEADER_PR="" HEADER_BRANCH="" REVIEW_DELTA_BASE=""
    DIR="$work/council/$ID" ERRORS_FILE="$work/council/$ID/errors.md" BRIEF="$work/council/$ID/brief.md"
    mkdir -p "$DIR"
    printf 'Review the change.\n' >"$BRIEF"
    review_run
  ) >"$work/$1.out" 2>&1
}
log="$CONCLAVE_ROUNDS_DIR/github.com_example_app_7.json"
rc=0; run_review full-fail fail || rc=$?
printf -- '--- review_run with every leg failed: exit %s\n' "$rc"
grep -E 'not recorded|not judgeable' "$work/full-fail.out" || fail "review_run failed launch: $(cat "$work/full-fail.out")"
[ ! -e "$log" ] || fail "a failed launch wrote the round log: $(cat "$log")"
rc=0; run_review full-ok ok || rc=$?
printf -- '--- review_run with reports: exit %s, log rounds %s\n' "$rc" "$(jq -c '[.rounds[].ballot]' "$log" 2>&1)"
[ "$(jq -c '[.rounds[].ballot]' "$log" 2>/dev/null)" = '["full-ok"]' ] || fail "review_run record: $(cat "$work/full-ok.out")"

echo "test-review-rounds: all passed"
