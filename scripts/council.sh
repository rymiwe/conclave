#!/usr/bin/env bash
# Deprecated alias for scripts/conclave.sh. Same arguments.
# An executed caller is replaced by conclave.sh, so the process image is
# conclave.sh. A sourced caller (COUNCIL_SOURCE_ONLY / CONCLAVE_SOURCE_ONLY
# tests) loads conclave.sh in this shell so its functions stay visible.
here="$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="$here/conclave.sh"
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  # shellcheck disable=SC1091
  . "$target"
else
  exec "$target" "$@"
fi
