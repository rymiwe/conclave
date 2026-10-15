#!/usr/bin/env bash
# Family and vendor words the reviewer brief and stack-run both reject.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() { printf 'FAIL %s\n' "$*" >&2; exit 1; }

hit() {
  local text="$1" want="$2" got got_lc want_lc
  got="$(review_forbidden_word "$text")"
  got_lc="$(printf '%s' "$got" | tr '[:upper:]' '[:lower:]')"
  want_lc="$(printf '%s' "$want" | tr '[:upper:]' '[:lower:]')"
  [ "$got_lc" = "$want_lc" ] || fail "want [$want] in [$text], got [$got]"
}

miss() {
  local text="$1" got
  got="$(review_forbidden_word "$text")"
  [ -z "$got" ] || fail "unexpected [$got] in [$text]"
}

hit "OpenAI said so" "OpenAI"
hit "the Muse leg cleared this" "Muse"
hit "Meta found nothing" "Meta"
hit "xAI engine" "xAI"
hit "Cursor reviewed this" "Cursor"
hit "GPT-6 found" "GPT-6"
hit "Sol and Terra" "Sol"
hit "the Grok CLI" "Grok"
hit "opus-5 shipped" "opus"
hit "Claude's notes" "Claude"
hit "See CLAUDE.md" "CLAUDE"
miss "astray from the path"
miss "octopus"
miss "affable"
miss "Gemini 3.1 Pro"
miss "metadata and solution and precursor and amused"
miss "GPT6 found"
miss "page with a cursor, a meta tag, the SOL date, and a muse"

python3 - "$ROOT/scripts/stack-run.py" <<'PY'
import importlib.util, sys
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("stack_run", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
hits = [
    "OpenAI said so", "the Muse leg cleared this", "Meta found nothing",
    "xAI engine", "Cursor reviewed this", "GPT-6 found", "Sol and Terra",
    "the Grok CLI", "opus-5 shipped", "Claude's notes", "See CLAUDE.md",
]
misses = [
    "astray from the path", "octopus", "affable", "Gemini 3.1 Pro",
    "metadata and solution and precursor and amused", "GPT6 found",
    "page with a cursor, a meta tag, the SOL date, and a muse",
]
for text in hits:
    if not mod.FORBIDDEN.search(text):
        raise SystemExit(f"stack-run missed {text!r}")
for text in misses:
    if mod.FORBIDDEN.search(text):
        raise SystemExit(f"stack-run flagged {text!r}")
print("ok stack-run forbidden list")
PY
printf 'ok family words\n'
