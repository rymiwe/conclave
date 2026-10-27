#!/usr/bin/env bash
# Merge gate: one read-only reviewer on a PR diff, and the only merge path.
#
#   scripts/merge-gate.sh review <repo_dir> <base_ref> <head_ref> <pr_url> <out_dir> <builder_vendor>
#   scripts/merge-gate.sh merge  <pr_url> <out_dir>
#
# review asks `seat-usage --route review --cached --json --builder-vendor V`
# for the reviewer and its launch argv (saved as gate-route.json). When the
# route asks, fails, names none, or returns no argv list (a seat-usage older
# than that contract), it refuses (exit 3, one line) before any reviewer runs.
# It dispatches on the argv's first element (agy, cursor-agent, codex, claude,
# muse) and runs that reviewer read-only from an empty temp dir, with the PR
# description and the diff in the prompt, never the repo as a workspace. Any
# other runner refuses. It writes gate-prompt.md, gate.stream.jsonl (the
# reviewer's raw output), gate.err, gate.md (the reviewer's answer),
# gate-head.txt (the reviewed head sha), and gate-verdict.json (the parsed
# one_way line) under <out_dir>.
#
# merge refuses, nonzero, when gate.md says one_way: yes, when its one_way
# line is missing or unparseable, when the PR has the one-way label, or when
# the labels cannot be read. Otherwise it runs gh pr merge --squash, pinned to
# the reviewed head sha. See scripts/merge_gate.py.
#
# MERGE_GATE_GH (default /usr/local/bin/gh) and MERGE_GATE_SEAT_USAGE (default
# seat-usage) override those binaries. MERGE_GATE_AGY, MERGE_GATE_CURSOR_AGENT,
# MERGE_GATE_CODEX, MERGE_GATE_CLAUDE and MERGE_GATE_MUSE replace the reviewer
# binary seat-usage names; the tests stub them all.
set -uo pipefail
ROOT="$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
  [ "$#" -eq 6 ] || usage
  local repo=$1 base=$2 head=$3 pr=$4 out=$5 vendor=$6 picked model why name bin
  mkdir -p "$out" || exit 1
  # A refusal must not leave an earlier verdict for merge to read.
  rm -f "$out/gate.md" "$out/gate-verdict.json" "$out/gate-head.txt" "$out/gate-route.json"
  if ! "$SEAT_USAGE" --route review --cached --json --builder-vendor "$vendor" \
      > "$out/gate-route.json" 2> "$out/gate-route.err"; then
    why=$(grep -v '^\[seat-usage\]' "$out/gate-route.err" | head -n 1)
    refuse "seat-usage --route review failed: ${why:-no output}"
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
  echo "merge-gate: reviewer $model on $name (seat-usage --route review, builder vendor $vendor)"
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
  printf '%s\n' "$headsha" > "$out/gate-head.txt"
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
          --workspace "$W" --model "$model" "$(cat "$P/PROMPT.md")" < /dev/null ) > "$raw" 2> "$err"
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
          --model "$model" "$(cat "$P/PROMPT.md")" < /dev/null ) > "$raw" 2> "$err"
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
  merge) shift; [ "$#" -eq 2 ] || usage
    MERGE_GATE_GH="$GH" exec python3 "$ROOT/scripts/merge_gate.py" merge "$1" "$2" ;;
  *) usage ;;
esac
