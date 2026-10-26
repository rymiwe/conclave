#!/usr/bin/env bash
# Merge gate: one tool-less reviewer on a PR diff, and the only merge path.
#
#   scripts/merge-gate.sh review <repo_dir> <base_ref> <head_ref> <pr_url> <out_dir> <model>
#   scripts/merge-gate.sh merge  <pr_url> <out_dir>
#
# review runs one agy model as a tool-less voter on the PR description and the
# diff, writes gate-prompt.md, gate.stream.jsonl, gate.err, gate.md (the
# reviewer's answer), gate-head.txt (the reviewed head sha), and
# gate-verdict.json (the parsed one_way line) under <out_dir>.
#
# merge refuses, nonzero, when gate.md says one_way: yes, when its one_way
# line is missing or unparseable, when the PR has the one-way label, or when
# the labels cannot be read. Otherwise it runs gh pr merge --squash, pinned to
# the reviewed head sha. See scripts/merge_gate.py.
#
# MERGE_GATE_GH (default /usr/local/bin/gh) and MERGE_GATE_AGY (default agy)
# override the binaries; the tests stub both.
set -uo pipefail
ROOT="$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GH="${MERGE_GATE_GH:-/usr/local/bin/gh}"
AGY="${MERGE_GATE_AGY:-agy}"
usage() { sed -n '4,5p' "${BASH_SOURCE[0]}" | sed 's/^# *//' >&2; exit 2; }

review() {
  [ "$#" -eq 6 ] || usage
  local repo=$1 base=$2 head=$3 pr=$4 out=$5 model=$6
  mkdir -p "$out" || exit 1
  GATE_TMP=$(mktemp -d) || exit 1
  trap 'rm -rf "$GATE_TMP"' EXIT
  local P=$GATE_TMP
  mkdir -p "$P/.agents/agents"
  cat > "$P/.agents/agents/conclave-voter.md" <<'A'
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
  ( cd "$P" && jq -cn --rawfile c PROMPT.md '{event:"user",message:{role:"user",content:$c}}' \
    | env -u TYPESAFE_API_KEY -u JEV_API_KEY "$AGY" --model "$model" --agent conclave-voter --disable-slash-commands --sandbox \
      --print-timeout 1500s --input-format stream-json --output-format stream-json -p= ) > "$out/gate.stream.jsonl" 2> "$out/gate.err"
  local ec=$?
  echo "agy exit=$ec"
  python3 "$ROOT/scripts/merge_gate.py" extract "$out/gate.stream.jsonl" "$out/gate.md" || ec=1
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
