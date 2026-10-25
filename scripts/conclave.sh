#!/usr/bin/env bash
# Conclave chair: route, run print-only voters in parallel, anonymize, call Jev.
# Does not judge in this process. Does not edit application source.
# scripts/council.sh is a deprecated alias that execs this file.
set -eu

INVOKE_CWD="$(pwd -P)"
# BASH_SOURCE is this file when it is executed and when a test sources it.
ROOT="$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/scripts/home-env.sh"
# shellcheck disable=SC1091
. "$ROOT/scripts/conclave-env.sh"
# Every child of this script is a voter leg or the judge. Hooks that print
# routing advice (seat-usage --cached, seat-route-hook) stay silent for them.
# Both names are exported. CONCLAVE_VOTER is the current name.
conclave_export VOTER 1
# cursor-agent legs fetch their plugin marketplace from git@github.com. Rewrite
# GitHub SSH URLs to HTTPS for this run only. CONCLAVE_GITHUB_HTTPS=0 turns it off.
# shellcheck disable=SC1091
. "$ROOT/scripts/github-https-env.sh"
cd "$ROOT"
# shellcheck disable=SC1091
. "$ROOT/scripts/review-kind.sh"
# shellcheck disable=SC1091
. "$ROOT/scripts/audit-kind.sh"
# shellcheck disable=SC1091
. "$ROOT/scripts/ask-mode.sh"

DEFAULT_VOTERS="claude,codex"
# No --voters: the lineup seats the ballot. Kind review uses --kind review. See apply_kind_voters.
# A missing router, a failed run, or mode ask keeps the fixed default.
VOTERS="$DEFAULT_VOTERS"
VOTERS_EXPLICIT=0
BALLOT_SOURCE=""
BALLOT_SOURCE_NOTE=""
BALLOT_EXCLUDED_NAME=""
BALLOT_EXCLUDED_WHY=""
OPUS_LEG_SKIPPED=0
ID=""
DRY_RUN=0
FORCE_MAX=0
FLAG_KIND=""
FLAG_DIFFICULTY=""
FLAG_CLAUDE=""
FABLE_OVERFLOW=""
FLAG_REPO=""
FLAG_PR=""
FLAG_BASE=""
FLAG_HEAD=""
FLAG_WHOLE=0
FLAG_TARGET=""
FLAG_TARGET_TYPE=""
CONTEXT_FILES=()
ATTACH_FILES=()
ATTACH_CANON=()
ATTACH_REL=()
KEEP_WORKTREES=0
FINDINGS_MODE=0
VERDICTS_MODE=0
JUDGE_MODE=0
CANNED_JEV=""
HEADER_KIND=""
HEADER_DIFFICULTY=""
HEADER_CLAUDE=""
HEADER_EFFORT=""
HEADER_AUTHOR=""
HEADER_TARGET=""
HEADER_TARGET_TYPE=""
HEADER_ATTACH=""
KIND=""
DIFFICULTY=""
SOURCE=""
CLAUDE_SKU=""
CLAUDE_MODEL_ALIAS=""
CLAUDE_EFFORT=""
CODEX_EFFORT=""
CODEX_MODEL=""
GROK_EFFORT=""
MUSE_EFFORT=""
ARGON_EFFORT=""
ARGON_PREFLIGHT_OK=0
PACKET=""
PACKET_TMP=""
ROUTING_OUT=""
SNAPSHOT_PATH=""
PACKET_MIRROR=""

cleanup_packet_tmp() {
  # EXIT traps run in subshells too; never rm the live packet from $(...) or ( ).
  [ "${BASH_SUBSHELL:-0}" -eq 0 ] || return 0
  if [ -n "${PACKET_TMP:-}" ] && [ -d "$PACKET_TMP" ]; then
    rm -rf "$PACKET_TMP"
  fi
  PACKET_TMP=""
}

cleanup_on_exit() {
  local ec=$?
  # A writer failure must not replace Conclave's exit status.
  council_record_outcome after "$ec" || true
  cleanup_packet_tmp
  review_cleanup_worktrees
}
trap cleanup_on_exit EXIT

COUNCIL_LEG_PGIDS=""

council_bg() {
  # Run "$@" in the background as its own process group and record it. A
  # leg's CLI keeps that group after the leg shell dies, and the leg shells
  # die first when a signal reaches them too: `pkill -f "conclave.sh <id>"`
  # matches them, because a forked subshell keeps the script's argv. The
  # council.sh alias execs this file, so that pattern matches an alias
  # launch too. Stdin is
  # /dev/null, as for any background job without job control.
  set -m
  "$@" </dev/null &
  set +m
  COUNCIL_LEG_PGIDS="$COUNCIL_LEG_PGIDS $!"
}

council_targets() {
  # Pids to stop, from one ps snapshot: every descendant of this shell, and
  # every process in a recorded leg group within this shell's session. Call
  # it inside $(...): that subshell, its ps, and its awk are left out, so a
  # pid that is already gone by the kill is never on the list. BASHPID is
  # read before the pipe: inside it, each side is already a child.
  local self="${BASHPID:-}"
  ps -A -o pid= -o ppid= -o pgid= -o sid= 2>/dev/null | awk \
    -v root="$$" -v self="$self" -v groups="$COUNCIL_LEG_PGIDS" '
    BEGIN { n = split(groups, g, " "); for (i = 1; i <= n; i++) leg[g[i]] = 1 }
    { kids[$2] = kids[$2] " " $1; pgid[$1] = $3; sid[$1] = $4 }
    END {
      for (p in pgid)
        if ((pgid[p] in leg) && sid[p] == sid[root] && p != self) out[p] = 1
      n = split(kids[root], queue, " ")
      for (i = 1; i <= n; i++) {
        if (queue[i] == self) continue
        out[queue[i]] = 1
        m = split(kids[queue[i]], more, " ")
        for (j = 1; j <= m; j++) queue[++n] = more[j]
      }
      for (p in out) print p
    }'
}

stop_children() {
  # TERM every process this script started, then KILL what is left after 5s.
  # The table is read again before each round, so a child forked after one
  # read is caught by the next. Never a name pattern, so another ballot's
  # legs on the same machine are untouched. TERM, not INT.
  local pids i
  for i in $(seq 1 25); do
    pids="$(council_targets)"
    [ -n "$pids" ] || return 0
    # shellcheck disable=SC2086
    kill -TERM $pids 2>/dev/null || true
    sleep 0.2
  done
  pids="$(council_targets)"
  # shellcheck disable=SC2086
  [ -z "$pids" ] || kill -KILL $pids 2>/dev/null || true
}

on_signal() {
  # Bash resets caught traps in subshells, so this runs in the main shell
  # only. Ignore further signals until the legs are stopped, so a second
  # Ctrl-C cannot skip the KILL. The EXIT trap then removes the worktrees.
  trap '' INT TERM HUP
  stop_children
  exit "$1"
}
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap 'on_signal 129' HUP

path_resolve() {
  CDPATH= cd -P -- "$1" && pwd -P
}

path_is_under() {
  # 0 = under parent; 1 = outside; 2 = could not resolve (callers must fail closed).
  local child parent
  child="$(path_resolve "$1")" || return 2
  parent="$(path_resolve "$2")" || return 2
  case "$child" in
    "$parent"|"$parent"/*) return 0 ;;
    *) return 1 ;;
  esac
}

attach_die() {
  die "attach: $1 is not a regular file inside the target repo or the Conclave clone"
}

attach_council_root() {
  local top
  top="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || printf '%s\n' "$ROOT")"
  path_resolve "$top"
}

attach_target_root() {
  local dir top
  if [ -n "${FLAG_REPO:-}" ]; then
    case "$FLAG_REPO" in
      /*) dir="$FLAG_REPO" ;;
      *) dir="$INVOKE_CWD/$FLAG_REPO" ;;
    esac
  else
    dir="$INVOKE_CWD"
  fi
  dir="$(CDPATH= cd -- "$dir" && pwd -P)" || die "cannot cd to target repo: $dir"
  top="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || printf '%s\n' "$dir")"
  path_resolve "$top"
}

attach_requested() {
  [ "${#ATTACH_FILES[@]}" -gt 0 ] && return 0
  [ -n "${HEADER_ATTACH:-}" ] && return 0
  return 1
}

attach_accept() {
  # $1 is a path from --attach or the attach: header. Canonical path must be
  # a regular file under the target repo or the Conclave clone. A symlink that
  # resolves outside those roots is refused. The message names the given path.
  # A relative path is tried under the target repo root first. If no file is
  # there, it is tried under the directory the script was started from.
  local given="$1" abs canon council target root rel i try_root
  [ -n "$given" ] || return 0
  case "$given" in
    /*) abs="$given" ;;
    *)
      try_root="$(attach_target_root)" || attach_die "$given"
      if [ -f "$try_root/$given" ]; then
        abs="$try_root/$given"
      else
        abs="$INVOKE_CWD/$given"
      fi
      ;;
  esac
  if [ ! -e "$abs" ]; then
    attach_die "$given"
  fi
  canon="$(readlink -f -- "$abs" 2>/dev/null)" || attach_die "$given"
  [ -f "$canon" ] || attach_die "$given"
  council="$(attach_council_root)" || attach_die "$given"
  target="$(attach_target_root)" || attach_die "$given"
  root=""
  case "$canon" in
    "$target"/*) root="$target" ;;
    "$council"/*) root="$council" ;;
  esac
  [ -n "$root" ] || attach_die "$given"
  rel="${canon#"$root"/}"
  [ -n "$rel" ] && [ "$rel" != "$canon" ] || attach_die "$given"
  case "$rel" in
    brief.md|review-snapshot.md|PROMPT.md)
      die "attach: $given would overwrite $rel in the packet"
      ;;
  esac
  for i in "${!ATTACH_CANON[@]}"; do
    [ "${ATTACH_CANON[$i]}" = "$canon" ] && return 0
  done
  ATTACH_CANON+=("$canon")
  ATTACH_REL+=("$rel")
}

attach_resolve() {
  local f tok
  ATTACH_CANON=()
  ATTACH_REL=()
  if [ "${KIND:-}" = "review" ]; then
    if attach_requested; then
      die "--attach is not valid for kind: review (legs have the repo)"
    fi
    return 0
  fi
  if [ "${#ATTACH_FILES[@]}" -gt 0 ]; then
    for f in "${ATTACH_FILES[@]}"; do
      attach_accept "$f"
    done
  fi
  if [ -n "${HEADER_ATTACH:-}" ]; then
    while IFS= read -r tok; do
      [ -n "$tok" ] || continue
      attach_accept "$tok"
    done <<EOF
$(printf '%s\n' "$HEADER_ATTACH" | tr ',' ' ' | tr -s '[:space:]' '\n')
EOF
  fi
}

attach_copy_into() {
  # $1 is the directory that receives each file at its relative path.
  local dest_root="$1" i dest
  [ "${#ATTACH_REL[@]}" -gt 0 ] || return 0
  for i in "${!ATTACH_REL[@]}"; do
    dest="$dest_root/${ATTACH_REL[$i]}"
    if [ -e "$dest" ]; then
      die "attach: ${ATTACH_REL[$i]} collides with an existing file"
    fi
    mkdir -p -- "$(dirname -- "$dest")"
    cp -- "${ATTACH_CANON[$i]}" "$dest"
  done
}

attach_fence() {
  local file="$1" n=3
  if grep -a -q '`' "$file"; then
    n="$(grep -a -oE '`+' "$file" | awk '{ if (length > m) m = length } END { print m + 0 }')"
    if [ "$n" -lt 3 ]; then
      n=3
    else
      n=$((n + 1))
    fi
  fi
  printf '%*s' "$n" '' | tr ' ' '`'
}

attach_prompt_section() {
  [ "${#ATTACH_REL[@]}" -gt 0 ] || return 0
  local i rel file fence last
  printf '%s\n\n' '## Attached files'
  for i in "${!ATTACH_REL[@]}"; do
    rel="${ATTACH_REL[$i]}"
    file="${ATTACH_CANON[$i]}"
    fence="$(attach_fence "$file")"
    printf '### %s\n\n' "$rel"
    printf '%s\n' "$fence"
    cat -- "$file"
    if [ -s "$file" ]; then
      last="$(tail -c 1 -- "$file" || true)"
      case "$last" in
        *$'\n'*|'') ;;
        *) printf '\n' ;;
      esac
    fi
    printf '%s\n\n' "$fence"
  done
}

packet_tmp_is_inside() {
  # 0 = inside parent, or the path could not be resolved (fail closed).
  local ec=0
  path_is_under "$1" "$2" || ec=$?
  [ "$ec" -eq 0 ] || [ "$ec" -eq 2 ]
}

alloc_packet_tmpdir() {
  # Prefer TMPDIR, but never place the voter cwd inside the Conclave clone
  # or the target repo. Return 1 on failure (do not die inside $(...)).
  local worktree target_top tmp base res
  worktree="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || printf '%s\n' "$ROOT")"
  worktree="$(path_resolve "$worktree")" || return 1
  target_top=""
  if [ -n "${REPO:-}" ]; then
    target_top="$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null || printf '%s\n' "$REPO")"
    target_top="$(path_resolve "$target_top")" || return 1
  fi
  base="${TMPDIR:-/tmp}"
  tmp="$(mktemp -d "${base%/}/council-pkt.${ID}.XXXXXX")" || return 1
  if packet_tmp_is_inside "$tmp" "$worktree" \
    || { [ -n "$target_top" ] && packet_tmp_is_inside "$tmp" "$target_top"; }; then
    rm -rf "$tmp"
    tmp="$(mktemp -d "/tmp/council-pkt.${ID}.XXXXXX")" || return 1
    if packet_tmp_is_inside "$tmp" "$worktree" \
      || { [ -n "$target_top" ] && packet_tmp_is_inside "$tmp" "$target_top"; }; then
      rm -rf "$tmp"
      return 1
    fi
  fi
  res="$(path_resolve "$tmp")" || { rm -rf "$tmp"; return 1; }
  printf '%s\n' "$res"
}

reset_plain_preview_dir() {
  # Dry-run preview must be a real directory under DIR, never a symlink escape.
  local preview="$DIR/preview" ec
  if [ -L "$preview" ]; then
    rm -f "$preview"
  elif [ -e "$preview" ]; then
    rm -rf "$preview"
  fi
  mkdir -m 755 -p "$preview" || die "could not create preview directory"
  [ -d "$preview" ] && [ ! -L "$preview" ] || die "preview path must be a plain directory"
  ec=0
  path_is_under "$preview" "$DIR" || ec=$?
  [ "$ec" -eq 0 ] || die "preview path escaped ballot directory"
}

usage() {
  cat <<'EOF'
Usage: scripts/conclave.sh <id> [options]
       scripts/conclave.sh <id> --ask TEXT | --ask-file FILE | --template NAME [--input FILE]...
       scripts/conclave.sh help
       scripts/conclave.sh --record-review --run-id ID --review-file PATH [--task-id ID] [--kind KIND]

scripts/council.sh is a deprecated alias and accepts the same arguments.

Requires tmp/council/<id>/brief.md already written, except in ask mode.

Options:
  --dry-run              Preview routing/packet under tmp/council/<id>/preview/ (no voters; ballot untouched)
  --max                  Allow max/xhigh where the table permits (design+high)
  --voters LIST          Default ballot is seat-usage --route lineup.
                         Families map to claude, codex, grok, and muse.
                         Design and mixed also seat argon unless CONCLAVE_ARGON=0
                         (COUNCIL_ARGON is the alias). Audit does not.
                         Kind review takes the two voters from
                         seat-usage --route lineup --kind review.
                         fable and astra in this list mean claude and codex.
                         A missing router, a failed run, or mode ask uses
                         claude,codex (Fable and Astra) and the lineup says so.
                         This list replaces that ballot. Argon votes when the
                         list or the review route names it. opus is review-only
                         unless kind is audit and --voters names it.
  --record-review        Append the review phase for an existing run and exit.
                         Requires --run-id and --review-file. Does not launch voters.
  --claude-model NAME    fable | opus | cursor-fable
                         cursor-fable runs on Cursor for review, design, and mixed.
  --fable-overflow cursor  Retry a usage-limited Fable leg on Cursor (review only)
  --kind KIND            design | review | mixed | audit
  --difficulty LEVEL     low | med | high
  --repo DIR             Target repo for every kind (default: the directory this script was started from)
  --pr N                 Review kind: pull request number
  --base REF             Review kind: base ref (with --head, default HEAD)
  --head REF             Review kind: head ref
  --whole                Review kind: whole codebase against a synthesized empty tree
                         (never the default; a brief 'pr:' header or --pr is the usual target)
  --keep-worktrees       Review or audit: leave voter worktrees in place
  --findings             Review or audit phase 3: judge findings.json (no voters)
  --verdicts             Audit proposal: Jev choice per change (no voters)
  --target URL-OR-FILE   Audit: page URL or file path (flag wins over the header)
  --target-type TYPE     Audit: content | claims | proposal (required; never inferred)
  --context FILE         Audit: extra file copied into the snapshot under context/ (repeatable)
  --attach FILE          Design, mixed, and audit: copy FILE into the packet (repeatable).
                         A relative path is resolved against the target repo root
                         first, then the directory the script was started from.
                         Review rejects it (legs have the repo)
  --judge                Design and mixed: Jev Decision on the existing opinion files (no voters)
  --canned FILE          With --findings, --verdicts, or ask mode: Jev response JSON, no API call

Ask mode (no brief, no ballot):
  --ask TEXT             Put one question to every named voter
  --ask-file FILE        The question from a file (not with --ask)
  --template NAME        A prompt from scripts/ask-templates/ (cold-read, tell-scan).
                         Needs --input. --ask or --ask-file with it adds a chair note.
  --input FILE           Text the question is about, inlined as data (repeatable)
  --effort LEVEL         low | med | high. Overrides the lean and the pace.
  Voters are claude, codex, grok, and muse, tool-less, from an out-of-repo temp cwd.
  A plain question asks seat-usage --route ask for two voters. CONCLAVE_SEAT_USAGE
  overrides that binary. A template keeps its voters: line. --voters wins.
  Jev picks low, medium, or high from the question. Pace steps that lean once.
  A failed lean uses the router's effort. The efforts are recorded.
  Writes tmp/council/<id>/ask-<template or question>/: PROMPT.md, answer-<voter>.md,
  answers.md (side by side), blind/ (blind.sh), errors.md. --dry-run writes only
  the prompt, under tmp/council/<id>/preview/.

Brief header overrides (YAML-ish, optional, before first heading):
  kind: design | review | mixed | audit
  author: fable | opus | astra | grok | muse
                         Review and mixed exclude this family when it is set.
                         With no author, no caller-passed builder, and no
                         --builder-vendor, they exclude nobody. Audit excludes
                         nobody until this names a family.
  target: URL or file path (audit; a flag wins)
  target-type: content | claims | proposal (audit; required; a flag wins)
  difficulty: low | med | high
  claude: fable | opus
  effort: low | med | high | max
  attach: PATH, PATH     Design, mixed, and audit. Comma- or space-separated.
                         Union with --attach, deduplicated. A relative path is
                         resolved against the target repo root first, then the
                         directory the script was started from. Review rejects it.

Flags override the header. Script owns routing; chair LLM must not override routing.md.
If kind cannot be inferred, the script stops (ask-human): set header kind: or --kind.
kind: audit is never inferred. Pass --kind audit or a kind: audit header.
The target repo is --repo, or the directory the script was started from.
Design and mixed snapshot that tree with git -C. Review reads it the same way.
The brief and every artifact stay in this repo under tmp/council/<id>/.
Nothing is written into the target: a review leg can find the target's main
checkout through git worktree metadata.

Design and mixed voters are packet-first: Claude/Grok run tool-less; Codex uses a
read-only sandbox with cwd in an out-of-repo temp packet (mirrored to
tmp/council/<id>/packet/). A Muse Wonder shadow leg is off unless
CONCLAVE_MUSE_SHADOW=1 (COUNCIL_MUSE_SHADOW is the alias). It runs beside
those voters, packet-first and tool-less, and writes voter-muse.md. That
file is not an opinion. The chair and Jev do not read it. A missing muse
binary, an auth failure, or an unavailable Wonder model skips the leg and does not
fail the run. When the leg runs, the launch passes --json. scripts/muse-usage.py
stores a subscription_usage snapshot at ~/.cache/quartermaster/muse-usage.json
when that stream includes one (MUSE_USAGE_PATH overrides the path). A missing
snapshot or a write error leaves the run unchanged. See docs/routing.md.
--voters ...,muse makes Muse a named voter instead, on every kind. Its
opinion goes to Jev like any other. The shadow does not run beside it. With
no --voters, the ballot is the lineup: every available leg. Design and mixed
also seat Argon (family argon, agy on the Google AI Pro subscription) unless
CONCLAVE_ARGON=0 (COUNCIL_ARGON is the alias). The lineup router does not
seat it. --voters wins: Argon votes only when that list names it. Review and
audit do not seat Argon. Naming it there skips the leg with
"argon: kind <k> is not supported; Argon votes on design and mixed only".
Design excludes nobody. Review and mixed exclude a named author, or on
review the vendor a commit trailer names. With neither they exclude nobody.
A pace-picked builder is not excluded. Audit excludes that vendor when the
brief names the author. The
chair's vendor still votes. The lineup says who sat out and why. A capped
pool is not seated. A pool ahead of pace still votes. When seat-usage is
missing, fails, or returns mode ask, the fixed default is used and the
lineup says so. A named voter passes the routed effort. Review and audit
pass high. Argon follows that same resolved effort: low, medium, or high
(gemini-4-argon-low, gemini-4-argon-medium, gemini-4-argon-high).
CONCLAVE_ARGON_MODEL overrides the id. CONCLAVE_AGY is the binary
(default: command -v agy, else ~/.local/bin/agy). CONCLAVE_ARGON_TIMEOUT
is the print timeout in seconds (default 900). See docs/review.md.
agy must be signed in. Do not opt in to data sharing. Do not mark this
repo trusted in agy. The leg is packet-first: cwd is the out-of-repo packet,
--sandbox, --disable-slash-commands, and no permission skip. A missing agy,
a failed "agy -p /usage", an Argon row at 0% remaining, or a bad answer
writes voter-argon.skipped and does not fail the run. The remaining voters
still need two valid opinions. --judge does not fail because the leg was skipped.
A named voter with no valid ballot does not stop design or mixed when two
opinions from two vendors are already in hand. That name is written to
errors.md and the judgment header. --judge uses the same rule.
--attach and an attach: header copy regular files
from the target repo or this clone into that packet, and put their contents
in PROMPT.md under Attached files. A relative path is resolved against the
target repo root first. If no file exists there, it is resolved against the
directory the script was started from. Review kind gives each voter its own worktree. The
default Anthropic leg is Fable on the reviewer brief. Opus native is a
coverage leg on review kind; its agreement with Fable is one vote. OpenAI
and Grok stay on native methods. See docs/review.md.

Writes under tmp/council/<id>/:
  routing.md
  review-snapshot.md        # design and mixed; names the target path and short HEAD
  voter-claude.md / voter-codex.md / voter-grok.md
  voter-argon.md            # design and mixed; voter-argon.skipped when the leg is skipped
  voter-muse.md             # shadow verdict, or the muse voter's ballot when --voters names it
  opinion-1.md … opinion-N.md   # one per named voter; design and mixed need at least two
  judgment.md               # only when Jev returns a Decision
  options.md                # design and mixed, only on no automatic decision
  errors.md

Does not judge via an LLM chair. The Decision judge is Jev (TypeSafe System One).
If Jev has no key or fails, there is no judgment.md. Re-auth, then re-judge
with --judge. That runs Jev on the existing opinion files and launches no voters.
kind: review uses --findings, not --judge.
kind: audit is never inferred. It uses --findings for content and claims,
and --verdicts for a proposal. See docs/review.md.

Next step after voters:
  - Read judgment.md Decision (implement | implement with changes | no automatic decision)
  - Today no kind builds without the human. Review never returns implement.
  - Design and mixed implement* go to the human with judgment.md until the calibration in docs/routing.md exists
  - no automatic decision: the chair fills options.md; the human picks
  - If Jev has no key or failed: re-auth, then scripts/conclave.sh <id> --judge
EOF
}

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

log_err() {
  # Dry-run must not append into a preserved ballot's errors.md.
  if [ "${DRY_RUN:-0}" -eq 1 ] || [ -z "${ERRORS_FILE:-}" ]; then
    printf '%s\n' "$*" >&2
    return
  fi
  printf '%s\n' "$*" >>"$ERRORS_FILE"
}

errors_file_has_content() {
  # 0 when the errors file has a line other than blank or the "(none)" placeholder.
  # This does not decide failure. REVIEW_FAIL does.
  local line
  [ -n "${ERRORS_FILE:-}" ] && [ -f "$ERRORS_FILE" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'(none)') ;;
      *) return 0 ;;
    esac
  done <"$ERRORS_FILE"
  return 1
}

voter_is_named() {
  # $1 is a voter id. $VOTERS is the comma-separated list from --voters.
  case ",$(printf '%s' "$VOTERS" | tr -d '[:space:]')," in
    *,"$1",*) return 0 ;;
    *) return 1 ;;
  esac
}

reject_review_only_flags() {
  # --repo is every kind. These four are review worktree flags.
  if [ -n "${FLAG_PR}${FLAG_BASE}${FLAG_HEAD}" ] || [ "$KEEP_WORKTREES" -eq 1 ] || [ "$FLAG_WHOLE" -eq 1 ]; then
    die "--pr, --base, --head, --whole, and --keep-worktrees require kind: review"
  fi
}

require_design_voters() {
  # Design and mixed need two named voters before any launch. Review and audit do not.
  if [ "${KIND:-}" = "review" ] || [ "${KIND:-}" = "audit" ]; then
    return 0
  fi
  local n=0 v
  local IFS=','
  for v in $VOTERS; do
    v="$(printf '%s' "$v" | tr -d '[:space:]')"
    [ -n "$v" ] || continue
    n=$((n + 1))
  done
  if [ "$n" -lt 2 ]; then
    die "design and mixed need at least 2 voters; listed $n (${VOTERS})"
  fi
}

council_branch_label() {
  # $1 is a git repo. abbrev-ref prints HEAD on a detached checkout.
  local repo="$1" name sha
  name="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [ "$name" = "HEAD" ]; then
    sha="$(git -C "$repo" rev-parse --short HEAD 2>/dev/null || true)"
    if [ -n "$sha" ]; then
      printf '%s (detached)\n' "$sha"
    else
      printf 'none\n'
    fi
    return 0
  fi
  if [ -n "$name" ]; then
    printf '%s\n' "$name"
  else
    printf 'none\n'
  fi
}

ballot_voters_text() {
  # $1 is a comma-separated voter list. Prints one normalized line.
  local v out=""
  local IFS=','
  for v in $1; do
    v="$(printf '%s' "$v" | tr -d '[:space:]')"
    [ -n "$v" ] || continue
    out="${out:+$out,}$v"
  done
  printf '%s\n' "$out"
}

save_ballot_voters() {
  ballot_voters_text "$VOTERS" >"$DIR/voters.txt"
}

judge_load_voters() {
  # --judge uses the ballot saved by the original run.
  # Missing file: die unless this invocation passed --voters.
  # A passed list that differs from the saved one dies and shows both.
  local saved passed
  if [ ! -s "$DIR/voters.txt" ]; then
    if [ "$VOTERS_EXPLICIT" -eq 0 ]; then
      die "--judge: missing tmp/council/$ID/voters.txt. Pass --voters with the original ballot."
    fi
    return 0
  fi
  saved="$(ballot_voters_text "$(cat "$DIR/voters.txt")")"
  if [ "$VOTERS_EXPLICIT" -eq 1 ]; then
    passed="$(ballot_voters_text "$VOTERS")"
    if [ "$passed" != "$saved" ]; then
      die "--judge: --voters ($passed) differs from the saved ballot ($saved)"
    fi
  fi
  VOTERS="$saved"
}

clear_ballot_artifacts() {
  # Fresh ballot only (real vote): never reuse prior voters/opinions/judgment for this id.
  # Dry-run must not call this — peeking at routing must not destroy evidence.
  rm -f \
    "$DIR"/voter-*.md "$DIR"/voter-*.err "$DIR"/voter-*.ok \
    "$DIR"/voter-*.raw.md "$DIR"/voter-*.prompt.md "$DIR"/voter-*.model.txt \
    "$DIR"/voter-*.stream.jsonl \
    "$DIR"/voter-*.skipped \
    "$DIR"/opinion-*.md \
    "$DIR"/judgment.md "$DIR"/judge-prompt.md "$DIR"/options.md \
    "$DIR"/header.json \
    "$DIR"/jev-*.json "$DIR"/jev-*.md "$DIR"/jev-*.log \
    "$DIR"/review-snapshot.md \
    "$DIR"/muse-shadow.status \
    "$DIR"/classifier.out "$DIR"/classifier.err
  rm -rf "$DIR/packet" "$DIR/preview"
  cleanup_packet_tmp
}

has_voter_schema() {
  local f="$1"
  [ -s "$f" ] || return 1
  grep -q '^## Findings' "$f" \
    && grep -q '^## Approach' "$f" \
    && grep -q '^## Would not do' "$f" \
    && grep -q '^## Uncertainties' "$f"
}

mark_voter_ok() {
  local name="$1"
  local out="$DIR/voter-$name.md"
  if ! has_voter_schema "$out"; then
    log_err "$name: output missing required schema headings"
    rm -f "$out" "$DIR/voter-$name.ok"
    return 1
  fi
  : >"$DIR/voter-$name.ok"
  return 0
}

voter_ballot_ok() {
  # $1 is a voter id. Same bar the main path uses before it will judge.
  local raw="$DIR/voter-$1.md"
  [ -f "$DIR/voter-$1.ok" ] && [ -f "$raw" ] && has_voter_schema "$raw"
}

publish_voter_opinions() {
  # Named voters only. A muse voter is published like any other. The Muse
  # shadow file is not: chair_inputs and Jev read opinions, not a shadow verdict.
  OPINION_N=0
  MISSING_VOTERS=""
  local v raw
  for v in claude codex grok muse argon; do
    voter_is_named "$v" || continue
    if [ "$v" = argon ] && [ -s "$DIR/voter-argon.skipped" ]; then
      continue
    fi
    raw="$DIR/voter-$v.md"
    if voter_ballot_ok "$v"; then
      OPINION_N=$((OPINION_N + 1))
      anonymize "$raw" >"$DIR/opinion-$OPINION_N.md"
    else
      log_err "$v: no valid ballot for this run (missing .ok or schema)"
      MISSING_VOTERS="${MISSING_VOTERS:+$MISSING_VOTERS }$v"
    fi
  done
}

named_voters_missing() {
  local v missing=""
  for v in claude codex grok muse argon; do
    voter_is_named "$v" || continue
    if [ "$v" = argon ] && [ -s "$DIR/voter-argon.skipped" ]; then
      continue
    fi
    voter_ballot_ok "$v" && continue
    missing="${missing:+$missing }$v"
  done
  printf '%s' "$missing"
}

opinion_vendor_count() {
  # Distinct vendors among named voters that already have a valid ballot.
  # A skipped Argon is not a ballot and is not a vendor.
  local v seen=" " n=0 vendor
  for v in claude opus codex grok muse argon; do
    voter_is_named "$v" || continue
    if [ "$v" = argon ] && [ -n "${DIR:-}" ] && [ -s "$DIR/voter-argon.skipped" ]; then
      continue
    fi
    voter_ballot_ok "$v" || continue
    vendor="$(review_voter_vendor "$v")"
    [ -n "$vendor" ] || continue
    case "$seen" in
      *" $vendor "*) ;;
      *) seen="$seen$vendor "; n=$((n + 1)) ;;
    esac
  done
  printf '%s' "$n"
}

ballot_quorum_ok() {
  # 0 when at least two valid opinions come from two vendors.
  # A missing named voter is logged and does not by itself refuse the ballot.
  local vendors
  vendors="$(opinion_vendor_count)"
  if [ "${OPINION_N:-0}" -ge 2 ] && [ "$vendors" -ge 2 ]; then
    if [ -n "${MISSING_VOTERS:-}" ]; then
      log_err "named voter missing: ${MISSING_VOTERS}; continuing with ${OPINION_N} opinions from ${vendors} vendors"
    fi
    return 0
  fi
  if [ -n "${MISSING_VOTERS:-}" ]; then
    printf 'Named voter with no valid ballot: %s. See tmp/council/%s/errors.md\n' \
      "$MISSING_VOTERS" "$ID" >&2
  fi
  if [ "${OPINION_N:-0}" -lt 2 ]; then
    printf 'Need at least 2 valid opinions; got %s. See tmp/council/%s/errors.md\n' \
      "${OPINION_N:-0}" "$ID" >&2
    if [ -z "${ERRORS_FILE:-}" ] || [ ! -s "$ERRORS_FILE" ]; then
      log_err "invalid ballot count: ${OPINION_N:-0}"
    fi
  else
    log_err "fewer than 2 vendors with a valid ballot (${OPINION_N} opinions)"
    printf 'Need at least 2 vendors with a valid opinion; got %s opinion(s) from %s vendor(s). See tmp/council/%s/errors.md\n' \
      "${OPINION_N:-0}" "$vendors" "$ID" >&2
  fi
  return 1
}

record_missing_voters_header() {
  # Merge the missing-voter line into header.json. --judge does not rewrite
  # the rest of the header. An empty list leaves the file alone.
  [ -n "${MISSING_VOTERS:-}" ] || return 0
  [ -n "${DIR:-}" ] || return 0
  HDR_MISSING_VOTERS="$MISSING_VOTERS"
  HDR_DEST="$DIR/header.json"
  export HDR_MISSING_VOTERS HDR_DEST
  python3 - <<'PY'
import json, os
from pathlib import Path
p = Path(os.environ["HDR_DEST"])
meta = {}
if p.is_file():
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        loaded = {}
    if isinstance(loaded, dict):
        meta = loaded
meta["missing_voters"] = os.environ["HDR_MISSING_VOTERS"]
p.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
PY
}

build_review_snapshot() {
  # Snapshot the target repo, not the Conclave clone. Ballot files stay under $DIR.
  local snap="${SNAPSHOT_PATH:-$DIR/review-snapshot.md}"
  local head
  [ -n "${REPO:-}" ] || die "target repo is unset"
  mkdir -p "$(dirname "$snap")"
  head="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || printf 'none')"
  {
    printf '%s\n\n' '# Review snapshot (identical for all voters)'
    printf '%s\n' 'Chair-assembled packet. This is the only repo context voters should use.'
    printf '\n'
    printf 'repo: %s\n' "$REPO"
    printf 'head: %s\n' "$head"
    printf '\n'
    if git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      printf '%s\n' '## git status -sb'
      git -C "$REPO" status -sb 2>/dev/null || true
      printf '\n%s\n' '## git diff --stat HEAD'
      git -C "$REPO" diff --stat HEAD 2>/dev/null || true
      printf '\n%s\n' '## git diff HEAD (capped)'
      git -C "$REPO" diff HEAD 2>/dev/null | head -c 100000 || true
      printf '\n\n%s\n' '## untracked (names)'
      git -C "$REPO" ls-files --others --exclude-standard 2>/dev/null | head -80 || true
    else
      printf '%s\n' '(not a git repo; no diff snapshot)'
    fi
  } >"$snap"
}

prepare_packet_workspace() {
  # Real vote: cwd outside git worktree + human mirror.
  # Dry-run: write only under preview/packet/ (no external temp).
  local mirror snap
  mirror="${PACKET_MIRROR:-$DIR/packet}"
  snap="${SNAPSHOT_PATH:-$DIR/review-snapshot.md}"

  attach_resolve
  if [ "$DRY_RUN" -eq 1 ]; then
    cleanup_packet_tmp
    PACKET_TMP=""
    rm -rf "$mirror"
    mkdir -p "$mirror"
    PACKET="$(CDPATH= cd -P -- "$mirror" && pwd -P)"
    cp "$BRIEF" "$PACKET/brief.md"
    cp "$snap" "$PACKET/review-snapshot.md"
    attach_copy_into "$PACKET"
    voter_prompt >"$PACKET/PROMPT.md"
    return
  fi

  cleanup_packet_tmp
  if ! PACKET_TMP="$(alloc_packet_tmpdir)"; then
    die "could not create packet temp dir outside work tree"
  fi
  [ -n "$PACKET_TMP" ] && [ -d "$PACKET_TMP" ] || die "could not create packet temp dir outside work tree"
  PACKET="$(CDPATH= cd -P -- "$PACKET_TMP" && pwd -P)"
  cp "$BRIEF" "$PACKET/brief.md"
  cp "$snap" "$PACKET/review-snapshot.md"
  attach_copy_into "$PACKET"
  voter_prompt >"$PACKET/PROMPT.md"
  rm -rf "$mirror"
  mkdir -p "$mirror"
  cp -a "$PACKET/." "$mirror/"
}

refresh_packet_preview() {
  # Dry-run: write under DIR/preview/ only; never touch ballot routing/snapshot/packet.
  build_review_snapshot
  prepare_packet_workspace
}

normalize_effort() {
  # map med -> medium for CLI flags; keep low|high|max|xhigh
  case "$1" in
    med) printf 'medium\n' ;;
    medium|low|high|max|xhigh) printf '%s\n' "$1" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

effort_rank() {
  case "$1" in
    low) printf '1\n' ;;
    medium|med) printf '2\n' ;;
    high) printf '3\n' ;;
    xhigh) printf '4\n' ;;
    max) printf '5\n' ;;
    *) printf '0\n' ;;
  esac
}

brief_winning_kind() {
  # --kind wins over the kind: line, same order as resolve_routing.
  # target: lines stay inside this scan so a later kind: line is still seen.
  if [ -n "${FLAG_KIND:-}" ]; then
    printf '%s\n' "$FLAG_KIND"
    return 0
  fi
  local line seen=0
  [ -f "${BRIEF:-}" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      '#'*) break ;;
    esac
    if [ "$seen" -eq 0 ] && [ -z "$line" ]; then
      continue
    fi
    case "$line" in
      kind:*)
        printf '%s' "${line#kind:}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]'
        printf '\n'
        return 0
        ;;
      difficulty:*|claude:*|effort:*|author:*|target-type:*|target:*|attach:*|pr:*|branch:*)
        seen=1
        ;;
      '')
        # A blank line does not end the header, same as parse_brief_header.
        ;;
      *)
        break
        ;;
    esac
  done <"$BRIEF"
  printf '\n'
}

parse_brief_header() {
  HEADER_KIND=""
  HEADER_DIFFICULTY=""
  HEADER_CLAUDE=""
  HEADER_EFFORT=""
  HEADER_AUTHOR=""
  HEADER_TARGET=""
  HEADER_TARGET_TYPE=""
  HEADER_ATTACH=""
  HEADER_PR=""
  HEADER_BRANCH=""
  local audit_keys=0 val
  if [ "$(brief_winning_kind)" = "audit" ]; then
    audit_keys=1
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      '#'*)
        break
        ;;
    esac
    if [ -z "$HEADER_KIND$HEADER_DIFFICULTY$HEADER_CLAUDE$HEADER_EFFORT$HEADER_AUTHOR$HEADER_TARGET$HEADER_TARGET_TYPE$HEADER_ATTACH$HEADER_PR$HEADER_BRANCH" ] && [ -z "$line" ]; then
      continue
    fi
    case "$line" in
      kind:*)
        HEADER_KIND="$(printf '%s' "${line#kind:}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
        ;;
      difficulty:*)
        HEADER_DIFFICULTY="$(printf '%s' "${line#difficulty:}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
        ;;
      claude:*)
        HEADER_CLAUDE="$(printf '%s' "${line#claude:}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
        ;;
      effort:*)
        HEADER_EFFORT="$(printf '%s' "${line#effort:}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
        ;;
      author:*)
        HEADER_AUTHOR="$(printf '%s' "${line#author:}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
        ;;
      attach:*)
        val="$(printf '%s' "${line#attach:}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        if [ -n "$val" ]; then
          HEADER_ATTACH="${HEADER_ATTACH:+$HEADER_ATTACH }$val"
        fi
        ;;
      pr:*)
        HEADER_PR="$(printf '%s' "${line#pr:}" | tr -d '[:space:]')"
        ;;
      branch:*)
        HEADER_BRANCH="$(printf '%s' "${line#branch:}" | tr -d '[:space:]')"
        ;;
      target-type:*)
        if [ "$audit_keys" -ne 1 ]; then
          break
        fi
        HEADER_TARGET_TYPE="$(printf '%s' "${line#target-type:}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
        ;;
      target:*)
        if [ "$audit_keys" -ne 1 ]; then
          break
        fi
        HEADER_TARGET="$(printf '%s' "${line#target:}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        ;;
      '')
        # A blank line does not end the header. Later keys stay headers.
        ;;
      *)
        if [ -n "$HEADER_KIND$HEADER_DIFFICULTY$HEADER_CLAUDE$HEADER_EFFORT$HEADER_AUTHOR$HEADER_TARGET$HEADER_TARGET_TYPE$HEADER_ATTACH$HEADER_PR$HEADER_BRANCH" ]; then
          break
        fi
        break
        ;;
    esac
  done <"$BRIEF"
}

brief_body() {
  # Body without leading key: value header lines.
  # target: and target-type: are header keys only when the winning kind is audit.
  # pr: and branch: are header keys for every kind but audit, whose voters read them.
  local audit=0
  if [ "$(brief_winning_kind)" = "audit" ]; then
    audit=1
  fi
  awk -v audit="$audit" '
    BEGIN { in_body=0 }
    /^kind:|^difficulty:|^claude:|^effort:|^author:|^attach:/ && !in_body { next }
    !audit && /^pr:|^branch:/ && !in_body { next }
    audit && /^target-type:|^target:/ && !in_body { next }
    /^$/ && !in_body { next }
    { in_body=1; print }
  ' "$BRIEF"
}

brief_body_lower() {
  brief_body | tr '[:upper:]' '[:lower:]'
}

infer_kind_heuristic() {
  local body scores_design=0 scores_review=0
  body="$(brief_body_lower)"

  printf '%s\n' "$body" | grep -Eqi \
    '(\bdiff\b|\bpr\b|pull request|\bpatch\b|regression|what.?s wrong|code review|\breview\b|findings|written code)' \
    && scores_review=1
  printf '%s\n' "$body" | grep -Eqi \
    '(what to build|architecture|\bapi\b|data model|\bapproach\b|should we|\bdesign\b|proposal|new feature)' \
    && scores_design=1

  # Section headings often encode intent
  printf '%s\n' "$body" | grep -Eqi '^#+ .*review' && scores_review=1
  printf '%s\n' "$body" | grep -Eqi '^#+ .*design' && scores_design=1

  if [ "$scores_design" -eq 1 ] && [ "$scores_review" -eq 1 ]; then
    printf 'mixed\n'
  elif [ "$scores_review" -eq 1 ]; then
    printf 'review\n'
  elif [ "$scores_design" -eq 1 ]; then
    printf 'design\n'
  else
    printf 'inconclusive\n'
  fi
}

infer_difficulty_heuristic() {
  local body
  body="$(brief_body_lower)"

  if printf '%s\n' "$body" | grep -Eqi \
    '(security|\bauth\b|authentication|authorization|data loss|migration|consistency|public api|multi-package|multi package|shape unknown|unknown shape)'; then
    printf 'high\n'
    return
  fi
  if printf '%s\n' "$body" | grep -Eqi \
    '(\bcopy\b|naming|small test|unit test|lint|single-file|single file|\bdocs\b|readme|typo|comment)'; then
    printf 'low\n'
    return
  fi
  printf 'med\n'
}

judge_resolve_kind() {
  # --judge launches no voters, so a failed model resolver must not stop it.
  # Kind comes from the flag, the brief header, or the existing header.json.
  parse_brief_header
  if [ -n "$FLAG_KIND" ]; then
    KIND="$FLAG_KIND"
  elif [ -n "$HEADER_KIND" ]; then
    KIND="$HEADER_KIND"
  elif [ -f "$DIR/header.json" ]; then
    KIND="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1], encoding="utf-8")).get("kind") or "").strip())' "$DIR/header.json" 2>/dev/null || true)"
  fi
  if [ -z "${KIND:-}" ]; then
    inferred_kind="$(infer_kind_heuristic)"
    if [ "$inferred_kind" != "design" ]; then
      die "kind inconclusive: set brief header 'kind: design|review|mixed' or pass --kind (ask-human; no silent default). kind: audit is never inferred"
    fi
    KIND="design"
  fi
  if [ -z "$KIND" ]; then
    die "kind inconclusive: set brief header 'kind: design|review|mixed' or pass --kind (ask-human; no silent default). kind: audit is never inferred"
  fi
  case "$KIND" in
    design|review|mixed|audit) ;;
    *) die "invalid kind: $KIND" ;;
  esac
}

load_models() {
  local out err
  err="$(mktemp)"
  if ! out="$(bash "$ROOT/scripts/resolve-models.sh" 2>"$err")"; then
    cat "$err" >&2 || true
    rm -f "$err"
    die "resolve-models failed"
  fi
  rm -f "$err"
  # Resolver output is shell-quoted assignments from a local script.
  eval "$out"
}

resolve_routing() {
  local kind_src="" diff_src="" inferred_kind

  load_models
  parse_brief_header

  # Kind. review and mixed are never taken from the keyword heuristic.
  # A review-only flag (--pr, --base, --head, --whole) or a brief pr: header selects review.
  # Design may still be inferred. Anything else asks the human.
  if [ -n "$FLAG_KIND" ]; then
    KIND="$FLAG_KIND"
    kind_src="flags"
  elif [ -n "$HEADER_KIND" ]; then
    KIND="$HEADER_KIND"
    kind_src="header"
  elif [ -n "${FLAG_PR}${FLAG_BASE}${FLAG_HEAD}${HEADER_PR}" ] || [ "$FLAG_WHOLE" -eq 1 ]; then
    KIND="review"
    kind_src="flags"
  else
    inferred_kind="$(infer_kind_heuristic)"
    if [ "$inferred_kind" != "design" ]; then
      # Ask-human: no silent default, no classifier for kind.
      die "kind inconclusive: set brief header 'kind: design|review|mixed' or pass --kind (ask-human; no silent default). kind: audit is never inferred"
    fi
    KIND="design"
    kind_src="heuristic"
  fi

  # Difficulty
  if [ -n "$FLAG_DIFFICULTY" ]; then
    DIFFICULTY="$FLAG_DIFFICULTY"
    diff_src="flags"
  elif [ -n "$HEADER_DIFFICULTY" ]; then
    DIFFICULTY="$HEADER_DIFFICULTY"
    diff_src="header"
  elif [ -z "$diff_src" ]; then
    DIFFICULTY="$(infer_difficulty_heuristic)"
    diff_src="heuristic"
  fi

  case "$KIND" in design|review|mixed|audit) ;; *) die "invalid kind: $KIND" ;; esac
  case "$DIFFICULTY" in low|med|high) ;; *) die "invalid difficulty: $DIFFICULTY" ;; esac
  if [ -n "$HEADER_AUTHOR" ]; then
    case "$HEADER_AUTHOR" in
      fable|opus|astra|grok|muse) ;;
      *) die "invalid author family: $HEADER_AUTHOR (want fable|opus|astra|grok|muse)" ;;
    esac
  fi

  SOURCE="kind=${kind_src}; difficulty=${diff_src}"

  # --- Claude (exactly one: fable XOR opus) ---
  # Always Fable, high effort, every kind (Ryan, 2026-09-30). The limit hit
  # before came from Fable subagents, not Fable itself; voters here are
  # tool-less, so no subagents exist.
  CLAUDE_SKU="fable"
  CLAUDE_EFFORT="high"

  if [ -n "$FLAG_CLAUDE" ]; then
    CLAUDE_SKU="$FLAG_CLAUDE"
  elif [ -n "$HEADER_CLAUDE" ]; then
    CLAUDE_SKU="$HEADER_CLAUDE"
  fi


  if [ -n "$HEADER_EFFORT" ]; then
    CLAUDE_EFFORT="$(normalize_effort "$HEADER_EFFORT")"
  fi

  # An approved downgrade sets CLAUDE_EFFORT after this cap, from the lineup.
  if [ "$CLAUDE_EFFORT" = "max" ] || [ "$CLAUDE_EFFORT" = "xhigh" ]; then
    if [ "$FORCE_MAX" -eq 1 ] || { [ "$HEADER_EFFORT" = "max" ] && [ "$KIND" = "design" ] && [ "$DIFFICULTY" = "high" ]; }; then
      if [ "$CLAUDE_SKU" = "fable" ] && [ "$KIND" = "design" ] && [ "$DIFFICULTY" = "high" ]; then
        CLAUDE_EFFORT="max"
      else
        CLAUDE_EFFORT="high"
        log_err "routing: max/xhigh capped to high (only fable design+high with --max or effort:max)"
      fi
    else
      CLAUDE_EFFORT="high"
    fi
  fi

  if [ "$FORCE_MAX" -eq 1 ] && [ "$CLAUDE_SKU" = "fable" ] && [ "$KIND" = "design" ] && [ "$DIFFICULTY" = "high" ]; then
    CLAUDE_EFFORT="max"
  fi

  case "$CLAUDE_SKU" in
    fable) CLAUDE_MODEL_ALIAS="fable" ;;
    opus) CLAUDE_MODEL_ALIAS="opus" ;;
    cursor-fable) CLAUDE_MODEL_ALIAS="${CURSOR_FABLE:-}" ;;
    *) die "invalid claude sku: $CLAUDE_SKU (want fable|opus|cursor-fable)" ;;
  esac
  if [ -n "$FABLE_OVERFLOW" ] && [ "$KIND" != review ]; then
    die "--fable-overflow is kind review only"
  fi
  if [ "$CLAUDE_SKU" = cursor-fable ] && [ "$KIND" != review ] && [ "$KIND" != design ] && [ "$KIND" != mixed ]; then
    die "cursor-fable is review, design, or mixed only"
  fi
  if [ "$CLAUDE_SKU" = cursor-fable ] && [ -z "${CURSOR_FABLE:-}" ]; then
    die "cursor-fable: Cursor Fable id is empty"
  fi

  # --- Codex (Astra on every kind) ---
  CODEX_MODEL="$CODEX_REVIEW_MODEL"
  [ -n "$CODEX_MODEL" ] || die "codex model unresolved"
  case "$KIND" in
    review)
      # OpenAI stays high on review kind. Difficulty, explicit or inferred,
      # does not lower it. An effort: header below still overrides.
      CODEX_EFFORT="high"
      ;;
    design)
      case "$DIFFICULTY" in
        high) CODEX_EFFORT="high" ;;
        *) CODEX_EFFORT="medium" ;;
      esac
      ;;
    mixed)
      case "$DIFFICULTY" in
        low) CODEX_EFFORT="low" ;;
        high) CODEX_EFFORT="high" ;;
        *) CODEX_EFFORT="medium" ;;
      esac
      ;;
    audit)
      CODEX_EFFORT="high"
      ;;
  esac

  case "${HEADER_EFFORT}" in
    low|med|medium|high)
      CODEX_EFFORT="$(normalize_effort "$HEADER_EFFORT")"
      ;;
  esac
  if [ "$FORCE_MAX" -eq 1 ] && [ "$KIND" = "design" ] && [ "$DIFFICULTY" = "high" ]; then
    CODEX_EFFORT="xhigh"
  fi

  # --- Grok standard ---
  case "$KIND" in
    review)
      case "$DIFFICULTY" in
        low) GROK_EFFORT="low" ;;
        med) GROK_EFFORT="medium" ;;
        high) GROK_EFFORT="high" ;;
      esac
      ;;
    design)
      case "$DIFFICULTY" in
        high) GROK_EFFORT="high" ;;
        *) GROK_EFFORT="medium" ;;
      esac
      ;;
    mixed)
      case "$DIFFICULTY" in
        low) GROK_EFFORT="low" ;;
        high) GROK_EFFORT="high" ;;
        *) GROK_EFFORT="medium" ;;
      esac
      ;;
    audit)
      GROK_EFFORT="high"
      ;;
  esac

  case "${HEADER_EFFORT}" in
    low|med|medium|high)
      GROK_EFFORT="$(normalize_effort "$HEADER_EFFORT")"
      ;;
  esac
  if [ "$FORCE_MAX" -eq 1 ] && [ "$KIND" = "design" ] && [ "$DIFFICULTY" = "high" ]; then
    GROK_EFFORT="xhigh"
  fi

  # Muse follows Grok's resolved effort. Review and audit launches pass high.
  MUSE_EFFORT="$GROK_EFFORT"
  # Argon follows the same resolved effort. The model id maps low, medium, and high.
  ARGON_EFFORT="$GROK_EFFORT"

  # Kind review logs the target repo. Resolve it before the lineup call.
  if [ "$KIND" = "review" ] && [ -z "${REPO:-}" ]; then
    review_resolve_repo
  fi
  apply_kind_voters
}

ballot_use_fixed_default() {
  # Fable and Astra. Review uses the same pair when the route cannot seat two.
  VOTERS="$DEFAULT_VOTERS"
}

review_author_vendor() {
  # Family from the brief author: header, as a seat-usage vendor.
  case "$1" in
    fable|opus) printf 'anthropic\n' ;;
    astra) printf 'openai\n' ;;
    grok) printf 'xai\n' ;;
    muse|wonder|wonder-full) printf 'meta\n' ;;
    argon) printf 'google\n' ;;
    *) return 1 ;;
  esac
}

review_pr_number() {
  # --pr, or a brief pr: header. Empty when this is not a PR review.
  local n=""
  if [ -n "${FLAG_PR:-}" ]; then
    n="$FLAG_PR"
  elif [ -n "${HEADER_PR:-}" ]; then
    n="$(review_header_pr_number "$HEADER_PR")"
  fi
  case "$n" in
    ''|*[!0-9]*) return 0 ;;
  esac
  printf '%s\n' "$n"
}

# Last gh pr view --json author,commits parse, keyed by repo and PR number.
# A failed fetch is not a fact: REVIEW_PR_FACT_SKIP stops the resolvers in
# this same call from asking again, and the caller clears it so a later
# re-seat can try. Caching the failure would hide a builder vendor.
REVIEW_PR_FACT_NUM=""
REVIEW_PR_FACT_VENDOR=""
REVIEW_PR_FACT_HUMAN=""
REVIEW_PR_FACT_SKIP=""

review_pr_load_facts() {
  # Sets REVIEW_PR_FACT_VENDOR (a vendor, "ambiguous", or empty) and
  # REVIEW_PR_FACT_HUMAN (yes or no). Returns 1 when there is no PR or gh fails.
  # A Co-authored-by trailer names a model when its display name contains a
  # known family. The mailbox does not. Co-authored-by: Cursor
  # <cursoragent@cursor.com> has no family in the display name, so it does not.
  # gh pr view --json author has is_bot. This gh omits type; a present type
  # must be User. is_bot false with type absent or User is a human author.
  local num json parsed vendor human key
  num="$(review_pr_number)"
  if [ -z "$num" ]; then
    REVIEW_PR_FACT_NUM=""
    REVIEW_PR_FACT_VENDOR=""
    REVIEW_PR_FACT_HUMAN=no
    return 1
  fi
  key="${REPO:-}:$num"
  if [ "$REVIEW_PR_FACT_NUM" = "$key" ]; then
    return 0
  fi
  if [ -n "$REVIEW_PR_FACT_SKIP" ] && [ "$REVIEW_PR_FACT_SKIP" = "$key" ]; then
    return 1
  fi
  REVIEW_PR_FACT_NUM=""
  REVIEW_PR_FACT_VENDOR=""
  REVIEW_PR_FACT_HUMAN=no
  # No repo yet is not a failed fetch. resolve_routing sets REPO before the
  # lineup; a later call can load once the repo exists.
  [ -n "${REPO:-}" ] || return 1
  json="$(cd "$REPO" && gh pr view "$num" --json author,commits 2>/dev/null)" || {
    REVIEW_PR_FACT_SKIP="$key"
    return 1
  }
  [ -n "$json" ] || {
    REVIEW_PR_FACT_SKIP="$key"
    return 1
  }
  parsed="$(REVIEW_PR_JSON="$json" python3 - <<'PY'
import json, os, re, sys
doc = json.loads(os.environ["REVIEW_PR_JSON"])
families = (
    ("wonder-full", "meta"),
    ("fable", "anthropic"),
    ("opus", "anthropic"),
    ("astra", "openai"),
    ("grok", "xai"),
    ("muse", "meta"),
    ("argon", "google"),
    ("wonder", "meta"),
)
trailer = re.compile(r"(?im)^[ \t]*co-authored-by:[ \t]*(.+)$")
vendors = set()
for commit in doc.get("commits") or []:
    if not isinstance(commit, dict):
        continue
    text = (commit.get("messageHeadline") or "") + "\n" + (commit.get("messageBody") or "")
    for match in trailer.finditer(text):
        line = match.group(1).strip()
        name = line.split("<", 1)[0].strip().lower()
        if name in ("cursor", "cursor agent"):
            continue
        for fam, vendor in families:
            if re.search(r"(?<![a-z0-9])" + re.escape(fam) + r"(?![a-z0-9])", name):
                vendors.add(vendor)
if len(vendors) == 1:
    vendor = next(iter(vendors))
elif vendors:
    vendor = "ambiguous"
else:
    vendor = ""
author = doc.get("author") if isinstance(doc.get("author"), dict) else {}
human = "no"
if author.get("is_bot") is False:
    typ = author.get("type")
    if typ is None or typ == "User":
        human = "yes"
sys.stdout.write(vendor + "\n" + human + "\n")
PY
)" || {
    REVIEW_PR_FACT_SKIP="$key"
    return 1
  }
  vendor="${parsed%%$'\n'*}"
  human="${parsed#*$'\n'}"
  human="${human%%$'\n'*}"
  case "$vendor" in
    anthropic|openai|xai|meta|google|ambiguous) ;;
    *) vendor="" ;;
  esac
  case "$human" in
    yes) ;;
    *) human=no ;;
  esac
  REVIEW_PR_FACT_NUM="$key"
  REVIEW_PR_FACT_VENDOR="$vendor"
  REVIEW_PR_FACT_HUMAN="$human"
  REVIEW_PR_FACT_SKIP=""
  return 0
}

review_resolved_builder_vendor() {
  # Vendor for --builder-vendor, or empty.
  # A brief author: of a known family still maps to its vendor.
  # Otherwise only a PR commit trailer that names one model vendor.
  if [ -n "${HEADER_AUTHOR:-}" ]; then
    review_author_vendor "$HEADER_AUTHOR" || true
    return 0
  fi
  [ "${KIND:-}" = review ] || return 0
  review_pr_load_facts || return 0
  case "$REVIEW_PR_FACT_VENDOR" in
    anthropic|openai|xai|meta|google) printf '%s\n' "$REVIEW_PR_FACT_VENDOR" ;;
  esac
}

review_resolved_author_arg() {
  # The --author value, or empty.
  # A brief author: header keeps passing --author. A GitHub User with no
  # model trailer passes human, on kind review only.
  if [ -n "${HEADER_AUTHOR:-}" ]; then
    printf '%s\n' "$HEADER_AUTHOR"
    return 0
  fi
  [ "${KIND:-}" = review ] || return 0
  review_pr_load_facts || return 0
  case "$REVIEW_PR_FACT_VENDOR" in
    anthropic|openai|xai|meta|google|ambiguous) return 0 ;;
  esac
  if [ "$REVIEW_PR_FACT_HUMAN" = yes ]; then
    printf 'human\n'
  fi
}

review_append_author_args() {
  # $1 is an array name. Adds --author and, on review, --builder-vendor.
  # Load PR facts in this shell. The resolvers run inside $(...), and a
  # subshell's assignments do not survive, so a later reseat would ask gh again.
  local -n _dest="$1"
  local author vendor
  if [ "${KIND:-}" = review ] && [ -z "${HEADER_AUTHOR:-}" ]; then
    review_pr_load_facts || true
  fi
  author="$(review_resolved_author_arg)"
  if [ -n "$author" ]; then
    _dest+=(--author "$author")
  fi
  [ "${KIND:-}" = review ] || return 0
  vendor="$(review_resolved_builder_vendor)"
  if [ -n "$vendor" ]; then
    _dest+=(--builder-vendor "$vendor")
  fi
  # This call is done. A later re-seat may ask gh if this one failed.
  REVIEW_PR_FACT_SKIP=""
}

review_map_voter() {
  # --voters fable,astra is the same override as claude,codex. Review only.
  case "$1" in
    fable) printf 'claude\n' ;;
    astra) printf 'codex\n' ;;
    wonder|wonder-full) printf 'muse\n' ;;
    *) printf '%s\n' "$1" ;;
  esac
}

review_alias_voters() {
  [ "${KIND:-}" = "review" ] || return 0
  [ "${VOTERS_EXPLICIT:-0}" -eq 1 ] || return 0
  local out="" v mapped
  local IFS=','
  for v in $VOTERS; do
    v="$(printf '%s' "$v" | tr -d '[:space:]')"
    [ -n "$v" ] || continue
    mapped="$(review_map_voter "$v")"
    out="${out:+$out,}$mapped"
  done
  VOTERS="$out"
}

note_review_route_fallback() {
  # Stderr always. errors.md on a real run. Dry-run leaves that file alone.
  # review_write_lineup copies the same line into lineup.md.
  [ "${KIND:-}" = "review" ] || return 0
  [ "${BALLOT_SOURCE:-}" = "fallback" ] || return 0
  local msg
  msg="warning: review route unavailable (${BALLOT_SOURCE_NOTE:-seat-usage unavailable}); using Fable and Astra"
  BALLOT_ROUTE_WARNING="$msg"
  printf '%s\n' "$msg" >&2
  if [ "${DRY_RUN:-0}" != 1 ] && [ -n "${ERRORS_FILE:-}" ]; then
    printf '%s\n' "$msg" >>"$ERRORS_FILE"
  fi
}

ballot_source_line() {
  # One line for routing.md, the dry-run, and the printed lineup.
  if [ "$VOTERS_EXPLICIT" -eq 1 ]; then
    printf 'ballot: --voters'
    return 0
  fi
  if [ "${BALLOT_SOURCE:-}" = lineup ]; then
    printf 'ballot: lineup'
    return 0
  fi
  printf 'ballot: fallback (%s; fixed default)' "${BALLOT_SOURCE_NOTE:-seat-usage unavailable}"
}

excluded_line() {
  # Empty when the router did not say who sat out. Stubs from older tests omit it.
  if [ -z "${BALLOT_EXCLUDED_WHY:-}" ]; then
    return 0
  fi
  if [ -z "${BALLOT_EXCLUDED_NAME:-}" ] || [ "$BALLOT_EXCLUDED_NAME" = none ]; then
    printf 'excluded: none (%s)' "$BALLOT_EXCLUDED_WHY"
    return 0
  fi
  printf 'excluded: %s (%s)' "$BALLOT_EXCLUDED_NAME" "$BALLOT_EXCLUDED_WHY"
}

lineup_parse_voters() {
  # $1 is seat-usage --route lineup --json. A stub may print the object.
  # Prints "ok" and a comma list, "ask", or "bad".
  # The family/seat map lives in scripts/lineup.py. A missing seat is bad.
  python3 "$ROOT/scripts/lineup.py" "$1"
}

load_lineup_ballot() {
  # Never stops the run. The control plane already ran the lineup.
  # A brief author: header naming a known family passes --author, and on
  # review also --builder-vendor for that family. With no header, review
  # passes --builder-vendor only when a PR commit trailer names one model.
  # A GitHub User author with no such trailer passes --author human and no
  # --builder-vendor. A display name of Cursor does not name a model. The
  # mailbox does not decide. resolve_routing has already set REPO for a
  # review; the --repo branch below is only for a direct loader call.
  # Mixed still omits --author unless the brief has one, and passes no
  # builder, so the router excludes nobody. Review does the same when it
  # passes neither --author nor --builder-vendor. --author human excludes
  # nobody. A pace-picked builder is not excluded. Audit has no default author.
  local bin raw parsed status legs args tail name why line rest
  bin="$(conclave_env SEAT_USAGE "$HOME/.local/bin/seat-usage")"
  if [ -z "$bin" ] || [ ! -x "$bin" ]; then
    BALLOT_SOURCE=fallback
    BALLOT_SOURCE_NOTE="seat-usage missing"
    ballot_use_fixed_default
    note_review_route_fallback
    return 0
  fi
  args=(--route lineup --json --kind "$KIND")
  if [ "$KIND" = "review" ]; then
    if [ -z "${REPO:-}" ] && [ -n "${FLAG_REPO:-}" ]; then
      review_resolve_repo
    fi
  fi
  review_append_author_args args
  if [ "$KIND" = "review" ]; then
    if [ -n "${ID:-}" ]; then
      args+=(--ballot "$ID")
    fi
    if [ -n "${REPO:-}" ]; then
      args+=(--repo "$REPO")
    fi
  fi
  raw="$("$bin" "${args[@]}" 2>/dev/null)" || raw=""
  if [ -z "$raw" ]; then
    BALLOT_SOURCE=fallback
    BALLOT_SOURCE_NOTE="seat-usage failed"
    ballot_use_fixed_default
    note_review_route_fallback
    return 0
  fi
  parsed="$(lineup_parse_voters "$raw" 2>/dev/null)" || parsed=""
  status="${parsed%%$'\n'*}"
  case "$status" in
    ok)
      rest="${parsed#*$'\n'}"
      legs="${rest%%$'\n'*}"
      rest="${rest#*$'\n'}"
      if [ "$rest" = "$legs" ]; then
        rest=""
      fi
      if [ "${rest%%$'\n'*}" = cursor-fable ]; then
        CLAUDE_SKU="cursor-fable"
        CLAUDE_MODEL_ALIAS="${CURSOR_FABLE:-}"
        if [ "$rest" = cursor-fable ]; then
          rest=""
        else
          rest="${rest#*$'\n'}"
        fi
      fi
      line="${rest%%$'\n'*}"
      if [[ "$line" == downgraded:* ]]; then
        LINEUP_DOWNGRADED_EFFORT="${line#downgraded:}"
        if [ "$rest" = "$line" ]; then
          rest=""
        else
          rest="${rest#*$'\n'}"
        fi
      fi
      if [ -n "$rest" ]; then
        name="${rest%%$'\n'*}"
        why="${rest#*$'\n'}"
        if [ "$why" != "$rest" ]; then
          BALLOT_EXCLUDED_NAME="$name"
          BALLOT_EXCLUDED_WHY="$why"
        fi
      fi
      if [ -n "$legs" ]; then
        BALLOT_SOURCE=lineup
        BALLOT_SOURCE_NOTE=""
        VOTERS="$legs"
        return 0
      fi
      ;;
    downgrade)
      rest="${parsed#*$'\n'}"
      legs="${rest%%$'\n'*}"
      why="${rest#*$'\n'}"
      if [ -n "$legs" ] && [ "$why" != "$rest" ]; then
        BALLOT_SOURCE=lineup
        BALLOT_SOURCE_NOTE=""
        BALLOT_DOWNGRADE_WHY="$why"
        VOTERS="$legs"
        return 0
      fi
      ;;
    ask)
      BALLOT_SOURCE=fallback
      BALLOT_SOURCE_NOTE="seat-usage asked"
      ballot_use_fixed_default
      note_review_route_fallback
      return 0
      ;;
  esac
  BALLOT_SOURCE=fallback
  BALLOT_SOURCE_NOTE="seat-usage failed"
  ballot_use_fixed_default
  note_review_route_fallback
}

review_add_opus_leg() {
  # Coverage leg. After claude when that seat is seated, else first.
  if voter_is_named opus; then
    return 0
  fi
  local out="" v added=0
  local IFS=','
  for v in $VOTERS; do
    v="$(printf '%s' "$v" | tr -d '[:space:]')"
    [ -n "$v" ] || continue
    out="${out:+$out,}$v"
    if [ "$v" = claude ]; then
      out="${out},opus"
      added=1
    fi
  done
  if [ "$added" -eq 0 ]; then
    out="opus${out:+,$out}"
  fi
  VOTERS="$out"
}

apply_kind_voters() {
  # No --voters: the lineup seats the ballot. An explicit list always wins.
  # opus is the review coverage leg. Design and mixed reject it.
  # When the claude leg is already Opus native, drop opus so it cannot run twice.
  BALLOT_EXCLUDED_NAME=""
  BALLOT_EXCLUDED_WHY=""
  BALLOT_DOWNGRADE_WHY=""
  LINEUP_DOWNGRADED_EFFORT=""
  review_alias_voters
  if [ "$VOTERS_EXPLICIT" -eq 0 ]; then
    load_lineup_ballot
    # Review voters are the route's two seats. Opus joins only when --voters names it.
    if [ "$CLAUDE_SKU" = cursor-fable ] && [ -z "${CURSOR_FABLE:-}" ] \
      && [ -z "${BALLOT_DOWNGRADE_WHY:-}" ]; then
      BALLOT_DOWNGRADE_WHY="Cursor Fable id cannot be resolved: approve a downgraded leg"
    fi
    if [ -n "${LINEUP_DOWNGRADED_EFFORT:-}" ]; then
      CLAUDE_EFFORT="$LINEUP_DOWNGRADED_EFFORT"
    fi
  fi
  if [ "$KIND" != "review" ] && [ "$KIND" != "audit" ]; then
    if voter_is_named opus; then
      die "opus is a review coverage leg; design and mixed reject it in --voters (listed ${VOTERS})"
    fi
    OPUS_LEG_SKIPPED=0
    seat_argon
    return 0
  fi
  OPUS_LEG_SKIPPED=0
  if [ "${CLAUDE_SKU:-fable}" = "opus" ] && voter_is_named claude && voter_is_named opus; then
    OPUS_LEG_SKIPPED=1
    local out="" v
    local IFS=','
    for v in $VOTERS; do
      v="$(printf '%s' "$v" | tr -d '[:space:]')"
      [ -n "$v" ] || continue
      [ "$v" = "opus" ] && continue
      out="${out:+$out,}$v"
    done
    VOTERS="$out"
  fi
  seat_argon
}

seat_argon() {
  # Conclave adds this leg. The lineup router does not.
  # Design and mixed seat it beside the lineup unless CONCLAVE_ARGON=0
  # (COUNCIL_ARGON is the alias). --voters wins: an explicit list is not edited.
  # Review and audit do not seat it. A named argon on those kinds is skipped later.
  if [ "${KIND:-}" = "review" ] || [ "${KIND:-}" = "audit" ]; then
    return 0
  fi
  if [ "${VOTERS_EXPLICIT:-0}" -eq 1 ]; then
    return 0
  fi
  if [ "$(conclave_env ARGON 1)" = "0" ]; then
    return 0
  fi
  voter_is_named argon && return 0
  if [ -n "${VOTERS:-}" ]; then
    VOTERS="${VOTERS},argon"
  else
    VOTERS="argon"
  fi
}

write_routing_md() {
  local codex_model_line="$CODEX_MODEL"
  local muse_enabled="no"
  if muse_shadow_on; then
    muse_enabled="yes"
  fi

  local excl=""
  if [ -n "${BALLOT_EXCLUDED_WHY:-}" ]; then
    excl="$(excluded_line)"
  fi
  mkdir -p "$(dirname "${ROUTING_OUT:-$DIR/routing.md}")"
  cat >"${ROUTING_OUT:-$DIR/routing.md}" <<EOF
# Conclave routing

id: $ID
kind: $KIND
difficulty: $DIFFICULTY
source: $SOURCE
max: $FORCE_MAX
$(ballot_source_line)${excl:+
$excl}
voters: $VOTERS

## Claude voter (one seat)

sku: $CLAUDE_SKU
model_alias: $CLAUDE_MODEL_ALIAS
effort: $CLAUDE_EFFORT
notes: fable on this seat; opus is a review coverage leg and is rejected here

## Codex voter

model: $codex_model_line
effort: $CODEX_EFFORT
config: model_reasoning_effort=$CODEX_EFFORT

## Grok voter

model: $GROK_NATIVE_MODEL (standard; never Fast)
reasoning_effort: $GROK_EFFORT
$(argon_routing_section)

## Judge (Decision authority)

primary: Jev (TypeSafe System One; outside vote+impl set) via scripts/jev-judge.py
notes: Jev is bound to locked decision rules in code. Not a voter. No fallback judge. If Jev has no key or fails, re-auth, then scripts/conclave.sh <id> --judge.

## Muse shadow (non-voting)

enabled: $muse_enabled
model: ${MUSE_WONDER_MODEL:-}
votes: no
chair: excluded
judge: excluded
cwd: out-of-repo packet, tool-less

## Packet policy

mode: packet-first
voter_cwd: out-of-repo temp dir (never under git worktree; mirrored for humans)
mirror_real_vote: tmp/council/$ID/packet/
mirror_dry_run: tmp/council/$ID/preview/packet/
contents: brief + review-snapshot + PROMPT + attached files
notes: all voters start with cwd=packet; Claude/Grok tool-less; Codex read-only sandbox (not a full FS jail)
EOF
}

anonymize() {
  sed -E \
    -e '/^[[:space:]]*Powered by/Id' \
    -e 's/\b(Claude Code|Claude|Anthropic|Codex CLI|Codex|OpenAI|GPT-[0-9a-z.-]+|Grok( [0-9.]+)?|xAI|Fable( [0-9.]+)?|Opus( [0-9.]+)?|Astra)\b/[voter]/gI' \
    -e 's/\bmuse-[a-z0-9.-]*[a-z0-9]/[voter]/gI' \
    -e 's/\bgemini-[a-z0-9.-]*[a-z0-9]/[voter]/gI' \
    -e 's/\b(Muse( Wonder)?|Meta)\b/[voter]/g' \
    -e 's/\b(Gemini|Argon|Google|Antigravity|agy)\b/[voter]/gI' \
    "$1"
}

voter_prompt() {
  local only='the Brief and Review snapshot'
  local snap_note='this is your only repo context'
  local attached=""
  if [ "${#ATTACH_REL[@]}" -gt 0 ]; then
    only='the Brief, the Review snapshot, and the Attached files'
    snap_note='attached files are copied beside this brief'
    attached="$(attach_prompt_section)"
  fi
  cat <<EOF
You are a Conclave voter in PACKET-FIRST mode.

Print-only. Do not edit files. Do not apply patches. Do not run shell commands.
Do not browse or search the repository. Do not open paths outside this packet.
Do not open other ballot files. Use ONLY ${only} below.

Respond with ONLY these sections (markdown headings exactly):

## Findings
## Approach
For each approach you would consider: its lean (the one you would pick and why, in a sentence), tradeoffs, risks, accretiveness (what later work it makes easier or harder), and cost to try in lines and days.
## Would not do
## Uncertainties

Brief:

$(cat "$BRIEF")

Review snapshot (identical for every voter; ${snap_note}):

$(cat "${SNAPSHOT_PATH:-$DIR/review-snapshot.md}")
EOF
  if [ -n "$attached" ]; then
    printf '\n%s\n' "$attached"
  fi
}

muse_shadow_on() {
  [ "$(conclave_env MUSE_SHADOW 0)" = "1" ]
}

# The pin rule is muse-models.py --accept. This only forwards the exit status.
muse_pin_usable() {
  python3 "$ROOT/scripts/muse-models.py" --accept "${MUSE_WONDER_MODEL:-}" >/dev/null 2>&1
}

# Headless flags are the ones `muse exec --help` prints. cwd is the packet.
# The model id is the Wonder full pin from resolve-models.sh and nowhere else.
# The shadow launch does not pass an effort. The named voter does.
# --json is the CLI JSONL event stream. scripts/muse-usage.py reads it for
# Meta's subscription_usage snapshot after muse exits. Limits: docs/routing.md.
muse_launch_argv() {
  printf '%s\n' \
    muse \
    exec \
    --json \
    --provider meta \
    --model "${MUSE_WONDER_MODEL:-}" \
    --contributor-mode \
    off \
    --prompt-file "$DIR/voter-muse.prompt.md" \
    --disable-shell \
    --disable-write \
    --disable-web-tools \
    --disable-approval \
    --user-input-auto-resolve \
    --no-foreign-personal-context \
    --max-model-steps 8 \
    --worktree off
}

muse_exec() {
  local -a args=()
  local line
  while IFS= read -r line; do
    args+=("$line")
  done < <(muse_launch_argv)
  "${args[@]}"
}

muse_write_prompt() {
  cat "$PACKET/PROMPT.md" >"$DIR/voter-muse.prompt.md"
  cat >>"$DIR/voter-muse.prompt.md" <<'EOF'

Shadow only. After the sections above, add this heading and exactly one of these three lines under it:

## Decision
implement
implement with changes
no automatic decision
EOF
}

muse_note_skip() {
  # $1 is missing, auth, unavailable, or failed.
  case "$1" in
    missing) log_err "muse: CLI not found; skipping shadow leg" ;;
    auth) log_err "muse: auth failure; skipping shadow leg" ;;
    unavailable) log_err "muse: Wonder model unavailable; skipping shadow leg" ;;
    *) log_err "muse: invocation failed; skipping shadow leg" ;;
  esac
  printf 'skipped\n' >"$DIR/muse-shadow.status"
  rm -f "$DIR/voter-muse.md" "$DIR/voter-muse.ok"
}

muse_failure_class() {
  python3 - "$1" <<'PY'
import sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read().lower()
auth = (
    "missing meta credentials",
    "meta_api_key",
    "muse login",
    "not logged in",
    "unauthorized",
    "authentication failed",
    "invalid api key",
    "invalid_api_key",
)
unavailable = (
    "model selection failed",
    "using the provider default model",
    "using resolved default model",
    "fetch yielded no resolvable default model",
    "unsupported model",
    "unsupportedmodel",
    "unknown model",
    "serves unavailable",
    "catalog does not carry",
    "muse-spark",
    "contributor",
    "journal model_id does not match",
)
if any(s in text for s in auth):
    print("auth")
elif any(s in text for s in unavailable):
    print("unavailable")
else:
    print("failed")
PY
}

muse_extract_decision() {
  python3 - "$1" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
allowed = ("implement with changes", "no automatic decision", "implement")
match = re.search(r"(?m)^## Decision\b[^\n]*\n(.*)", text, re.S)
if not match:
    raise SystemExit(0)
for line in match.group(1).splitlines():
    line = line.strip().strip("`").strip()
    if line in allowed:
        print(line)
        break
PY
}

muse_normalize() {
  python3 - "$1" "$2" <<'PY'
import re, sys
raw_path, out_path = sys.argv[1], sys.argv[2]
text = open(raw_path, encoding="utf-8", errors="replace").read()
lines = [ln for ln in text.splitlines() if not ln.startswith("muse:")]
text = "\n".join(lines)
text = re.sub(
    r"(?m)(?<!^)(?<![\n])(## (?:Findings|Approach|Would not do|Uncertainties|Decision)\b)",
    r"\n\1",
    text,
)
match = re.search(r"(?m)^## Findings\b", text)
if not match:
    raise SystemExit(2)
body = text[match.start():].strip() + "\n"
for heading in ("## Findings", "## Approach", "## Would not do", "## Uncertainties"):
    if not re.search(rf"(?m)^{re.escape(heading)}\b", body):
        raise SystemExit(2)
open(out_path, "w", encoding="utf-8").write(body)
PY
}

launch_muse_shadow() {
  muse_shadow_on || return 0
  # A named muse voter owns voter-muse.md. The shadow does not run beside it.
  if voter_is_named muse; then
    printf 'skipped\n' >"$DIR/muse-shadow.status"
    MUSE_SHADOW_STATUS=skipped
    return 0
  fi
  if ! command -v muse >/dev/null 2>&1; then
    muse_note_skip missing
    MUSE_SHADOW_STATUS=skipped
    return 0
  fi
  if ! muse_pin_usable; then
    muse_note_skip unavailable
    MUSE_SHADOW_STATUS=skipped
    return 0
  fi
  council_bg run_muse
  PIDS="$PIDS $!"
}

muse_load_status() {
  MUSE_SHADOW_STATUS=""
  muse_shadow_on || return 0
  if [ -f "${DIR:-}/muse-shadow.status" ]; then
    MUSE_SHADOW_STATUS="$(head -n 1 "$DIR/muse-shadow.status" | tr -d '[:space:]')"
  fi
  case "${MUSE_SHADOW_STATUS:-}" in
    ran|skipped) ;;
    *) MUSE_SHADOW_STATUS=skipped ;;
  esac
}

run_muse() {
  local raw="$DIR/voter-muse.raw.md"
  if ! muse_pin_usable; then
    muse_note_skip unavailable
    return 0
  fi
  local err="$DIR/voter-muse.err"
  local out="$DIR/voter-muse.md"
  local ec class
  muse_write_prompt
  ec=0
  (
    cd -- "$PACKET" || exit 97
    muse_exec
  ) >"$raw" 2>"$err" || ec=$?
  muse_capture_usage "$raw" "$err"
  if [ "$ec" -ne 0 ]; then
    class="$(muse_failure_class "$err" || true)"
    [ -n "$class" ] || class=failed
    muse_note_skip "$class"
    rm -f "$raw" "$out"
    return 0
  fi
  class="$(muse_failure_class "$err" || true)"
  if [ "$class" = "unavailable" ] || [ "$class" = "auth" ]; then
    muse_note_skip "$class"
    rm -f "$raw" "$out"
    return 0
  fi
  if [ ! -s "$raw" ]; then
    log_err "muse: empty output; skipping shadow leg"
    printf 'skipped\n' >"$DIR/muse-shadow.status"
    rm -f "$out"
    return 0
  fi
  if ! muse_normalize "$raw" "$out"; then
    log_err "muse: output missing verdict headings; skipping shadow leg"
    printf 'skipped\n' >"$DIR/muse-shadow.status"
    rm -f "$out"
    return 0
  fi
  printf 'ran\n' >"$DIR/muse-shadow.status"
  return 0
}

# Voter leg (--voters ...,muse). The shadow flags, plus a workspace for the
# review and audit legs. Read tools only: no shell, no write tool. $1 is the
# prompt file. $2 is the workspace, or empty for the packet. $3 is web: yes or no.
muse_voter_argv() {
  local prompt="$1" ws="${2:-}" web="${3:-no}"
  printf '%s\n' \
    muse \
    exec \
    --json \
    --provider meta \
    --model "${MUSE_WONDER_MODEL:-}"
  if [ -n "${MUSE_EFFORT:-}" ]; then
    printf '%s\n' --reasoning-effort "$MUSE_EFFORT"
  fi
  printf '%s\n' \
    --contributor-mode \
    off \
    --prompt-file "$prompt"
  if [ -n "$ws" ]; then
    printf '%s\n' --workspace "$ws" --max-model-steps 80
  else
    printf '%s\n' --max-model-steps 8
  fi
  printf '%s\n' --disable-shell --disable-write
  [ "$web" = yes ] || printf '%s\n' --disable-web-tools
  printf '%s\n' \
    --disable-approval \
    --user-input-auto-resolve \
    --no-foreign-personal-context \
    --worktree off
}

# Run one Muse voter leg from the caller's cwd. $4 gets the answer text and
# $5 stderr. Writes $DIR/voter-muse.model.txt from run.model.configured.
# Muse refuses a TMPDIR inside its workspace, so callers keep TMPDIR outside.
muse_voter_run() {
  local prompt="$1" ws="$2" web="$3" raw="$4" err="$5" ec=0 line
  local -a args=()
  rm -f "$DIR/voter-muse.model.txt"
  if ! muse_pin_usable; then
    printf 'muse: FAIL Wonder model unavailable ran=none (resolved %s)\n' "${MUSE_WONDER_MODEL:-none}" \
      >"$DIR/voter-muse.model.txt"
    return 1
  fi
  while IFS= read -r line; do
    args+=("$line")
  done < <(muse_voter_argv "$prompt" "$ws" "$web")
  "${args[@]}" >"$raw" 2>"$err" || ec=$?
  muse_capture_usage "$raw" "$err" "$DIR/voter-muse.model.txt"
  return "$ec"
}

muse_voter_fail_reason() {
  # $1 exit code, $2 stderr path. One line for errors.md or voter-muse.fail.
  local class
  class="$(muse_failure_class "$2" 2>/dev/null || true)"
  printf 'muse: exit %s (%s); see voter-muse.err\n' "$1" "${class:-failed}"
}

run_muse_voter() {
  # Design and mixed: packet-first, the same tool-less flags as the shadow.
  local raw="$DIR/voter-muse.raw.md" err="$DIR/voter-muse.err" out="$DIR/voter-muse.md" ec=0
  (
    cd -- "$PACKET" || exit 97
    muse_voter_run "$PACKET/PROMPT.md" "" no "$raw" "$err"
  ) || ec=$?
  if [ "$ec" -ne 0 ]; then
    log_err "$(muse_voter_fail_reason "$ec" "$err")"
    rm -f "$out"
    return 1
  fi
  if ! grep -q '^muse: ok ' "$DIR/voter-muse.model.txt" 2>/dev/null; then
    log_err "$(head -n 1 "$DIR/voter-muse.model.txt" 2>/dev/null || printf 'muse: FAIL no model proof')"
    rm -f "$out"
    return 1
  fi
  if [ ! -s "$raw" ] || ! muse_normalize "$raw" "$out"; then
    log_err "muse: output missing required schema headings; see voter-muse.raw.md"
    rm -f "$out"
    return 1
  fi
  mark_voter_ok muse
}

# Read the shadow stdout and stderr for a subscription_usage snapshot.
# Rewrites the raw answer when stdout is the JSONL journal. A missing
# snapshot or a write error returns 0 and leaves the verdict path alone.
# $1 is the raw stdout path. $2 is the stderr path. $3, when set, gets the
# voter leg's model proof line.
muse_capture_usage() {
  local raw="$1" err="${2:-}" proof="${3:-}" usage_file
  [ -n "$raw" ] && [ -f "$raw" ] || return 0
  if [ -n "${MUSE_USAGE_PATH:-}" ]; then
    usage_file="$MUSE_USAGE_PATH"
  elif [ -n "${HOME:-}" ]; then
    usage_file="${HOME}/.cache/quartermaster/muse-usage.json"
  else
    usage_file=""
  fi
  python3 "$ROOT/scripts/muse-usage.py" \
    --raw "$raw" \
    --err "$err" \
    --usage-file "$usage_file" \
    --expect-model "${MUSE_WONDER_MODEL:-}" \
    ${proof:+--proof "$proof"} \
    >/dev/null 2>&1 || true
}

argon_routing_section() {
  voter_is_named argon || return 0
  cat <<EOF

## Argon voter

model: $(argon_model_id)
effort: ${ARGON_EFFORT:-}
binary: agy
pool: Google AI Pro
notes: design and mixed only. Packet-first and fail-soft. A missing agy, a failed usage check, a 0% Argon pool, or a bad answer skips the leg.
EOF
}

argon_agy_bin() {
  # CONCLAVE_AGY wins (COUNCIL_AGY is the alias). Else PATH, else ~/.local/bin/agy.
  local bin
  bin="$(conclave_env AGY)"
  if [ -n "$bin" ]; then
    printf '%s\n' "$bin"
    return 0
  fi
  bin="$(command -v agy 2>/dev/null || true)"
  if [ -n "$bin" ]; then
    printf '%s\n' "$bin"
    return 0
  fi
  printf '%s\n' "${HOME:-}/.local/bin/agy"
}

argon_model_id() {
  # CONCLAVE_ARGON_MODEL is a full id. Otherwise the resolved effort picks one.
  local override effort
  override="$(conclave_env ARGON_MODEL)"
  if [ -n "$override" ]; then
    printf '%s\n' "$override"
    return 0
  fi
  effort="${ARGON_EFFORT:-${GROK_EFFORT:-medium}}"
  case "$effort" in
    low) printf 'gemini-4-argon-low\n' ;;
    med|medium) printf 'gemini-4-argon-medium\n' ;;
    *) printf 'gemini-4-argon-high\n' ;;
  esac
}

argon_print_timeout() {
  local t
  t="$(conclave_env ARGON_TIMEOUT 900)"
  case "$t" in
    ''|*[!0-9]*) t=900 ;;
  esac
  printf '%s\n' "$t"
}

argon_unsupported_reason() {
  # $1 is the kind. Audit still skips the leg. Review seats it.
  if [ "$1" = "review" ]; then
    printf 'argon: kind review seats this leg when the lineup or --voters names it\n'
    return 0
  fi
  printf 'argon: kind %s is not supported; Argon votes on design and mixed only\n' "$1"
}

argon_zero_rows() {
  # Argon usage rows whose remaining percent is 0%. Other percents are ignored.
  awk -F '\t' '
    {
      gsub(/\r/, "", $0)
      pool = $1
      pct = $3
      gsub(/^[ \t]+|[ \t]+$/, "", pool)
      gsub(/^[ \t]+|[ \t]+$/, "", pct)
      if (tolower(pool) ~ /gemini 4 argon/ && pct ~ /^0%$/) print
    }
  '
}

argon_write_skipped() {
  # $1 is the verbatim reason. First line of the file is the marker.
  local reason="$1"
  {
    printf 'argon: skipped\n'
    printf '%s\n' "$reason"
  } >"$DIR/voter-argon.skipped"
  rm -f "$DIR/voter-argon.md" "$DIR/voter-argon.ok" "$DIR/voter-argon.model.txt"
}

argon_skip() {
  # $1 is the verbatim reason. Logged and stored. The leg is not fatal.
  local reason="$1" line
  argon_write_skipped "$reason"
  while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$line" ] || continue
    log_err "$line"
  done <<EOF
$reason
EOF
}

note_argon_kind_skip() {
  # Review and audit. Idempotent so a worktree retry does not log twice.
  [ -n "${DIR:-}" ] || return 0
  voter_is_named argon || return 0
  [ -s "$DIR/voter-argon.skipped" ] && return 0
  argon_skip "$(argon_unsupported_reason "${KIND:-}")"
}

argon_preflight() {
  # 0 when the leg may launch. 1 and the verbatim reason on stdout when it may not.
  # agy -p /usage is not a model call. A model turn never runs here.
  local bin out err ec row
  bin="$(argon_agy_bin)"
  if [ -z "$bin" ] || [ ! -x "$bin" ]; then
    printf 'agy: not found: %s\n' "${bin:-agy}"
    return 1
  fi
  out="$(mktemp)"
  err="$(mktemp)"
  ec=0
  # Judge keys never reach agy. SOURCE_ONLY returns before the ballot unset,
  # and COUNCIL_SAVED_* may still hold a copy.
  timeout 60 env \
    -u TYPESAFE_API_KEY \
    -u JEV_API_KEY \
    -u COUNCIL_SAVED_TYPESAFE \
    -u COUNCIL_SAVED_JEV \
    "$bin" -p "/usage" </dev/null >"$out" 2>"$err" || ec=$?
  if [ "$ec" -ne 0 ]; then
    if [ -s "$out" ]; then
      cat "$out"
    fi
    if [ -s "$err" ]; then
      cat "$err"
    fi
    if [ ! -s "$out" ] && [ ! -s "$err" ]; then
      printf 'agy: /usage exit %s\n' "$ec"
    fi
    rm -f "$out" "$err"
    return 1
  fi
  row="$(argon_zero_rows <"$out" || true)"
  rm -f "$out" "$err"
  if [ -n "$row" ]; then
    printf '%s\n' "$row"
    return 1
  fi
  return 0
}

argon_dry_status() {
  # Dry-run only. Prints the preflight. Does not write the ballot.
  voter_is_named argon || return 0
  local reason=""
  if reason="$(argon_preflight)"; then
    printf '  argon=%s effort=%s (packet-first, fail-soft)\n' "$(argon_model_id)" "${ARGON_EFFORT:-}"
    return 0
  fi
  printf '  argon: skipped\n'
  printf '%s\n' "$reason" | sed 's/^/  /'
}

prepare_argon_leg() {
  # Live design and mixed, after the ballot clear. One usage check, then launch or skip.
  ARGON_PREFLIGHT_OK=0
  voter_is_named argon || return 0
  local reason=""
  if reason="$(argon_preflight)"; then
    ARGON_PREFLIGHT_OK=1
    printf '  argon=%s effort=%s (packet-first, fail-soft)\n' "$(argon_model_id)" "${ARGON_EFFORT:-}"
    return 0
  fi
  argon_skip "$reason"
  printf '  argon: skipped\n'
}

argon_agent_name() {
  printf 'conclave-voter\n'
}

argon_write_agent() {
  # $1 is the packet. A tool-less agy agent scoped to this packet only.
  # The prompt already carries the diff, so the voter needs no tools, and a
  # headless run cannot approve one (agy auto-denies and answers empty).
  # Never ~/.gemini: the file lives in the packet temp dir and dies with it.
  local dir="$1/.agents/agents"
  mkdir -p -- "$dir" || return 1
  cat >"$dir/$(argon_agent_name).md" <<'EOF'
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
EOF
}

run_argon() {
  # Packet-first. The prompt is PROMPT.md on stdin as one stream-json user event.
  # cwd is the packet. A failure writes voter-argon.skipped and returns 0.
  local bin model timeout stream err raw out ec parse_ec reason
  bin="$(argon_agy_bin)"
  model="$(argon_model_id)"
  timeout="$(argon_print_timeout)"
  stream="$DIR/voter-argon.stream.jsonl"
  err="$DIR/voter-argon.err"
  raw="$DIR/voter-argon.raw.md"
  out="$DIR/voter-argon.md"
  rm -f "$out" "$DIR/voter-argon.ok" "$DIR/voter-argon.model.txt" "$stream" "$raw"
  if [ -z "${PACKET:-}" ] || [ ! -f "$PACKET/PROMPT.md" ]; then
    argon_skip "argon: packet prompt missing"
    return 0
  fi
  if [ -z "$bin" ] || [ ! -x "$bin" ]; then
    argon_skip "agy: not found: ${bin:-agy}"
    return 0
  fi
  if ! argon_write_agent "$PACKET"; then
    argon_skip "argon: cannot write the tool-less voter agent"
    return 0
  fi
  ec=0
  (
    set -o pipefail
    cd -- "$PACKET" || exit 97
    jq -cn --rawfile c "$PACKET/PROMPT.md" '{event:"user",message:{role:"user",content:$c}}' \
      | env \
          -u TYPESAFE_API_KEY \
          -u JEV_API_KEY \
          -u COUNCIL_SAVED_TYPESAFE \
          -u COUNCIL_SAVED_JEV \
          "$bin" \
          --model "$model" \
          --agent "$(argon_agent_name)" \
          --disable-slash-commands \
          --sandbox \
          --print-timeout "${timeout}s" \
          --input-format stream-json \
          --output-format stream-json \
          -p=
  ) >"$stream" 2>"$err" || ec=$?
  if [ "$ec" -ne 0 ]; then
    reason="$(cat "$err" 2>/dev/null || true)"
    if [ -z "$reason" ]; then
      reason="$(cat "$stream" 2>/dev/null || true)"
    fi
    if [ -z "$reason" ]; then
      reason="agy: exit ${ec}"
    fi
    argon_skip "$reason"
    return 0
  fi
  parse_ec=0
  local parse_reason
  parse_reason="$(mktemp)"
  python3 - "$stream" "$raw" "$parse_reason" "$err" "$(argon_agent_name)" <<'PY' || parse_ec=$?
import json, re, sys
stream_path, raw_path, reason_path, err_path, agent_name = sys.argv[1:6]
result = None
init = None
try:
    lines = open(stream_path, encoding="utf-8", errors="replace").read().splitlines()
except OSError as exc:
    open(reason_path, "w", encoding="utf-8").write(str(exc))
    raise SystemExit(1)
try:
    stderr = open(err_path, encoding="utf-8", errors="replace").read()
except OSError:
    stderr = ""
def fail(msg):
    # The reason is the leg's skip text. agy stderr follows verbatim.
    body = msg.rstrip("\n") + "\n"
    if stderr.strip():
        body += stderr if stderr.endswith("\n") else stderr + "\n"
    open(reason_path, "w", encoding="utf-8").write(body)
    raise SystemExit(1)
for line in lines:
    line = line.strip()
    if not line:
        continue
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        continue
    if isinstance(ev, dict) and ev.get("event") == "init" and init is None:
        init = ev.get("init")
    if isinstance(ev, dict) and ev.get("event") == "result":
        result = ev.get("result")
def as_text(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value)
if not isinstance(result, dict):
    open(reason_path, "w", encoding="utf-8").write("no result event\n")
    raise SystemExit(1)
status = result.get("status")
if status != "SUCCESS":
    err = as_text(result.get("error")).strip() or "status %s" % (status,)
    open(reason_path, "w", encoding="utf-8").write(err if err.endswith("\n") else err + "\n")
    raise SystemExit(1)
# A run that did not load the tool-less agent had tools, so it is not a vote.
loaded = init.get("agent") if isinstance(init, dict) else None
if loaded != agent_name:
    fail("argon: agy did not load the tool-less voter agent (init agent %r)" % (loaded,))
# A denied tool means the answer was cut short. Exit 0 does not make it a review.
denied = result.get("denied_actions")
if denied:
    fail("argon: agy denied a tool in headless mode: %s" % (as_text(denied),))
if re.search(r"headless mode cannot prompt for|no output produced", stderr):
    fail("argon: agy reported a headless permission denial")
response = result.get("response")
if not isinstance(response, str) or not response.strip():
    err = as_text(result.get("error")).strip()
    fail("argon: empty response (agy exit 0)" + (": " + err if err else ""))
open(raw_path, "w", encoding="utf-8").write(response if response.endswith("\n") else response + "\n")
PY
  if [ "$parse_ec" -ne 0 ]; then
    reason="$(cat "$parse_reason" 2>/dev/null || true)"
    rm -f "$parse_reason"
    [ -n "$reason" ] || reason="no result event"
    argon_skip "$reason"
    return 0
  fi
  rm -f "$parse_reason"
  # Review reports follow the reviewer brief, not the design headings.
  if [ "${KIND:-}" = "review" ]; then
    cp "$raw" "$out"
    printf 'argon: ok model=%s\n' "$model" >"$DIR/voter-argon.model.txt"
    return 0
  fi
  if ! python3 - "$raw" "$out" <<'PY'
import re, sys
raw_path, out_path = sys.argv[1], sys.argv[2]
text = open(raw_path, encoding="utf-8", errors="replace").read()
text = re.sub(r'(?m)(?<!^)(?<![\n])(## (?:Findings|Approach|Would not do|Uncertainties)\b)', r'\n\1', text)
m = re.search(r'(?m)^## Findings\b', text)
if not m:
    sys.exit(2)
body = text[m.start():].strip() + "\n"
for h in ["## Findings", "## Approach", "## Would not do", "## Uncertainties"]:
    if not re.search(rf'(?m)^{re.escape(h)}\b', body):
        sys.exit(2)
open(out_path, "w", encoding="utf-8").write(body)
PY
  then
    argon_skip "argon: output missing required schema headings"
    return 0
  fi
  printf 'argon: ok model=%s\n' "$model" >"$DIR/voter-argon.model.txt"
  if ! mark_voter_ok argon; then
    rm -f "$DIR/voter-argon.model.txt"
    argon_write_skipped "argon: output missing required schema headings"
    return 0
  fi
  return 0
}

run_claude() {
  if [ "$CLAUDE_SKU" = cursor-fable ]; then
    run_cursor_fable_packet
    return
  fi
  local out="$DIR/voter-claude.md"
  local err="$DIR/voter-claude.err"
  local prompt ec
  # Packet-first: cwd=packet, no tools. Prompt is the packet.
  prompt="$(cat "$PACKET/PROMPT.md")"

  ec=0
  (
    cd -- "$PACKET" || exit 97
    printf '%s\n' "$prompt" | claude -p --tools "" --permission-prompts none \
      --model "$CLAUDE_MODEL_ALIAS" --effort "$CLAUDE_EFFORT"
  ) >"$out" 2>"$err" || ec=$?
  if [ "$ec" -ne 0 ]; then
    log_err "claude: invocation failed (exit $ec); see voter-claude.err"
    rm -f "$out"
    return 1
  fi
  if [ ! -s "$out" ]; then
    log_err "claude: empty output"
    rm -f "$out"
    return 1
  fi
  mark_voter_ok claude
}

run_cursor_fable_packet() {
  # Design and mixed stay packet-first. Ask mode is read-only. The workspace
  # is the packet, not the target repo. Review keeps its own worktree launcher.
  local out="$DIR/voter-claude.md" err="$DIR/voter-claude.err"
  local stream="$DIR/voter-claude.stream.jsonl" ec=0
  if [ -z "${CURSOR_FABLE:-}" ] || [ ! -s "$PACKET/PROMPT.md" ]; then
    log_err "claude: Cursor Fable id is unresolved: approve a downgraded leg"
    rm -f "$out"
    return 1
  fi
  (
    cd -- "$PACKET" || exit 97
    "$(conclave_env CURSOR_AGENT cursor-agent)" -p --mode ask \
      --model "$CURSOR_FABLE" \
      --output-format stream-json \
      --trust --workspace "$PACKET" \
      "$(cat "$PACKET/PROMPT.md")"
  ) >"$stream" 2>"$err" || ec=$?
  if [ "$ec" -ne 0 ]; then
    log_err "claude: Cursor exit $ec; see voter-claude.err"
    rm -f "$out"
    return 1
  fi
  if ! python3 - "$DIR" "$CURSOR_FABLE" "$out" <<'PY'
import json, re, sys
from pathlib import Path
d, model, out_path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
lines = (d / "voter-claude.stream.jsonl").read_text(encoding="utf-8").split("\n")
if lines and lines[-1] == "":
    lines.pop()
events = [json.loads(line) for line in lines if line]
last = events[-1]
if (last.get("type") != "result" or last.get("is_error") is not False
        or not isinstance(last.get("result"), str) or not last["result"].strip()):
    sys.stderr.write("claude: Cursor result missing\n")
    raise SystemExit(1)
init = next((e for e in events if e.get("type") == "system"), {})
display = str(init.get("model") or "unknown")
shown = re.sub(r"\s", "_", display)
ok = "fable" in display.lower()
proof = (f"claude: ok via=cursor model={model} display={shown}" if ok else
         f"claude: FAIL via=cursor ran={shown} (lineup said {model})")
(d / "voter-claude.model.txt").write_text(proof + "\n", encoding="utf-8")
if not ok:
    sys.stderr.write("claude: Cursor init model has no fable proof\n")
    raise SystemExit(1)
out_path.write_text(last["result"].strip() + "\n", encoding="utf-8")
PY
  then
    log_err "claude: Cursor Fable output missing or model unproven; see voter-claude.stream.jsonl"
    rm -f "$out"
    return 1
  fi
  mark_voter_ok claude
}

run_codex() {
  local out="$DIR/voter-codex.md"
  local err="$DIR/voter-codex.err"
  local ec
  local -a args
  # Packet-first: absolute paths; cwd is out-of-repo packet (not git root).
  [ -n "$CODEX_MODEL" ] || die "codex model unresolved"
  args=(exec --sandbox read-only --ephemeral --skip-git-repo-check --color never
    -C "$PACKET"
    -c "model_reasoning_effort=\"$CODEX_EFFORT\"" -o "$out"
    -m "$CODEX_MODEL"
    -)

  ec=0
  "$CODEX" "${args[@]}" <"$PACKET/PROMPT.md" >"$err" 2>&1 || ec=$?
  if [ "$ec" -ne 0 ]; then
    log_err "codex: invocation failed (exit $ec); see voter-codex.err"
    rm -f "$out"
    return 1
  fi
  if [ ! -s "$out" ]; then
    log_err "codex: empty output"
    rm -f "$out"
    return 1
  fi
  mark_voter_ok codex
}

run_grok() {
  local out="$DIR/voter-grok.md"
  local err="$DIR/voter-grok.err"
  local prompt_file="$PACKET/PROMPT.md"
  local raw="$DIR/voter-grok.raw.md"
  local ec

  if [ -z "${GROK_NATIVE_MODEL:-}" ]; then
    log_err "grok: GROK_NATIVE_MODEL is empty; refusing to launch"
    return 1
  fi

  # Packet-first: absolute cwd + prompt-file; no tools.
  ec=0
  grok --cwd "$PACKET" --prompt-file "$prompt_file" --tools "" \
    --always-approve --output-format plain --model "$GROK_NATIVE_MODEL" \
    --reasoning-effort "$GROK_EFFORT" --no-subagents --disable-web-search \
    --max-turns 8 \
    >"$raw" 2>"$err" || ec=$?
  if [ "$ec" -ne 0 ]; then
    log_err "grok: invocation failed (exit $ec); see voter-grok.err"
    rm -f "$out"
    return 1
  fi
  if [ ! -s "$raw" ]; then
    log_err "grok: empty output"
    return 1
  fi

  if ! python3 - "$raw" "$out" <<'PY'
import re, sys
raw_path, out_path = sys.argv[1], sys.argv[2]
text = open(raw_path, encoding="utf-8").read()
text = re.sub(r'(?m)(?<!^)(?<![\n])(## (?:Findings|Approach|Would not do|Uncertainties)\b)', r'\n\1', text)
m = re.search(r'(?m)^## Findings\b', text)
if not m:
    sys.stderr.write("grok: no ## Findings heading after normalize\n")
    open(out_path, "w", encoding="utf-8").write(text)
    sys.exit(2)
body = text[m.start():].strip() + "\n"
for h in ["## Findings", "## Approach", "## Would not do", "## Uncertainties"]:
    if not re.search(rf'(?m)^{re.escape(h)}\b', body):
        sys.stderr.write(f"grok: missing heading {h}\n")
        open(out_path, "w", encoding="utf-8").write(body)
        sys.exit(2)
open(out_path, "w", encoding="utf-8").write(body)
PY
  then
    log_err "grok: output missing required schema headings; see voter-grok.raw.md / voter-grok.md"
    rm -f "$out"
    return 1
  fi
  mark_voter_ok grok
}

load_jev_api_key() {
  if [ -n "${COUNCIL_SAVED_TYPESAFE:-}" ]; then
    printf '%s' "$COUNCIL_SAVED_TYPESAFE" | tr -d '\r'
    return 0
  fi
  if [ -n "${COUNCIL_SAVED_JEV:-}" ]; then
    printf '%s' "$COUNCIL_SAVED_JEV" | tr -d '\r'
    return 0
  fi
  local ref bin out jev_op
  # CONCLAVE_JEV_OP_REF is the 1Password reference, with no default.
  # COUNCIL_JEV_OP_REF is the deprecated name. The new name wins when both
  # are set. Unset means no key and no Jev Decision.
  ref="$(conclave_env JEV_OP_REF)"
  [ -n "$ref" ] || return 1
  # CONCLAVE_JEV_OP pins the binary (COUNCIL_JEV_OP still works).
  # Unset: try op, then op.exe when that read fails.
  jev_op="$(conclave_env JEV_OP)"
  local -a bins
  if [ -n "$jev_op" ]; then
    bins=("$jev_op")
  else
    bins=(op)
  fi
  for bin in "${bins[@]}"; do
    if out="$("$bin" read "$ref" 2>/dev/null)"; then
      out="$(printf '%s' "$out" | tr -d '\r')"
      if [ -n "$out" ]; then
        printf '%s' "$out"
        return 0
      fi
    fi
  done
  if [ -z "$jev_op" ] && command -v op.exe >/dev/null 2>&1; then
    if out="$(op.exe read "$ref" 2>/dev/null)"; then
      out="$(printf '%s' "$out" | tr -d '\r')"
      if [ -n "$out" ]; then
        printf '%s' "$out"
        return 0
      fi
    fi
  fi
  return 1
}

council_write_header_json() {
  # Caller sets HDR_BRANCH HDR_PR HDR_VA HDR_VB HDR_BUILDER.
  # KIND is the ballot kind; phase 3 and --judge read it back from this file.
  # HDR_HAS_C=1 adds voter_c_model from HDR_VC (opt-in Grok voter).
  # HDR_HAS_OPUS=1 adds coverage_opus_model. That key does not replace A/B/C.
  # HDR_VB_REASONING and HDR_VB_EFFORT sit next to voter_b_model. Unset is unknown.
  # HDR_*_SECONDS are leg wall times. Review and audit only. Unset is unknown.
  # HDR_STACK is the review stack line. Unset on other kinds, so they omit it.
  # HDR_HEAD_OID is the commit review kind reviewed. Same rule.
  # A named muse voter adds muse_model from voter-muse.model.txt. Codex off the
  # ballot writes voter_b_model none instead of an empty Astra slot.
  HDR_KIND="${KIND:-}"
  HDR_VC="${HDR_VC:-}"
  HDR_HAS_C="${HDR_HAS_C:-0}"
  HDR_VOPUS="${HDR_VOPUS:-}"
  HDR_HAS_OPUS="${HDR_HAS_OPUS:-0}"
  HDR_VB_REASONING="${HDR_VB_REASONING:-}"
  HDR_VB_EFFORT="${HDR_VB_EFFORT:-}"
  HDR_STACK="${HDR_STACK:-}"
  HDR_HEAD_OID="${HDR_HEAD_OID:-}"
  HDR_VA_SECONDS="${HDR_VA_SECONDS:-}" HDR_VB_SECONDS="${HDR_VB_SECONDS:-}"
  HDR_VC_SECONDS="${HDR_VC_SECONDS:-}" HDR_VOPUS_SECONDS="${HDR_VOPUS_SECONDS:-}"
  export HDR_VA_SECONDS HDR_VB_SECONDS HDR_VC_SECONDS HDR_VOPUS_SECONDS
  HDR_MISSING_VOTERS="${MISSING_VOTERS:-}"
  export HDR_KIND HDR_BRANCH HDR_PR HDR_VA HDR_VB HDR_BUILDER HDR_HAS_C HDR_VC
  export HDR_HAS_OPUS HDR_VOPUS HDR_VB_REASONING HDR_VB_EFFORT HDR_STACK
  export HDR_HEAD_OID HDR_MISSING_VOTERS
  HDR_DEST="$DIR/header.json"
  HDR_VOTERS="${VOTERS:-}" HDR_DIR="$DIR"
  export HDR_DEST HDR_VOTERS HDR_DIR
  python3 - <<'PY'
import json, os
from pathlib import Path
meta = {
    "kind": (os.environ.get("HDR_KIND") or "").strip(),
    "branch": os.environ.get("HDR_BRANCH") or "none",
    "pr": os.environ.get("HDR_PR") or "none",
    "voter_a_model": os.environ.get("HDR_VA") or "unknown",
    "voter_b_model": os.environ.get("HDR_VB") or "unknown",
    "voter_b_reasoning_tokens": (os.environ.get("HDR_VB_REASONING") or "").strip() or "unknown",
    "voter_b_effort": (os.environ.get("HDR_VB_EFFORT") or "").strip() or "unknown",
    "builder_model": os.environ.get("HDR_BUILDER") or "unknown",
}
stack = (os.environ.get("HDR_STACK") or "").strip()
if meta["kind"] == "review":
    meta["stack"] = stack or "unknown"
    meta["head_oid"] = (os.environ.get("HDR_HEAD_OID") or "").strip() or "unknown"
timed = meta["kind"] in ("review", "audit")
def secs(env):
    return (os.environ.get(env) or "").strip() or "unknown"
if timed:
    meta["voter_a_seconds"] = secs("HDR_VA_SECONDS")
    meta["voter_b_seconds"] = secs("HDR_VB_SECONDS")
if os.environ.get("HDR_HAS_C") == "1":
    meta["voter_c_model"] = os.environ.get("HDR_VC") or "unproven"
    if timed:
        meta["voter_c_seconds"] = secs("HDR_VC_SECONDS")
if os.environ.get("HDR_HAS_OPUS") == "1":
    meta["coverage_opus_model"] = os.environ.get("HDR_VOPUS") or "unproven"
    if timed:
        meta["coverage_opus_seconds"] = secs("HDR_VOPUS_SECONDS")
named = [v.strip() for v in (os.environ.get("HDR_VOTERS") or "").split(",") if v.strip()]
if named and "codex" not in named:
    meta["voter_b_model"] = "none"
    meta["voter_b_reasoning_tokens"] = "none"
    meta["voter_b_effort"] = "none"
    if timed:
        meta["voter_b_seconds"] = "none"
if "muse" in named:
    d = Path(os.environ.get("HDR_DIR") or ".")
    proof = d / "voter-muse.model.txt"
    text = proof.read_text(encoding="utf-8").strip() if proof.is_file() else ""
    meta["muse_model"] = text.split("model=", 1)[1].split()[0] if text.startswith("muse: ok model=") else "unproven"
    if timed:
        sec = d / "voter-muse.seconds"
        meta["muse_seconds"] = (sec.read_text(encoding="utf-8").strip() if sec.is_file() else "") or "unknown"
if "argon" in named:
    d = Path(os.environ.get("HDR_DIR") or ".")
    skipped = d / "voter-argon.skipped"
    if skipped.is_file():
        lines = []
        for ln in skipped.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if ln and ln != "argon: skipped":
                lines.append(ln)
        meta["argon_skipped"] = lines[0] if lines else "argon: skipped"
    else:
        proof = d / "voter-argon.model.txt"
        text = proof.read_text(encoding="utf-8").strip() if proof.is_file() else ""
        meta["argon_model"] = text.split("model=", 1)[1].split()[0] if text.startswith("argon: ok model=") else "unproven"
for key, env in (
    ("target", "HDR_AUDIT_TARGET"),
    ("target_type", "HDR_AUDIT_TYPE"),
    ("snapshot_sha256", "HDR_AUDIT_SHA"),
    ("target_drift", "HDR_AUDIT_DRIFT"),
    ("redaction_kept", "HDR_AUDIT_KEPT"),
    ("gate", "HDR_AUDIT_GATE"),
):
    val = os.environ.get(env, "")
    if val:
        meta[key] = val
missing = (os.environ.get("HDR_MISSING_VOTERS") or "").strip()
if missing:
    meta["missing_voters"] = missing
Path(os.environ["HDR_DEST"]).write_text(
    json.dumps(meta, indent=2) + "\n", encoding="utf-8"
)
PY
}

run_jev_judge() {
  local key
  key="$(load_jev_api_key || true)"
  if [ -z "$key" ]; then
    log_err "jev: no API key (saved env empty and op read failed); skipping Jev judge"
    return 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    log_err "jev: python3 not found; skipping Jev judge"
    return 1
  fi
  if ! env -u JEV_API_KEY TYPESAFE_API_KEY="$key" \
    python3 "$ROOT/scripts/jev-judge.py" "$ID" "$DIR" >"$DIR/jev-judge.log" 2>&1; then
    log_err "jev: judge failed; see jev-judge.log"
    return 1
  fi
  cat "$DIR/jev-judge.log" >&2 || true
  [ -s "$DIR/judgment.md" ]
}

run_findings_mode() {
  # A failed rerun must not leave the previous gate's judgment in place.
  rm -f "$DIR/judgment.md" "$DIR/convergence.md"
  local key
  local -a cmd
  cmd=(python3 "$ROOT/scripts/jev-judge.py" --findings "$DIR")
  # Zero findings is a clean review. Do not call Jev and do not read the key.
  if python3 - "$DIR/findings.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
findings = doc.get("findings") if isinstance(doc, dict) else None
raise SystemExit(0 if isinstance(findings, list) and len(findings) == 0 else 1)
PY
  then
    "${cmd[@]}"
    return
  fi
  if [ -n "$CANNED_JEV" ]; then
    cmd+=(--canned "$CANNED_JEV")
    "${cmd[@]}"
    return
  fi
  key="$(load_jev_api_key || true)"
  if [ -z "$key" ]; then
    die "jev: no API key (saved env empty and op read failed)"
  fi
  env -u JEV_API_KEY TYPESAFE_API_KEY="$key" "${cmd[@]}"
}

# Outcome record. One line per phase in runs.jsonl, joined by run_id.
# The review part does not exist when the run ends, so a single line cannot
# hold all three parts without a later rewrite. The default ballot calls
# seat-usage --route lineup. The record still leaves lineup, host, usage,
# and chair reuse unknown unless the control plane passed them. Pool draw
# and builder rounds stay unknown.
# The record stores the effort each launch actually passes. Review and audit
# hardcode high for some legs. Design and mixed pass the resolved efforts.
# This script does not change the launches.
council_voter_started() {
  case "$1" in
    claude|opus)
      command -v claude >/dev/null 2>&1
      ;;
    codex)
      if [ -n "${CODEX:-}" ]; then
        [ -x "$CODEX" ]
      else
        command -v codex >/dev/null 2>&1
      fi
      ;;
    grok)
      command -v grok >/dev/null 2>&1
      ;;
    muse)
      command -v muse >/dev/null 2>&1
      ;;
    argon)
      [ -x "$(argon_agy_bin)" ]
      ;;
    *)
      return 1
      ;;
  esac
}

council_started_voters() {
  local name out=""
  local IFS=','
  for name in $1; do
    name="$(printf '%s' "$name" | tr -d '[:space:]')"
    [ -n "$name" ] || continue
    if council_voter_started "$name"; then
      out="${out:+$out,}$name"
    fi
  done
  printf '%s\n' "$out"
}

council_launch_efforts() {
  # Efforts the launches pass, by kind. Not the resolved routing variables.
  RAN_CLAUDE_EFFORT="${CLAUDE_EFFORT:-}"
  RAN_CODEX_EFFORT="${CODEX_EFFORT:-}"
  RAN_GROK_EFFORT="${GROK_EFFORT:-}"
  RAN_MUSE_EFFORT="${MUSE_EFFORT:-}"
  RAN_ARGON_EFFORT="${ARGON_EFFORT:-}"
  RAN_OPUS_EFFORT=""
  case "${KIND:-}" in
    review)
      # Fable on the brief and Opus native both pass --effort high.
      RAN_CLAUDE_EFFORT="high"
      RAN_GROK_EFFORT="high"
      RAN_MUSE_EFFORT="high"
      RAN_CODEX_EFFORT="${CODEX_EFFORT:-}"
      RAN_OPUS_EFFORT="high"
      ;;
    audit)
      RAN_CLAUDE_EFFORT="high"
      RAN_CODEX_EFFORT="high"
      RAN_GROK_EFFORT="high"
      RAN_MUSE_EFFORT="high"
      RAN_OPUS_EFFORT="high"
      ;;
  esac
}
council_runs_path() {
  if [ -n "${SEAT_USAGE_RUNS:-}" ]; then
    printf '%s\n' "$SEAT_USAGE_RUNS"
  else
    printf '%s\n' "${HOME}/.local/state/seat-usage/runs.jsonl"
  fi
}

council_record_outcome() {
  # Subshells inherit this trap, including background voter legs. Only the
  # parent of a real ballot appends. RUN_OUTCOME is not exported.
  [ "${BASH_SUBSHELL:-0}" -eq 0 ] || return 0
  [ "${RUN_OUTCOME:-0}" = 1 ] || return 0
  local phase="${1:-}"
  local ec="${2:-}"
  case "$phase" in
    before)
      [ "${OUTCOME_BEFORE_DONE:-0}" = 1 ] && return 0
      OUTCOME_BEFORE_DONE=1
      ;;
    after)
      [ "${OUTCOME_AFTER_DONE:-0}" = 1 ] && return 0
      OUTCOME_AFTER_DONE=1
      ;;
    *)
      return 0
      ;;
  esac
  local status="" path
  if [ "$phase" = "after" ]; then
    if [ "$ec" = "0" ]; then
      status="completed"
    else
      status="failed"
    fi
  fi
  local started muse_decision="" final_decision=""
  local -a muse_args=()
  started="$(council_started_voters "${VOTERS:-}")"
  council_launch_efforts
  path="$(council_runs_path)"
  if [ "$phase" = "after" ] && [ -n "${MUSE_SHADOW_STATUS:-}" ]; then
    if [ "${MUSE_SHADOW_STATUS}" = "ran" ] && [ -n "${DIR:-}" ] && [ -s "$DIR/voter-muse.md" ]; then
      muse_decision="$(muse_extract_decision "$DIR/voter-muse.md" || true)"
    fi
    if [ -n "${DIR:-}" ] && [ -s "$DIR/judgment.md" ]; then
      final_decision="$(grep -E '^(implement|implement with changes|no automatic decision)$' "$DIR/judgment.md" | head -n 1 || true)"
    fi
    muse_args=(
      --muse-status "$MUSE_SHADOW_STATUS"
      --muse-model "${MUSE_WONDER_MODEL:-}"
      --muse-decision "$muse_decision"
      --final-decision "$final_decision"
    )
  fi
  python3 "$ROOT/scripts/record-run.py" \
    --phase "$phase" \
    --path "$path" \
    --run-id "${RUN_ID:-}" \
    --task-id "${ID:-}" \
    --kind "${KIND:-}" \
    --difficulty "${DIFFICULTY:-}" \
    --started "${RUN_STARTED_TS:-}" \
    --status "$status" \
    --chair-reused "$(conclave_env CHAIR_REUSED)" \
    --lineup-file "$(conclave_env LINEUP_JSON)" \
    --host-file "$(conclave_env HOST_JSON)" \
    --voters "$started" \
    --ran-claude-model "${CLAUDE_MODEL_ALIAS:-}" \
    --ran-claude-effort "${RAN_CLAUDE_EFFORT:-}" \
    --ran-codex-model "${CODEX_MODEL:-}" \
    --ran-codex-effort "${RAN_CODEX_EFFORT:-}" \
    --ran-grok-model "${GROK_NATIVE_MODEL:-}" \
    --ran-grok-effort "${RAN_GROK_EFFORT:-}" \
    --ran-opus-model "opus" \
    --ran-opus-effort "${RAN_OPUS_EFFORT:-}" \
    --ran-muse-model "${MUSE_WONDER_MODEL:-}" \
    --ran-muse-effort "${RAN_MUSE_EFFORT:-}" \
    --ran-argon-model "$(argon_model_id)" \
    --ran-argon-effort "${RAN_ARGON_EFFORT:-}" \
    "${muse_args[@]}" \
    || true
}

council_begin_run() {
  # Real ballot only. Dry-run, --findings, --verdicts, and --judge return
  # before this is called. A sourced test never calls it.
  # A second call in this process no-ops once this run has an id. An inherited
  # RUN_OUTCOME with an empty id is not this run, so it still records.
  [ "${BASH_SUBSHELL:-0}" -eq 0 ] || return 0
  [ "${DRY_RUN:-0}" = 1 ] && return 0
  if [ "${RUN_OUTCOME:-0}" = 1 ] && [ -n "${RUN_ID:-}" ]; then
    return 0
  fi
  RUN_OUTCOME=1
  OUTCOME_BEFORE_DONE=0
  OUTCOME_AFTER_DONE=0
  RUN_STARTED_TS="$(date +%s)"
  RUN_ID="${ID:-unknown}-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
  printf 'run_id: %s\n' "$RUN_ID"
  council_record_outcome before
}

# Review is a later call. It does not require RUN_OUTCOME, and a bad file
# writes nothing. The exit status is the writer's.
council_record_review() {
  [ "${BASH_SUBSHELL:-0}" -eq 0 ] || return 0
  local review_file="${1:-}" path
  path="$(council_runs_path)"
  python3 "$ROOT/scripts/record-run.py" \
    --phase review \
    --path "$path" \
    --run-id "${RUN_ID:-}" \
    --task-id "${ID:-}" \
    --kind "${KIND:-}" \
    --difficulty "${DIFFICULTY:-}" \
    --review-file "$review_file"
}

# --- args ---
# Tests source this file to call brief_body. Honor the flag only then.
# An exported variable must not turn a real run into a silent exit 0.
if [ "$(conclave_env SOURCE_ONLY 0)" = "1" ] && [ "${BASH_SOURCE[0]}" != "$0" ]; then
  return 0 2>/dev/null || exit 0
fi

if [ "${1:-}" = "--record-review" ]; then
  shift
  rec_run_id=""
  rec_review=""
  rec_task=""
  rec_kind=""
  rec_difficulty=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --run-id)
        [ "$#" -ge 2 ] || die "--run-id requires a value"
        rec_run_id="$2"
        shift 2
        ;;
      --review-file)
        [ "$#" -ge 2 ] || die "--review-file requires a value"
        rec_review="$2"
        shift 2
        ;;
      --task-id)
        [ "$#" -ge 2 ] || die "--task-id requires a value"
        rec_task="$2"
        shift 2
        ;;
      --kind)
        [ "$#" -ge 2 ] || die "--kind requires a value"
        rec_kind="$2"
        shift 2
        ;;
      --difficulty)
        [ "$#" -ge 2 ] || die "--difficulty requires a value"
        rec_difficulty="$2"
        shift 2
        ;;
      *)
        die "unknown argument: $1"
        ;;
    esac
  done
  [ -n "$rec_run_id" ] || die "run-id is required"
  RUN_ID="$rec_run_id"
  ID="$rec_task"
  KIND="$rec_kind"
  DIFFICULTY="$rec_difficulty"
  council_record_review "$rec_review"
  exit $?
fi

if [ "$#" -lt 1 ]; then
  usage >&2
  exit 1
fi

case "$1" in
  help|-h|--help)
    usage
    exit 0
    ;;
esac

# Pre-run freshness check (also on --dry-run). See scripts/freshness-check.sh.
# shellcheck disable=SC1091
. "$ROOT/scripts/freshness-check.sh"
council_freshness_check "$ROOT" || exit 1

ID="$1"
shift

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --max)
      FORCE_MAX=1
      shift
      ;;
    --voters)
      [ "$#" -ge 2 ] || die "--voters requires a comma-separated list"
      VOTERS="$2"
      VOTERS_EXPLICIT=1
      shift 2
      ;;
    --voters=*)
      VOTERS="${1#--voters=}"
      VOTERS_EXPLICIT=1
      shift
      ;;
    --claude-model)
      [ "$#" -ge 2 ] || die "--claude-model requires fable|opus|cursor-fable"
      FLAG_CLAUDE="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
      shift 2
      ;;
    --claude-model=*)
      FLAG_CLAUDE="$(printf '%s' "${1#--claude-model=}" | tr '[:upper:]' '[:lower:]')"
      shift
      ;;
    --fable-overflow)
      [ "$#" -ge 2 ] && [ "$2" = cursor ] || die "--fable-overflow requires cursor"
      FABLE_OVERFLOW=cursor
      shift 2
      ;;
    --fable-overflow=*)
      [ "${1#*=}" = cursor ] || die "--fable-overflow requires cursor"
      FABLE_OVERFLOW=cursor
      shift
      ;;
    --kind)
      [ "$#" -ge 2 ] || die "--kind requires design|review|mixed|audit"
      FLAG_KIND="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
      shift 2
      ;;
    --kind=*)
      FLAG_KIND="$(printf '%s' "${1#--kind=}" | tr '[:upper:]' '[:lower:]')"
      shift
      ;;
    --difficulty)
      [ "$#" -ge 2 ] || die "--difficulty requires low|med|high"
      FLAG_DIFFICULTY="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
      shift 2
      ;;
    --difficulty=*)
      FLAG_DIFFICULTY="$(printf '%s' "${1#--difficulty=}" | tr '[:upper:]' '[:lower:]')"
      shift
      ;;
    --repo)
      [ "$#" -ge 2 ] || die "--repo requires a directory"
      FLAG_REPO="$2"
      shift 2
      ;;
    --repo=*)
      FLAG_REPO="${1#--repo=}"
      shift
      ;;
    --pr)
      [ "$#" -ge 2 ] || die "--pr requires a number"
      FLAG_PR="${2#\#}"
      case "$FLAG_PR" in
        ''|*[!0-9]*) die "--pr wants a number (optional leading #)" ;;
      esac
      shift 2
      ;;
    --pr=*)
      FLAG_PR="${1#--pr=}"
      FLAG_PR="${FLAG_PR#\#}"
      case "$FLAG_PR" in
        ''|*[!0-9]*) die "--pr wants a number (optional leading #)" ;;
      esac
      shift
      ;;
    --base)
      [ "$#" -ge 2 ] || die "--base requires a ref"
      FLAG_BASE="$2"
      [ -n "$FLAG_BASE" ] || die "--base requires a ref"
      shift 2
      ;;
    --base=*)
      FLAG_BASE="${1#--base=}"
      [ -n "$FLAG_BASE" ] || die "--base requires a ref"
      shift
      ;;
    --head)
      [ "$#" -ge 2 ] || die "--head requires a ref"
      FLAG_HEAD="$2"
      [ -n "$FLAG_HEAD" ] || die "--head requires a ref"
      shift 2
      ;;
    --head=*)
      FLAG_HEAD="${1#--head=}"
      [ -n "$FLAG_HEAD" ] || die "--head requires a ref"
      shift
      ;;
    --whole)
      FLAG_WHOLE=1
      shift
      ;;
    --keep-worktrees)
      KEEP_WORKTREES=1
      shift
      ;;
    --findings)
      FINDINGS_MODE=1
      shift
      ;;
    --verdicts)
      VERDICTS_MODE=1
      shift
      ;;
    --target)
      [ "$#" -ge 2 ] || die "--target requires a url or file"
      FLAG_TARGET="$2"
      [ -n "$FLAG_TARGET" ] || die "--target requires a url or file"
      shift 2
      ;;
    --target=*)
      FLAG_TARGET="${1#--target=}"
      [ -n "$FLAG_TARGET" ] || die "--target requires a url or file"
      shift
      ;;
    --target-type)
      [ "$#" -ge 2 ] || die "--target-type requires content|claims|proposal"
      FLAG_TARGET_TYPE="$(printf '%s' "$2" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
      shift 2
      ;;
    --target-type=*)
      FLAG_TARGET_TYPE="$(printf '%s' "${1#--target-type=}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
      shift
      ;;
    --context)
      [ "$#" -ge 2 ] || die "--context requires a file"
      CONTEXT_FILES+=("$2")
      shift 2
      ;;
    --context=*)
      CONTEXT_FILES+=("${1#--context=}")
      shift
      ;;
    --attach)
      [ "$#" -ge 2 ] || die "--attach requires a file"
      [ -n "$2" ] || die "--attach requires a file"
      ATTACH_FILES+=("$2")
      shift 2
      ;;
    --attach=*)
      ATTACH_FILES+=("${1#--attach=}")
      [ -n "${ATTACH_FILES[-1]}" ] || die "--attach requires a file"
      shift
      ;;
    --judge)
      JUDGE_MODE=1
      shift
      ;;
    --ask)
      [ "$#" -ge 2 ] && [ -n "$2" ] || die "--ask requires a question"
      ASK_MODE=1
      ASK_TEXT="$2"
      shift 2
      ;;
    --ask=*)
      ASK_MODE=1
      ASK_TEXT="${1#--ask=}"
      [ -n "$ASK_TEXT" ] || die "--ask requires a question"
      shift
      ;;
    --ask-file)
      [ "$#" -ge 2 ] && [ -n "$2" ] || die "--ask-file requires a file"
      ASK_MODE=1
      ASK_FILE="$2"
      shift 2
      ;;
    --template)
      [ "$#" -ge 2 ] || die "--template requires a name"
      ASK_MODE=1
      ASK_TEMPLATE="$2"
      shift 2
      ;;
    --input)
      [ "$#" -ge 2 ] && [ -n "$2" ] || die "--input requires a file"
      ASK_INPUTS+=("$2")
      shift 2
      ;;
    --effort)
      [ "$#" -ge 2 ] || die "--effort requires low|med|high"
      FLAG_EFFORT="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
      shift 2
      ;;
    --canned)
      [ "$#" -ge 2 ] || die "--canned requires a JSON file"
      CANNED_JEV="$2"
      shift 2
      ;;
    --canned=*)
      CANNED_JEV="${1#--canned=}"
      shift
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

case "$ID" in
  ''|*/*|*..*|*" "*)
    die "invalid id: use a simple token (e.g. smoke, auth-v1)"
    ;;
esac

if [ "$ASK_MODE" -eq 1 ]; then
  ask_main
  exit 0
fi
if [ "${#ASK_INPUTS[@]}" -gt 0 ] || [ -n "$FLAG_EFFORT" ]; then
  die "--input and --effort are ask mode only (--ask, --ask-file, or --template)"
fi

[ -f "$ROOT/tmp/council/$ID/brief.md" ] || die "missing brief: tmp/council/$ID/brief.md (write it first)"

DIR="$(CDPATH= cd -- "$ROOT/tmp/council/$ID" && pwd)"
BRIEF="$DIR/brief.md"
ERRORS_FILE="$DIR/errors.md"
BRIEF="$(CDPATH= cd -- "$(dirname "$BRIEF")" && pwd)/$(basename "$BRIEF")"

if [ -n "$CANNED_JEV" ] && [ "$FINDINGS_MODE" -eq 0 ] && [ "$VERDICTS_MODE" -eq 0 ]; then
  die "--canned requires --findings or --verdicts"
fi
if [ "$FINDINGS_MODE" -eq 1 ] && [ "$JUDGE_MODE" -eq 1 ]; then
  die "--judge cannot be combined with --findings"
fi
if [ "$FINDINGS_MODE" -eq 1 ] && [ "$VERDICTS_MODE" -eq 1 ]; then
  die "--verdicts cannot be combined with --findings"
fi
if [ "$VERDICTS_MODE" -eq 1 ] && [ "$JUDGE_MODE" -eq 1 ]; then
  die "--verdicts cannot be combined with --judge"
fi

# Capture before any voter and before --findings / --judge. Voters never see the key.
COUNCIL_SAVED_TYPESAFE="${TYPESAFE_API_KEY:-}"
COUNCIL_SAVED_JEV="${JEV_API_KEY:-}"
unset TYPESAFE_API_KEY JEV_API_KEY

if [ "$FINDINGS_MODE" -eq 1 ]; then
  [ "$DRY_RUN" -eq 0 ] || die "--findings is not a dry-run"
  run_findings_mode
  exit $?
fi
if [ "$VERDICTS_MODE" -eq 1 ]; then
  [ "$DRY_RUN" -eq 0 ] || die "--verdicts is not a dry-run"
  parse_brief_header
  if [ -n "$FLAG_KIND" ]; then
    KIND="$FLAG_KIND"
  elif [ -n "$HEADER_KIND" ]; then
    KIND="$HEADER_KIND"
  elif [ -f "$DIR/header.json" ]; then
    KIND="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1], encoding="utf-8")).get("kind") or "").strip())' "$DIR/header.json" 2>/dev/null || true)"
  fi
  if [ -z "${KIND:-}" ]; then
    die "kind inconclusive: set brief header 'kind: audit' or pass --kind. kind: audit is never inferred"
  fi
  [ "$KIND" = "audit" ] || die "--verdicts requires kind: audit"
  if [ -z "$FLAG_TARGET_TYPE" ] && [ -z "$HEADER_TARGET_TYPE" ] && [ -f "$DIR/header.json" ]; then
    HEADER_TARGET_TYPE="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1], encoding="utf-8")).get("target_type") or "").strip())' "$DIR/header.json" 2>/dev/null || true)"
  fi
  audit_verdicts_standalone
  exit $?
fi
if [ "$JUDGE_MODE" -eq 1 ]; then
  [ "$DRY_RUN" -eq 0 ] || die "--judge is not a dry-run"
  reject_review_only_flags
  judge_resolve_kind
  if [ "$KIND" = "review" ]; then
    die "--judge is for design and mixed; kind: review uses --findings"
  fi
  if [ "$KIND" = "audit" ]; then
    die "--judge is for design and mixed; kind: audit uses --findings or --verdicts"
  fi
  judge_load_voters
  publish_voter_opinions
  if ! ballot_quorum_ok; then
    exit 1
  fi
  record_missing_voters_header
  rm -f "$DIR/options.md"
  if run_jev_judge; then
    printf 'Jev wrote tmp/council/%s/judgment.md\n' "$ID"
    exit 0
  fi
  rm -f "$DIR/judgment.md" "$DIR/options.md"
  printf 'Jev has no key or the judge failed. No judgment.md.\n'
  printf 'Re-auth, then re-judge:\n'
  printf '  scripts/conclave.sh %s --judge\n' "$ID"
  exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
  # Peek only: write under preview/; never touch ballot routing/snapshot/packet/errors.
  reset_plain_preview_dir
  ROUTING_OUT="$DIR/preview/routing.md"
  SNAPSHOT_PATH="$DIR/preview/review-snapshot.md"
  PACKET_MIRROR="$DIR/preview/packet"
  resolve_routing
  require_design_voters
  audit_reject_foreign_flags
  if [ "$KIND" = "review" ]; then
    attach_resolve
    review_dry_run
    exit 0
  fi
  if [ "$KIND" = "audit" ]; then
    audit_dry_run
    exit 0
  fi
  reject_review_only_flags
  review_resolve_repo
  write_routing_md
  refresh_packet_preview
  printf 'Dry-run preview: tmp/council/%s/preview/ (ballot untouched)\n' "$ID"
  printf '  kind=%s difficulty=%s source=%s\n' "$KIND" "$DIFFICULTY" "$SOURCE"
  printf '  %s\n' "$(ballot_source_line)"
  if [ -n "${BALLOT_EXCLUDED_WHY:-}" ]; then
    printf '  %s\n' "$(excluded_line)"
  fi
  printf '  voters: %s\n' "$VOTERS"
  if [ -n "${BALLOT_DOWNGRADE_WHY:-}" ]; then
    printf '  downgrade: %s\n' "$BALLOT_DOWNGRADE_WHY"
  fi
  printf '  target=%s\n' "$REPO"
  printf '  claude=%s effort=%s | codex=%s effort=%s | %s effort=%s | judge=jev\n' \
    "$CLAUDE_MODEL_ALIAS" "$CLAUDE_EFFORT" \
    "$CODEX_MODEL" "$CODEX_EFFORT" \
    "$GROK_NATIVE_MODEL" "$GROK_EFFORT"
  if muse_shadow_on; then
    printf '  muse-shadow=on model=%s (non-voting, packet-first, tool-less)\n' \
      "$MUSE_WONDER_MODEL"
  else
    printf '  muse-shadow=off\n'
  fi
  argon_dry_status
  cat "$ROUTING_OUT"
  exit 0
fi

: >"$ERRORS_FILE"
ROUTING_OUT="$DIR/routing.md"
SNAPSHOT_PATH="$DIR/review-snapshot.md"
PACKET_MIRROR="$DIR/packet"

resolve_routing
require_design_voters
audit_reject_foreign_flags
if [ -n "${BALLOT_DOWNGRADE_WHY:-}" ]; then
  die "approve a downgraded leg: ${BALLOT_DOWNGRADE_WHY}"
fi
council_begin_run
if [ "$KIND" = "review" ]; then
  attach_resolve
  unset TYPESAFE_API_KEY JEV_API_KEY
  review_run
  exit $?
fi
if [ "$KIND" = "audit" ]; then
  unset TYPESAFE_API_KEY JEV_API_KEY
  audit_run
  exit $?
fi
reject_review_only_flags
review_resolve_repo
write_routing_md

printf 'Wrote tmp/council/%s/routing.md\n' "$ID"
printf '  kind=%s difficulty=%s source=%s\n' "$KIND" "$DIFFICULTY" "$SOURCE"
printf '  %s\n' "$(ballot_source_line)"
if [ -n "${BALLOT_EXCLUDED_WHY:-}" ]; then
  printf '  %s\n' "$(excluded_line)"
fi
printf '  voters: %s\n' "$VOTERS"
printf '  target=%s\n' "$REPO"
printf '  claude=%s effort=%s | codex=%s effort=%s | %s effort=%s | judge=jev\n' \
  "$CLAUDE_MODEL_ALIAS" "$CLAUDE_EFFORT" \
  "$CODEX_MODEL" "$CODEX_EFFORT" \
  "$GROK_NATIVE_MODEL" "$GROK_EFFORT"
if muse_shadow_on; then
  printf '  muse-shadow=on model=%s (non-voting, packet-first, tool-less)\n' \
    "$MUSE_WONDER_MODEL"
else
  printf '  muse-shadow=off\n'
fi

clear_ballot_artifacts
save_ballot_voters
# Keep routing-time log_err lines; do not truncate errors.md again here.
build_review_snapshot
prepare_packet_workspace
prepare_argon_leg

IFS=',' read -r -a VOTER_LIST <<EOF
$VOTERS
EOF

PIDS=""
for v in "${VOTER_LIST[@]}"; do
  v="$(printf '%s' "$v" | tr -d '[:space:]')"
  [ -n "$v" ] || continue
  case "$v" in
    claude)
      if [ "$CLAUDE_SKU" = cursor-fable ]; then
        if ! command -v "$(conclave_env CURSOR_AGENT cursor-agent)" >/dev/null 2>&1; then
          log_err "claude: cursor-agent not found"
          continue
        fi
      elif ! command -v claude >/dev/null 2>&1; then
        log_err "claude: CLI not found (command -v claude failed)"
        continue
      fi
      council_bg run_claude
      PIDS="$PIDS $!"
      ;;
    codex)
      if [ ! -x "$CODEX" ]; then
        log_err "codex: resolver binary is not executable: $CODEX"
        continue
      fi
      council_bg run_codex
      PIDS="$PIDS $!"
      ;;
    grok)
      if ! command -v grok >/dev/null 2>&1; then
        log_err "grok: CLI not found (command -v grok failed)"
        continue
      fi
      council_bg run_grok
      PIDS="$PIDS $!"
      ;;
    muse)
      if ! command -v muse >/dev/null 2>&1; then
        log_err "muse: CLI not found (command -v muse failed)"
        continue
      fi
      council_bg run_muse_voter
      PIDS="$PIDS $!"
      ;;
    argon)
      # Preflight already ran. A skip is not a missing ballot.
      [ "${ARGON_PREFLIGHT_OK:-0}" = 1 ] || continue
      council_bg run_argon
      PIDS="$PIDS $!"
      ;;
    *)
      log_err "unknown voter '$v' (supported: claude, codex, grok, muse, argon)"
      ;;
  esac
done

launch_muse_shadow

for pid in $PIDS; do
  wait "$pid" || true
done
muse_load_status

# Design and mixed: replace a failed leg once. Ask, or an explicit --voters
# list, leaves that leg missing. Two opinions from two vendors still judge.
review_reseat_failed
save_ballot_voters

publish_voter_opinions

if ! ballot_quorum_ok; then
  exit 1
fi

HDR_BRANCH="none"
if git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  HDR_BRANCH="$(council_branch_label "$REPO")"
fi
[ -n "$HDR_BRANCH" ] || HDR_BRANCH="none"
HDR_PR="none"
if voter_is_named claude; then
  HDR_VA="${CLAUDE_MODEL_ALIAS} (alias, not proven)"
else
  HDR_VA=""
fi
if voter_is_named codex; then
  HDR_VB="$CODEX_MODEL"
else
  HDR_VB=""
fi
HDR_BUILDER="${CURSOR_BUILDER:-unknown}"
HDR_HAS_C=0
HDR_VC=""
if voter_is_named grok; then
  HDR_HAS_C=1
  HDR_VC="${GROK_NATIVE_MODEL:-unproven}"
fi
HDR_HAS_OPUS=0
HDR_VOPUS=""
council_write_header_json

JEV_OK=0
if run_jev_judge; then
  JEV_OK=1
fi

if [ ! -s "$ERRORS_FILE" ]; then
  printf '(none)\n' >"$ERRORS_FILE"
fi

printf '\nConclave %s: %s valid opinion(s) written.\n' "$ID" "$OPINION_N"
printf 'Artifacts: tmp/council/%s/\n' "$ID"
printf '\nNext step:\n'
if [ "$JEV_OK" -eq 1 ]; then
  decision_line="$(grep -E '^(implement|implement with changes|no automatic decision)$' "$DIR/judgment.md" | head -1 || true)"
  printf '  Jev wrote tmp/council/%s/judgment.md\n' "$ID"
  printf '  Decision: %s\n' "${decision_line:-see judgment.md}"
  printf '  Today no kind builds without the human.\n'
  printf '  Design and mixed implement* go to the human with judgment.md until the calibration in docs/routing.md exists.\n'
  printf '  No automatic decision: the chair fills options.md; the human picks.\n'
else
  rm -f "$DIR/judgment.md" "$DIR/options.md"
  printf '  Jev has no key or the judge failed. No judgment.md.\n'
  printf '  Re-auth, then re-judge:\n'
    printf '    scripts/conclave.sh %s --judge\n' "$ID"
fi

exit 0
