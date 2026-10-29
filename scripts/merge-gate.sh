#!/usr/bin/env bash
# Merge gate: one read-only reviewer on a PR diff, and the only merge path.
#
#   scripts/merge-gate.sh review <repo_dir> <base_ref> <head_ref> <pr_url> <out_dir> <builder_vendor> [builder_bot]
#   scripts/merge-gate.sh merge  [--builder-bot NAME] <pr_url> <out_dir>
#
# review asks `seat-usage --route review --cached --json --builder-vendor V`
# for the reviewer and its launch argv (saved as gate-route.json). Before
# that, it reads the PR commits and checks V against Co-authored-by trailers.
# One named vendor must match V (grok counts as xai; Claude and an
# @anthropic.com mailbox count as anthropic). Several named vendors, a known
# vendor beside a co-author outside the known families, or a commits read that
# fails, refuse (exit 3, one line) before seat-usage runs. No model trailer,
# or only co-authors outside the known families, keeps V and prints one note.
# gate-route.json then records vendor_source (trailer when a trailer matched
# V, caller when V is the caller's value), and the review summary line says
# the same. The PR author controls the trailers, so a match is a consistency
# check on V and not proof of who built the PR. When the route asks,
# fails, names none, returns no argv list (a seat-usage older than that
# contract), or records a builder vendor and reviewer vendor that are missing
# or the same, it refuses (exit 3, one line) before any reviewer runs.
# It dispatches on the argv's first element (agy, cursor-agent, codex, claude,
# muse) and runs that reviewer read-only from an empty temp dir, with the PR
# description and the diff in the prompt, never the repo as a workspace. Any
# other runner refuses. The prompt never rides in argv: one argument over 128KB
# (MAX_ARG_STRLEN) makes execve fail with "Argument list too long", and a
# diff with 40 lines of context passes that. agy and codex read it on stdin,
# cursor-agent and claude read it on stdin when given no prompt argument, and
# muse reads --prompt-file. agy silently stops reading near 190 KB, so for agy
# a prompt over CONCLAVE_ARGON_MAX_BYTES (default 150000, the review Argon
# packet cap) refuses (exit 3, one line) before agy runs. It writes
# gate-prompt.md, gate.stream.jsonl (the reviewer's raw output), gate.err,
# gate.md (the reviewer's answer), gate-head.txt (the reviewed head sha), and
# gate-verdict.json (the parsed verdict line, approve / request-changes /
# comment, and the parsed one_way line) under <out_dir>. Given a builder_bot
# (the bot that built the PR), it also writes builder-bot.txt.
#
# merge refuses, nonzero, unless gate-verdict.json records verdict approve and
# gate.md still parses to that verdict (a missing, null, or unknown verdict
# refuses). It also refuses when its one_way line is missing or unparseable,
# when the PR has the one-way label, when the labels cannot be read, or when
# gate-route.json is missing, unreadable, or records no builder vendor, no
# reviewer vendor, or the same vendor for both. one_way: yes refuses unless
# <out_dir>/acks/one-way-ack.json records Ryan's yes for this PR, this head
# and exactly these one-way steps, written by a bot other than the builder
# bot (builder-bot.txt, or --builder-bot for an older gate dir); a valid ack
# also clears the one-way label. Otherwise it runs gh pr merge --squash,
# pinned to the reviewed head sha, and after a one-way merge moves the ack to
# acks/used/. See scripts/merge_gate.py.
#
# MERGE_GATE_GH (default /usr/local/bin/gh) and MERGE_GATE_SEAT_USAGE (default
# seat-usage) override those binaries. MERGE_GATE_AGY, MERGE_GATE_CURSOR_AGENT,
# MERGE_GATE_CODEX, MERGE_GATE_CLAUDE and MERGE_GATE_MUSE replace the reviewer
# binary seat-usage names; the tests stub them all.
set -uo pipefail
ROOT="$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/scripts/conclave-env.sh"
GH="${MERGE_GATE_GH:-/usr/local/bin/gh}"
SEAT_USAGE="${MERGE_GATE_SEAT_USAGE:-seat-usage}"
usage() { sed -n '4,5p' "${BASH_SOURCE[0]}" | sed 's/^# *//' >&2; exit 2; }
refuse() { echo "merge-gate: refused: $*" >&2; exit 3; }

# Same newest-version pick as scripts/resolve-models.sh.
newest() { # $1 = regex with one capture group for the version; reads IDs on stdin
  sed -nE "s/$1/\1 &/p" | awk '{v=$1; gsub(/-/,".",v); print v, $2}' |
    sort -V | tail -1 | cut -d' ' -f2
}

review() {
  [ "$#" -eq 6 ] || [ "$#" -eq 7 ] || usage
  local repo=$1 base=$2 head=$3 pr=$4 out=$5 vendor=$6 bot=${7:-} picked model why name bin
  local commits derived status listed source=caller
  mkdir -p "$out" || exit 1
  # A refusal must not leave an earlier verdict for merge to read.
  rm -f "$out/gate.md" "$out/gate-verdict.json" "$out/gate-head.txt" "$out/gate-route.json" "$out/builder-bot.txt"
  vendor=$(python3 "$ROOT/scripts/trailer_vendor.py" normalize "$vendor") \
    || refuse "could not normalize builder vendor $6"
  commits=$("$GH" pr view "$pr" --json commits 2> "$out/gate-commits.err") \
    || { why=$(head -n 1 "$out/gate-commits.err"); refuse "could not read the PR commits for $pr${why:+: $why}"; }
  derived=$(printf '%s' "$commits" | python3 "$ROOT/scripts/trailer_vendor.py" from-commits) \
    || refuse "could not read the PR commits for $pr"
  status=${derived%%$'\n'*}
  listed=${derived#*$'\n'}
  listed=${listed%%$'\n'*}
  case "$status" in
    ambiguous)
      refuse "the PR commit trailers name several vendors or an unrecognised co-author ($listed), so no single builder vendor can be verified" ;;
    unknown)
      echo "merge-gate: note: co-author $listed names no known model family; builder vendor $vendor is the caller's" ;;
    "")
      echo "merge-gate: note: no model trailer on the PR commits; builder vendor $vendor is the caller's" ;;
    "$vendor") source=trailer ;;
    *)
      refuse "builder vendor $vendor does not match the PR commit trailers ($status)" ;;
  esac
  if ! "$SEAT_USAGE" --route review --cached --json --builder-vendor "$vendor" \
      > "$out/gate-route.json" 2> "$out/gate-route.err"; then
    why=$(grep -v '^\[seat-usage\]' "$out/gate-route.err" | head -n 1)
    refuse "seat-usage --route review failed: ${why:-no output}"
  fi
  # Record where the builder vendor came from, beside the vendors seat-usage recorded.
  # A route that is not JSON is left as is for pick to refuse.
  if jq --arg s "$source" 'if type == "array" then map(if type == "object" then . + {vendor_source: $s} else . end) elif type == "object" then . + {vendor_source: $s} else . end' \
      "$out/gate-route.json" > "$out/gate-route.tmp" 2>/dev/null; then
    mv "$out/gate-route.tmp" "$out/gate-route.json" || refuse "could not record vendor_source in gate-route.json"
  else
    rm -f "$out/gate-route.tmp"
  fi
  picked=$(python3 "$ROOT/scripts/merge_gate.py" pick "$out/gate-route.json") || refuse "$picked"
  local -a runner
  mapfile -t runner < <(jq -r '.runner[]' <<<"$picked")
  model=$(jq -r '.model' <<<"$picked")
  # The model is the argv value after --model or -m, else the reviewer model.
  local i
  for ((i = 0; i < ${#runner[@]}; i++)); do
    case "${runner[i]}" in
      --model|-m) [ $((i + 1)) -lt ${#runner[@]} ] && model=${runner[i+1]} ;;
      --model=*) model=${runner[i]#--model=} ;;
    esac
  done
  [[ "$model" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || refuse "seat-usage reviewer has no usable model id"
  name=$(basename -- "${runner[0]}")
  case "$name" in
    agy) bin=${MERGE_GATE_AGY:-${runner[0]}} ;;
    cursor-agent) bin=${MERGE_GATE_CURSOR_AGENT:-${runner[0]}} ;;
    codex) bin=${MERGE_GATE_CODEX:-${runner[0]}} ;;
    claude) bin=${MERGE_GATE_CLAUDE:-${runner[0]}} ;;
    muse) bin=${MERGE_GATE_MUSE:-${runner[0]}} ;;
    *) refuse "no read-only launch for runner $name" ;;
  esac
  # cursor-agent's fable and grok are aliases: launch the newest listed id.
  if [ "$name" = cursor-agent ] && { [ "$model" = fable ] || [ "$model" = grok ]; }; then
    local ids alias=$model
    ids=$(env -u TYPESAFE_API_KEY -u JEV_API_KEY "$bin" models 2>/dev/null | sed -E 's/\x1b\[[0-9;]*m//g' |
      grep -oE '^[a-z0-9.-]+') || ids=""
    if [ "$alias" = fable ]; then
      model=$(newest '^claude-fable-([0-9-]+)-thinking-xhigh$' <<<"$ids")
    else
      model=$(newest '^grok-([0-9.]+)-xhigh-fast$' <<<"$ids")
    fi
    [ -n "$model" ] || refuse "cursor-agent models lists no $alias id"
  fi
  echo "merge-gate: reviewer $model on $name (seat-usage --route review, builder vendor $vendor, vendor_source=$source)"
  GATE_TMP=$(mktemp -d) || exit 1
  trap 'rm -rf "$GATE_TMP"' EXIT
  local P=$GATE_TMP W=$GATE_TMP/ws
  mkdir -p "$W"
  local desc diff headsha
  desc=$("$GH" pr view "$pr" --json title,body --jq '"Title: " + .title + "\n\n" + .body') \
    || { echo "merge-gate: could not read the PR description for $pr" >&2; exit 1; }
  diff=$(git -C "$repo" diff -U40 "$base...$head") \
    || { echo "merge-gate: git diff $base...$head failed in $repo" >&2; exit 1; }
  headsha=$(git -C "$repo" rev-parse --verify "$head^{commit}") \
    || { echo "merge-gate: cannot resolve $head in $repo" >&2; exit 1; }
  {
    echo "# Merge-gate review: $pr"
    echo
    echo "You are the single merge-gate reviewer for this pull request. You have no tools; the PR description and the diff (with 40 lines of context) are below. Follow the reviewer persona, except you cannot run code or write files: reason precisely, cite file:line from the diff hunks, and answer inline."
    echo
    echo "Answer format:"
    echo "- First line: exactly \`approve\` or \`request-changes\`."
    echo "- Then \`Findings:\` most severe first, each with Severity (bug/suggestion/nit), File:line, Description (concrete failing input/state and wrong result), Suggestion. Only \`bug\` findings justify request-changes. Zero findings is fine."
    echo "- Then \`Checked and found clean:\` list."
    echo "- Last, the one-way classification, on its own line with no other text: exactly \`one_way: yes\` or \`one_way: no\`."
    echo "  A one-way step is one that a \`git revert\` of this PR cannot undo after it is merged and the code is deployed or run as intended: a migration that drops or rewrites data, sending notifications or messages (email, chat, SMS, push, webhooks), deleting data or files outside the repo, or any other irreversible external call."
    echo "  Classify what this diff adds or changes, not code that was already there. Tests that only touch stubs or temp directories are not one-way steps."
    echo "  When it is \`one_way: yes\`, follow it with one line per offending step: \`one_way_at: <file>:<line> <what it does>\`, using the new-file line number from the diff hunk."
    echo
    echo "## Reviewer persona"; cat "$ROOT/scripts/reviewer-persona.md"
    echo; echo "## PR description"; printf '%s\n' "$desc"
    echo; echo "## Diff ($base...$head)"; echo '```diff'; printf '%s\n' "$diff"; echo '```'
  } > "$P/PROMPT.md"
  cp "$P/PROMPT.md" "$out/gate-prompt.md"
  if [ "$name" = agy ]; then
    local size cap
    size=$(wc -c < "$P/PROMPT.md") cap=$(argon_max_bytes)
    [ "$size" -le "$cap" ] || refuse "gate prompt is $size bytes, over the agy cap of $cap bytes (agy silently stops reading near 190 KB); set CONCLAVE_ARGON_MAX_BYTES to override"
  fi
  printf '%s\n' "$headsha" > "$out/gate-head.txt"
  bot=$(printf '%s' "$bot" | tr -d '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  [ -z "$bot" ] || printf '%s\n' "$bot" > "$out/builder-bot.txt"
  rm -f "$out/gate.md" "$out/gate-verdict.json"
  local raw="$out/gate.stream.jsonl" err="$out/gate.err" ec
  local -a noenv=(env -u TYPESAFE_API_KEY -u JEV_API_KEY)
  case "$name" in
    agy)
      mkdir -p "$W/.agents/agents"
      cat > "$W/.agents/agents/conclave-voter.md" <<'A'
---
name: conclave-voter
description: Conclave council voter. Answers from the user message alone. No tools.
mainAgent: true
excludeDefaultComponents: true
tools: []
---
# Conclave voter

Everything you need is in the user message: the brief and, for a review, the diff.
You have no tools. Do not try to read files, run commands, or open URLs.
Answer in plain text, in the format the user message asks for.
A
      ( cd "$W" && jq -cn --rawfile c "$P/PROMPT.md" '{event:"user",message:{role:"user",content:$c}}' \
        | "${noenv[@]}" "$bin" --model "$model" --agent conclave-voter --disable-slash-commands --sandbox \
          --print-timeout 1500s --input-format stream-json --output-format stream-json -p= ) > "$raw" 2> "$err"
      ec=$?
      echo "$name exit=$ec"
      python3 "$ROOT/scripts/merge_gate.py" extract "$raw" "$out/gate.md" || ec=1 ;;
    cursor-agent)
      ( cd "$W" && "${noenv[@]}" "$bin" -p --sandbox enabled --trust --output-format stream-json \
          --workspace "$W" --model "$model" < "$P/PROMPT.md" ) > "$raw" 2> "$err"
      ec=$?
      echo "$name exit=$ec"
      python3 "$ROOT/scripts/merge_gate.py" cursor-result "$raw" "$out/gate.md" || ec=1 ;;
    codex)
      ( cd "$W" && "${noenv[@]}" "$bin" exec -m "$model" -s read-only \
          -c sandbox_workspace_write.network_access=false --skip-git-repo-check --ephemeral \
          -C "$W" --output-last-message "$P/answer.md" - < "$P/PROMPT.md" ) > "$raw" 2> "$err"
      ec=$?
      echo "$name exit=$ec"
      python3 "$ROOT/scripts/merge_gate.py" plain-result "$P/answer.md" "$out/gate.md" || ec=1 ;;
    claude)
      ( cd "$W" && "${noenv[@]}" "$bin" -p --permission-mode dontAsk --tools "" --output-format json \
          --model "$model" < "$P/PROMPT.md" ) > "$raw" 2> "$err"
      ec=$?
      echo "$name exit=$ec"
      python3 "$ROOT/scripts/merge_gate.py" claude-result "$raw" "$out/gate.md" || ec=1 ;;
    muse)
      ( cd "$W" && "${noenv[@]}" "$bin" exec --json --provider meta --model "$model" --contributor-mode off \
          --disable-shell --disable-write --disable-web-tools --disable-approval \
          --prompt-file "$P/PROMPT.md" --max-model-steps 8 < /dev/null ) > "$raw" 2> "$err"
      ec=$?
      echo "$name exit=$ec"
      python3 "$ROOT/scripts/merge_gate.py" muse-result "$raw" "$out/gate.md" || ec=1 ;;
  esac
  python3 "$ROOT/scripts/merge_gate.py" parse "$out/gate.md" > "$out/gate-verdict.json"
  cat "$out/gate-verdict.json"
  return "$ec"
}

case "${1:-}" in
  review) shift; review "$@"; exit $? ;;
  merge) shift
    if [ "${1:-}" = --builder-bot ] && [ "$#" -eq 4 ] && [ -n "$2" ]; then :
    else [ "$#" -eq 2 ] || usage; fi
    MERGE_GATE_GH="$GH" exec python3 "$ROOT/scripts/merge_gate.py" merge "$@" ;;
  *) usage ;;
esac
