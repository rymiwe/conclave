#!/usr/bin/env bash
# One Argon question through the same preflight and run the ballot uses.
# No Jev and no other voter. Prints voter-argon.md, or the skipped reason,
# then the usage object from the final result event when the stream has one.
# Usage: scripts/argon-smoke.sh "<question>"
set -euo pipefail

question="${1:-}"
if [ -z "$question" ]; then
  printf '%s\n' 'usage: scripts/argon-smoke.sh "<question>"' >&2
  exit 2
fi

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export CONCLAVE_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/conclave.sh"
# SOURCE_ONLY returns before conclave.sh unsets the judge key.
unset TYPESAFE_API_KEY JEV_API_KEY COUNCIL_SAVED_TYPESAFE COUNCIL_SAVED_JEV

tmp="$(mktemp -d /tmp/argon-smoke.XXXXXX)"
DIR="$tmp/ballot"
PACKET="$tmp/packet"
mkdir -p "$DIR" "$PACKET"
ERRORS_FILE="$DIR/errors.md"
: >"$ERRORS_FILE"
KIND=design
ARGON_EFFORT="${ARGON_EFFORT:-${GROK_EFFORT:-low}}"
GROK_EFFORT="$ARGON_EFFORT"

cat >"$PACKET/PROMPT.md" <<EOF
${question}

Respond with ONLY these sections (markdown headings exactly):

## Findings
## Approach
## Would not do
## Uncertainties
EOF

if ! reason="$(argon_preflight)"; then
  argon_skip "$reason"
  cat "$DIR/voter-argon.skipped"
else
  run_argon
  if [ -s "$DIR/voter-argon.md" ]; then
    cat "$DIR/voter-argon.md"
  elif [ -s "$DIR/voter-argon.skipped" ]; then
    cat "$DIR/voter-argon.skipped"
  fi
fi

python3 - "$DIR/voter-argon.stream.jsonl" <<'PY'
import json, sys
path = sys.argv[1]
usage = None
try:
    lines = open(path, encoding="utf-8", errors="replace")
except OSError:
    lines = []
for line in lines:
    line = line.strip()
    if not line:
        continue
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        continue
    if isinstance(ev, dict) and ev.get("event") == "result":
        result = ev.get("result")
        if isinstance(result, dict) and "usage" in result:
            usage = result.get("usage")
if usage is not None:
    print(json.dumps(usage, separators=(",", ":")))
PY
