#!/usr/bin/env bash
# Run one command with GitHub SSH URLs rewritten to HTTPS for that command's
# process tree only. Use it to launch a cursor-agent chair:
#   scripts/with-github-https.sh cursor-agent -p --force --trust ... "$(cat brief.md)"
# See scripts/github-https-env.sh for why.
set -eu
[ "$#" -gt 0 ] || { echo "usage: $0 COMMAND [ARGS...]" >&2; exit 2; }
# shellcheck disable=SC1091
. "$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)/github-https-env.sh"
# Chair marker for the process this wrapper execs. seat-usage --grade-findings
# refuses to grade when it sees CONCLAVE_CHAIR=1. CONCLAVE_CHAIR_MODEL is not
# a marker. Voter and Jev launches do not go through this wrapper.
export CONCLAVE_CHAIR=1
exec "$@"
