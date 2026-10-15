#!/usr/bin/env bash
# Review target selection: the brief's pr: header, conflicts, --whole opt-in,
# and the file-count guard. Sources review-kind.sh; no git, gh, or network.
set -uo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
fails=0

run() {
  # $1 label, $2 want (ok:MODE:PR or die:substring), rest: var assignments
  local label="$1" want="$2" out
  shift 2
  out="$(
    die() { printf 'DIE %s\n' "$*"; exit 1; }
    . "$ROOT/scripts/conclave-env.sh"
    . "$ROOT/scripts/review-kind.sh"
    FLAG_PR="" FLAG_BASE="" FLAG_HEAD="" FLAG_WHOLE=0 HEADER_PR="" HEADER_BRANCH=""
    for a in "$@"; do eval "$a"; done
    review_target_mode
    printf 'ok:%s:%s\n' "$REVIEW_MODE" "$FLAG_PR"
  )"
  case "$want" in
    die:*) [[ "$out" == DIE*"${want#die:}"* ]] ;;
    *) [ "$out" = "$want" ] ;;
  esac || { printf 'FAIL %s: got %s, want %s\n' "$label" "$out" "$want"; fails=$((fails + 1)); }
}

run "no target dies" "die:review target missing"
run "branch header alone dies" "die:review target missing" 'HEADER_BRANCH=feature/x'
run "--whole opts in" "ok:whole:" 'FLAG_WHOLE=1'
run "--whole with --pr dies" "die:--whole takes no" 'FLAG_WHOLE=1' 'FLAG_PR=3'
run "--whole with branch header dies" "die:conflicts with --whole" 'FLAG_WHOLE=1' 'HEADER_BRANCH=x'
run "header URL selects pr" "ok:pr:3523" 'HEADER_PR=https://github.com/example/app/pull/3523'
run "header URL with /files" "ok:pr:3523" 'HEADER_PR=https://github.com/example/app/pull/3523/files'
run "header #N" "ok:pr:12" 'HEADER_PR=#12'
run "header bare N" "ok:pr:12" 'HEADER_PR=12'
run "header junk dies" "die:not a PR number" 'HEADER_PR=https://github.com/o/r/issues/5'
run "header and flag agree" "ok:pr:7" 'HEADER_PR=7' 'FLAG_PR=7'
run "header and flag differ" "die:conflicts with brief header" 'HEADER_PR=7' 'FLAG_PR=8'
run "header and --base conflict" "die:conflicts with --whole or --base" 'HEADER_PR=7' 'FLAG_BASE=main'
run "header and --whole conflict" "die:conflicts with --whole or --base" 'HEADER_PR=7' 'FLAG_WHOLE=1'
run "refs still work" "ok:refs:" 'FLAG_BASE=main'
run "header URL .diff" "ok:pr:3523" 'HEADER_PR=https://github.com/example/app/pull/3523.diff'
run "header URL query holds another pull" "ok:pr:3523" 'HEADER_PR=https://github.com/example/app/pull/3523?next=https://github.com/evil/repo/pull/1'

size() {
  local label="$1" want="$2" n="$3" max="${4-}" out
  out="$(
    die() { printf 'DIE %s\n' "$*"; exit 1; }
    . "$ROOT/scripts/conclave-env.sh"
    . "$ROOT/scripts/review-kind.sh"
    if [ -n "$max" ]; then export CONCLAVE_REVIEW_MAX_FILES="$max"; else unset CONCLAVE_REVIEW_MAX_FILES COUNCIL_REVIEW_MAX_FILES; fi
    review_check_size "$n" && echo ok
  )"
  [[ "$out" == "$want"* ]] || { printf 'FAIL %s: got %s\n' "$label" "$out"; fails=$((fails + 1)); }
}
size "300 files passes" ok 300
size "301 files asks" "DIE review diff has 301 files" 301
size "override raises the cap" ok 4075 5000

check_hdr() {
  local label="$1" want="$2" out
  shift 2
  out="$(
    die() { printf 'DIE %s\n' "$*"; exit 1; }
    . "$ROOT/scripts/conclave-env.sh"
    . "$ROOT/scripts/review-kind.sh"
    FLAG_PR=3523 REPO=/r HEADER_PR="" HEADER_BRANCH=""
    PR_URL=https://github.com/example/app/pull/3523 PR_HEAD=feat
    for a in "$@"; do eval "$a"; done
    review_check_pr_header && echo ok
  )"
  [[ "$out" == "$want"* ]] || { printf 'FAIL %s: got %s\n' "$label" "$out"; fails=$((fails + 1)); }
}
check_hdr "matching URL and branch" ok 'HEADER_PR=https://github.com/example/app/pull/3523/' 'HEADER_BRANCH=feat'
check_hdr "branch mismatch" "DIE brief header 'branch: main'" 'HEADER_PR=3523' 'HEADER_BRANCH=main'
check_hdr "URL from another repo" "DIE brief header 'pr: https://github.com/o/other/pull/3523'" 'HEADER_PR=https://github.com/o/other/pull/3523'
for u in https://github.com/example/app/pull/3523/files https://github.com/example/app/pull/3523/files?w=1 \
  https://github.com/example/app/pull/3523/commits/abc https://www.github.com/example/app/pull/3523 \
  http://github.com/example/app/pull/3523 HTTPS://GITHUB.COM/example/app/pull/3523 github.com/example/app/pull/3523; do
  check_hdr "same PR as $u" ok "HEADER_PR=$u"
done
check_hdr "schemeless other repo" "DIE brief header" 'HEADER_PR=github.com/o/other/pull/3523'
check_hdr "uppercase scheme other repo" "DIE brief header" 'HEADER_PR=HTTPS://github.com/o/other/pull/3523'
check_hdr "bare number is not repo-checked" ok 'HEADER_PR=3523'

if [ "$fails" -eq 0 ]; then echo "test-review-target: all passed"; else exit 1; fi
