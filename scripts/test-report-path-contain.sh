#!/usr/bin/env bash
# review_collect_grok_report copies the grok voter's stated report into the
# ballot. A stated path that escapes the leg TMPDIR, by '..' or by a symlink
# placed inside it, must be refused as a voter failure, never copied.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

status=0
fail() {
  printf 'FAIL %s\n' "$*"
  status=1
}

# Sets work, legtmp, DIR, and an outside secret named like a report.
setup() {
  work="$(mktemp -d)"
  legtmp="$work/leg-tmp"
  DIR="$work/ballot"
  mkdir -p "$legtmp" "$DIR" "$work/outside"
  printf 'OUTSIDE-SECRET\n' >"$work/outside/grok-review-secret.md"
  unset ERRORS_FILE
}

# $1 case name, $2 stated path. Expects a refusal recorded in voter-grok.fail.
expect_refused() {
  local name="$1" stated="$2" rc=0
  printf 'Review written to %s\n' "$stated" >"$DIR/voter-grok.stdout.md"
  review_collect_grok_report "$legtmp" || rc=$?
  if grep -q OUTSIDE-SECRET "$DIR/voter-grok-report.md" 2>/dev/null; then
    fail "$name: outside file copied into the ballot"
  else
    printf 'ok %s: outside file not copied\n' "$name"
  fi
  if [ "$rc" -ne 0 ] && grep -q 'refused' "$DIR/voter-grok.fail" 2>/dev/null; then
    printf 'ok %s: refusal recorded as a voter failure (%s)\n' "$name" "$(cat "$DIR/voter-grok.fail")"
  else
    fail "$name: expected rc!=0 and a refusal in voter-grok.fail, rc=$rc fail=$(cat "$DIR/voter-grok.fail" 2>/dev/null || echo none)"
  fi
}

setup
expect_refused "dotdot path" "$legtmp/../outside/grok-review-secret.md"
rm -rf "$work"

setup
ln -s "$work/outside/grok-review-secret.md" "$legtmp/grok-review-link.md"
expect_refused "symlink in legtmp" "$legtmp/grok-review-link.md"
rm -rf "$work"

setup
mkdir -p "$legtmp/sub"
ln -s "$work/outside" "$legtmp/sub/out"
expect_refused "symlinked dir in legtmp" "$legtmp/sub/out/grok-review-secret.md"
rm -rf "$work"

setup
ln -s "$work/outside" "$legtmp/grok-$(id -un)"
rc=0
review_collect_grok_report "$legtmp" || rc=$?
if grep -q OUTSIDE-SECRET "$DIR/voter-grok-report.md" 2>/dev/null; then
  fail "symlinked review dir: outside file copied into the ballot"
else
  printf 'ok symlinked review dir: outside file not copied (rc=%s)\n' "$rc"
fi
rm -rf "$work"

setup
mkdir -p "$legtmp/grok-$(id -un)"
printf 'inside report\n' >"$legtmp/grok-$(id -un)/grok-review-1.md"
printf 'Review written to %s\n' "$legtmp/grok-$(id -un)/grok-review-1.md" >"$DIR/voter-grok.stdout.md"
rc=0
review_collect_grok_report "$legtmp" || rc=$?
if [ "$rc" -eq 0 ] && grep -q 'inside report' "$DIR/voter-grok-report.md" && [ ! -e "$DIR/voter-grok.fail" ]; then
  printf 'ok contained report: copied, no failure\n'
else
  fail "contained report: rc=$rc fail=$(cat "$DIR/voter-grok.fail" 2>/dev/null || echo none)"
fi
rm -rf "$work"

# End to end through review_run_grok_native with a fake grok, so the refusal
# reaches voter-grok.fail even when grok also exits non-zero.
for fake_exit in 0 1; do
  setup
  mkdir -p "$work/bin" "$work/wt" "$work/sessions"
  cat >"$work/bin/grok" <<SH
#!/usr/bin/env bash
ln -sf "$work/outside/grok-review-secret.md" "\$TMPDIR/grok-review-link.md"
printf 'Review written to %s\n' "\$TMPDIR/grok-review-link.md"
exit $fake_exit
SH
  chmod +x "$work/bin/grok"
  rc=0
  (
    export PATH="$work/bin:$PATH" CONCLAVE_GROK_SESSIONS="$work/sessions"
    review_run_grok_native "$work/wt" "$legtmp"
  ) >/dev/null 2>&1 || rc=$?
  name="native run, grok exit $fake_exit"
  if grep -q OUTSIDE-SECRET "$DIR/voter-grok-report.md" 2>/dev/null; then
    fail "$name: outside file copied into the ballot"
  elif [ "$rc" -ne 0 ] && grep -q 'refused' "$DIR/voter-grok.fail" 2>/dev/null; then
    printf 'ok %s: voter failure names the refusal (%s)\n' "$name" "$(cat "$DIR/voter-grok.fail")"
  else
    fail "$name: rc=$rc fail=$(cat "$DIR/voter-grok.fail" 2>/dev/null || echo none)"
  fi
  rm -rf "$work"
done

exit "$status"
