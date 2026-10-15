#!/usr/bin/env bash
# review_run_grok resumes on the sessions' outcomes, inner reviewer first.
# No live Grok call. A fake grok on PATH writes session directories shaped like
# the 2026-10-13 ballots: an outer session, and the /review reviewer as an
# inner session that the outer one lists in subagents/<id>/meta.json.
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
mkdir -p "$work/bin"
export CONCLAVE_GROK_SESSIONS="$work/sessions"
export FAKE_OUTER=01a13641-0000-7000-8000-000000000001
export FAKE_INNER=01a13642-0000-7000-8000-000000000002
export FAKE_MODEL=grok-fake-model
export PATH="$work/bin:$PATH"

# Fake grok. A first call writes both sessions: the outer one ends completed,
# the inner one ends FAKE_INNER_END. A review file appears only when
# FAKE_FIRST_REPORT=1. A --resume call follows FAKE_RESUME: refuse (exit 1,
# session untouched), empty (completes, writes nothing), or report (completes
# and writes the review file). Every call is one line in FAKE_LOG.
cat >"$work/bin/grok" <<'EOF'
#!/usr/bin/env bash
set -eu
resume="" cwd="" turns="" prompt=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --resume) resume="$2"; shift 2 ;;
    --cwd) cwd="$2"; shift 2 ;;
    --max-turns) turns="$2"; shift 2 ;;
    -p) prompt="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf 'resume=%s max_turns=%s cwd=%s tmpdir=%s prompt=%s\n' \
  "${resume:--}" "$turns" "$cwd" "$TMPDIR" "${prompt%%$'\n'*}" >>"$FAKE_LOG"
root="$CONCLAVE_GROK_SESSIONS/$(printf '%s' "$cwd" | sed 's|/|%2F|g')"
out="$TMPDIR/grok-$(id -un)"
turn() { # $1 session id, $2 outcome
  printf '{"type":"turn_started","session_id":"%s","model_id":"%s"}\n' "$1" "$FAKE_MODEL"
  printf '{"type":"turn_ended","outcome":"%s"}\n' "$2"
}
if [ -z "$resume" ]; then
  mkdir -p "$root/$FAKE_OUTER/subagents/$FAKE_INNER" "$root/$FAKE_INNER" "$out"
  printf '{"child_session_id":"%s","parent_session_id":"%s"}\n' "$FAKE_INNER" "$FAKE_OUTER" \
    >"$root/$FAKE_OUTER/subagents/$FAKE_INNER/meta.json"
  turn "$FAKE_INNER" "$FAKE_INNER_END" >"$root/$FAKE_INNER/events.jsonl"
  turn "$FAKE_OUTER" completed >"$root/$FAKE_OUTER/events.jsonl"
  printf 'diff\n' >"$out/grok-review-diff-1.diff"
  if [ "$FAKE_FIRST_REPORT" = 1 ]; then
    printf 'first run report\n' >"$out/grok-review-1.md"
    printf 'Review written to %s\n' "$out/grok-review-1.md"
  else
    printf 'The reviewer hit its turn limit and wrote no review file.\n'
  fi
  # The outer session ends last, so it is the newest directory.
  touch -d '2026-10-13 10:24:32' "$root/$FAKE_INNER"
  touch -d '2026-10-13 10:24:56' "$root/$FAKE_OUTER"
  exit 0
fi
case "$FAKE_RESUME" in
  refuse)
    echo "error: cannot resume session $resume" >&2
    exit 1
    ;;
  empty)
    turn "$resume" completed >>"$root/$resume/events.jsonl"
    printf 'Stopped. Nothing written.\n'
    ;;
  report)
    turn "$resume" completed >>"$root/$resume/events.jsonl"
    printf 'resumed report\n' >"$out/grok-review-1.md"
    printf 'Review written to %s\n' "$out/grok-review-1.md"
    ;;
esac
EOF
chmod +x "$work/bin/grok"

# run_case NAME INNER_END FIRST_REPORT RESUME. Sets W, T, DIR, RC, and CALLS.
# The leg runs in a subshell because review_grok_exec changes directory.
run_case() {
  W="$work/$1/leg/wt"
  T="$work/$1/leg/tmp"
  DIR="$work/$1/out"
  mkdir -p "$W" "$T" "$DIR"
  export FAKE_LOG="$work/$1/grok.log" FAKE_INNER_END="$2" FAKE_FIRST_REPORT="$3" FAKE_RESUME="$4"
  : >"$FAKE_LOG"
  RC=0
  (review_run_grok "$W" "$T") || RC=$?
  CALLS="$(wc -l <"$FAKE_LOG" | tr -d ' ')"
}

first_call() {
  [ "$(sed -n 1p "$FAKE_LOG")" = "resume=- max_turns=60 cwd=$W tmpdir=$T prompt=/review --local" ] \
    || fail "$1: first call: $(sed -n 1p "$FAKE_LOG")"
}

# 1. Outer completed, inner cancelled, no report. The inner session is resumed
# with the resume prompt, same --cwd and TMPDIR, max-turns 80. Its report counts.
run_case inner-report cancelled 0 report
[ "$RC" -eq 0 ] || fail "inner-report: rc=$RC: $(cat "$DIR/voter-grok.fail" 2>/dev/null)"
[ "$CALLS" -eq 2 ] || fail "inner-report: $CALLS calls: $(cat "$FAKE_LOG")"
first_call inner-report
[ "$(sed -n 2p "$FAKE_LOG")" = "resume=$FAKE_INNER max_turns=80 cwd=$W tmpdir=$T prompt=$GROK_RESUME_PROMPT" ] \
  || fail "inner-report: resume call: $(sed -n 2p "$FAKE_LOG")"
[ "$(cat "$DIR/voter-grok-report.md")" = "resumed report" ] \
  || fail "inner-report: report: $(cat "$DIR/voter-grok-report.md" 2>/dev/null)"
[ ! -e "$DIR/voter-grok.fail" ] || fail "inner-report: fail file: $(cat "$DIR/voter-grok.fail")"
[ "$(cat "$DIR/voter-grok.model.txt")" = "grok: ok model_id=$FAKE_MODEL turn=completed" ] \
  || fail "inner-report: model: $(cat "$DIR/voter-grok.model.txt")"
[ "$(wc -l <"$DIR/voter-grok.turn.txt" | tr -d ' ')" -eq 2 ] \
  || fail "inner-report: turn.txt: $(cat "$DIR/voter-grok.turn.txt")"
review_report_present grok || fail "inner-report: report not counted"
printf 'ok inner session cancelled: resumed, report collected\n'

# 2. Same, but the CLI refuses to resume the inner session. The leg fails, and
# the fail line names the session and what happened.
run_case inner-refused cancelled 0 refuse
[ "$RC" -eq 1 ] || fail "inner-refused: rc=$RC"
[ "$CALLS" -eq 2 ] || fail "inner-refused: $CALLS calls: $(cat "$FAKE_LOG")"
first_call inner-refused
sed -n 2p "$FAKE_LOG" | grep -q "^resume=$FAKE_INNER max_turns=80 " \
  || fail "inner-refused: resume call: $(sed -n 2p "$FAKE_LOG")"
want="grok: resumed inner session $FAKE_INNER: exit 1, last turn_ended cancelled, no report (expected exactly one grok-review-*.md under the leg TMPDIR, found 0)"
[ "$(cat "$DIR/voter-grok.fail")" = "$want" ] || fail "inner-refused: fail: $(cat "$DIR/voter-grok.fail")"
[ "$(cat "$DIR/voter-grok.model.txt")" = "grok: FAIL turn_ended not completed (inner $FAKE_INNER cancelled)" ] \
  || fail "inner-refused: model: $(cat "$DIR/voter-grok.model.txt")"
if review_report_present grok; then fail "inner-refused: counted as a report"; fi
printf 'ok resume refused: leg fails, fail line names the inner session\n'

# 2b. The resume completes and still writes nothing. Every session ended
# completed, but with no report the model check must not say turn=completed.
run_case inner-empty cancelled 0 empty
[ "$RC" -eq 1 ] || fail "inner-empty: rc=$RC"
[ "$CALLS" -eq 2 ] || fail "inner-empty: $CALLS calls: $(cat "$FAKE_LOG")"
want="grok: resumed inner session $FAKE_INNER: exit 0, last turn_ended completed, no report (expected exactly one grok-review-*.md under the leg TMPDIR, found 0)"
[ "$(cat "$DIR/voter-grok.fail")" = "$want" ] || fail "inner-empty: fail: $(cat "$DIR/voter-grok.fail")"
[ "$(cat "$DIR/voter-grok.model.txt")" = "grok: FAIL no report" ] \
  || fail "inner-empty: model: $(cat "$DIR/voter-grok.model.txt")"
if review_report_present grok; then fail "inner-empty: counted as a report"; fi
printf 'ok resume writes nothing: leg fails, model check is not completed\n'

# 3. Both sessions completed and the report exists. No resume.
run_case both-completed completed 1 refuse
[ "$RC" -eq 0 ] || fail "both-completed: rc=$RC: $(cat "$DIR/voter-grok.fail" 2>/dev/null)"
[ "$CALLS" -eq 1 ] || fail "both-completed: $CALLS calls: $(cat "$FAKE_LOG")"
first_call both-completed
[ "$(cat "$DIR/voter-grok-report.md")" = "first run report" ] \
  || fail "both-completed: report: $(cat "$DIR/voter-grok-report.md" 2>/dev/null)"
[ ! -e "$DIR/voter-grok.fail" ] || fail "both-completed: fail file: $(cat "$DIR/voter-grok.fail")"
[ "$(cat "$DIR/voter-grok.model.txt")" = "grok: ok model_id=$FAKE_MODEL turn=completed" ] \
  || fail "both-completed: model: $(cat "$DIR/voter-grok.model.txt")"
review_report_present grok || fail "both-completed: report not counted"
printf 'ok both sessions completed: no resume\n'

# 4. Target choice. Both sessions unfinished: the inner one wins. Inner
# completed and outer cut off with no turn_ended: the outer one, which is the
# resume the newest-session check already did.
W="$work/pick/leg/wt"
T="$work/pick/leg/tmp"
DIR="$work/pick/out"
mkdir -p "$W" "$T" "$DIR"
export FAKE_LOG="$work/pick.log" FAKE_INNER_END=cancelled FAKE_FIRST_REPORT=0 FAKE_RESUME=refuse
(cd "$W" && TMPDIR="$T" grok --cwd "$W" -p probe >/dev/null)
root="$(review_grok_session_dir "$W")"
printf '{"type":"turn_started","session_id":"%s","model_id":"%s"}\n' "$FAKE_OUTER" "$FAKE_MODEL" \
  >"$root/$FAKE_OUTER/events.jsonl"
[ "$(review_grok_resume_target "$W")" = "$root/$FAKE_INNER" ] \
  || fail "pick: both unfinished: $(review_grok_resume_target "$W")"
printf '{"type":"turn_ended","outcome":"completed"}\n' >>"$root/$FAKE_INNER/events.jsonl"
[ "$(review_grok_resume_target "$W")" = "$root/$FAKE_OUTER" ] \
  || fail "pick: outer cut off: $(review_grok_resume_target "$W")"
review_grok_leg_check "$W"
[ "$(cat "$DIR/voter-grok.model.txt")" = "grok: FAIL turn_ended not completed (outer $FAKE_OUTER none)" ] \
  || fail "pick: model: $(cat "$DIR/voter-grok.model.txt")"
printf 'ok inner session wins; a cut-off outer session is still resumed\n'

printf 'ok test-grok-resume\n'
